# Local setup (Windows 11) — BERT intent router + financial supervisor

This guide walks through running the whole stack on a Windows 11 machine:
preparing the Banking77-based dataset, fine-tuning the BERT intent classifier,
and running the ADK route-graph supervisor that dispatches to the eight
financial sub-agents.

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | https://www.python.org/downloads/windows/ — tick *Add python.exe to PATH*. Check with `python --version`. |
| Git | https://git-scm.com/download/win |
| (Optional) NVIDIA GPU + driver | Any recent Game Ready / Studio driver includes the CUDA runtime needed by the PyTorch wheels. Check with `nvidia-smi`. No CUDA Toolkit install is needed. |
| RAM | 24 GB is plenty. CPU training uses ~4–6 GB; GPU training needs ~4 GB VRAM at batch size 16. |
| (Optional) Redis | Only needed if you don't use `USE_FAKEREDIS=true`. Easiest: `docker run -d -p 6379:6379 redis:7-alpine` or [Memurai](https://www.memurai.com/). |
| (Optional) Java 17 + Maven | Only if you want the Spring Boot MCP server in `mcp-server/`. |

All commands below are for **PowerShell**.

## 2. Clone and create a virtual environment

```powershell
git clone https://github.com/chichasonu/a2a-adk-2.0.git
cd a2a-adk-2.0
git checkout bert-intent-router

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell refuses to run the activation script:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 3. Install dependencies

### 3a. PyTorch (pick one)

`pip install -e .` pulls the default PyPI `torch` wheel, which on Windows is
**CPU-only**. If you have an NVIDIA GPU, install the CUDA build *first* so pip
keeps it:

```powershell
# GPU (CUDA 12.x)
pip install torch --index-url https://download.pytorch.org/whl/cu124

# CPU only (smaller download)
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Verify: `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`.

### 3b. The project

```powershell
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```powershell
uv venv .venv ; .\.venv\Scripts\Activate.ps1
uv pip install torch --index-url https://download.pytorch.org/whl/cu124   # or /cpu
uv pip install -e ".[dev]"
```

This installs `google-adk`, `litellm`, `transformers`, `datasets` and friends.

## 4. Environment variables

Copy `.env.example` to `.env` and fill in:

```dotenv
# Required — used by the Gemini route-graph and LiteLLM (gemini/ provider)
GOOGLE_API_KEY=your_google_ai_studio_key

# Models
GEMINI_MODEL=gemini-2.0-flash              # team/legacy agents
GEMINI_FLASH_LITE_MODEL=gemini-2.5-flash-lite   # 8 financial sub-agents via LiteLlm

# Optional: route sub-agents through an OpenAI-compatible proxy instead of
# LiteLLM's native gemini/ provider (e.g. a LiteLLM proxy or Vertex OpenAI endpoint).
# When OPENAI_API_BASE is set the sub-agents use model "openai/<GEMINI_FLASH_LITE_MODEL>".
# OPENAI_API_BASE=http://localhost:4000/v1
# OPENAI_API_KEY=sk-...

# BERT router
INTENT_MODEL_PATH=models/intent_classifier
INTENT_CONFIDENCE_THRESHOLD=0.5

# Sessions / tools
USE_FAKEREDIS=true                          # or REDIS_URL=redis://localhost:6379/0
MCP_ENABLED=false                           # true + MCP_SERVER_URL when the Spring server runs
MCP_SERVER_URL=http://localhost:8080/mcp
A2A_BASE_URL=http://localhost:8000
LOG_LEVEL=INFO
```

`GEMINI_API_KEY` is derived from `GOOGLE_API_KEY` automatically for LiteLLM.

## 5. Prepare the dataset

```powershell
python scripts\prepare_banking77.py
```

* Downloads `PolyAI/banking77` (~13k utterances) from the Hugging Face Hub
  (first run only; cached under `%USERPROFILE%\.cache\huggingface`).
* Collapses the 77 intents into the 8 agent labels using
  `data\intent_mapping.json`.
* Merges `data\financial_intents_seed.jsonl` (the hand-written seed set — the
  only source of `spending_insights_agent` and `fallback_agent` examples, so
  add more rows there if you want those classes to be stronger).
* Writes a stratified 80/20 split to `data\train.jsonl` / `data\test.jsonl`.

Offline smoke test: `python scripts\prepare_banking77.py --skip-banking77`.

## 6. Train the classifier

```powershell
# GPU (auto-detected). ~1–2 min/epoch on an RTX 3060-class card.
python scripts\train_intent_classifier.py --epochs 3 --batch-size 16 --fp16

# CPU only. On a modern 8-core laptop expect ~25–40 min/epoch for the full
# ~13k-row set; 2 epochs is usually enough for >95% accuracy.
$env:INTENT_TRAIN_DEVICE = "cpu"
python scripts\train_intent_classifier.py --epochs 2 --batch-size 32 --max-length 48
```

Notes for a 24 GB RAM machine:

* CPU training peaks at ~5 GB RAM, so it fits comfortably; close other heavy
  apps to keep all cores free. `--batch-size 32 --max-length 48` is a good
  CPU trade-off (Banking77 utterances are short).
* For a quick sanity run: `--max-train-samples 500 --epochs 1`.
* Device can also be forced with `--device cpu|cuda`.

The script prints per-class precision/recall/F1 and calls out
`transfer_to_human_agent` recall (the safety-critical class), then saves the
model, tokenizer, `label_map.json` and `eval_report.json` to
`models\intent_classifier\`. The `models/` directory is git-ignored.

## 7. Run the server

```powershell
# Console script
a2a-adk

# or
python -m a2a_adk --host 0.0.0.0 --port 8000

# or
uvicorn a2a_adk.main:app --host 0.0.0.0 --port 8000
```

On startup `build_runner(agent_type="graph")` loads the classifier once
(look for `Loaded intent classifier from models/intent_classifier on cuda/cpu`).
If the directory is missing you will see
`Intent classifier unavailable; using keyword routing` — the graph still works
using the keyword rules in `a2a_adk/agents.py`.

Optional — Spring Boot MCP tools: `cd mcp-server; mvn spring-boot:run`, then set
`MCP_ENABLED=true`. Sub-agents pick up tools by name (e.g. `getBalance`,
`getTransactions`, `getExchangeRate`) through `mcp_tool_cache.get_tools()`.

## 8. Verify routing end-to-end

Health check:

```powershell
curl http://localhost:8000/health
```

Classifier alone (no LLM call):

```powershell
python -c "from a2a_adk.intent_classifier import classify; print(classify('I lost my card and someone is using it'))"
# -> ('transfer_to_human_agent', 0.97)
```

Full graph (router → sub-agent → Gemini flash-lite):

```powershell
curl -X POST http://localhost:8000/run/graph -H "Content-Type: application/json" `
  -d '{\"user_id\":\"u1\",\"message\":\"How much did I spend on groceries last month?\"}'
```

In the server log you should see
`Graph router (bert) classified input as route=spending_insights_agent confidence=0.9x`
followed by the sub-agent's response. Try one message per label:

| Message | Expected route |
|---|---|
| "Freeze my card please" | `card_management_agent` |
| "Change my phone number" | `account_management_agent` |
| "I want to talk to a human" | `transfer_to_human_agent` |
| "Which countries do you support?" | `answer_hub_agent` |
| "My transfer hasn't arrived" | `transaction_agent` |
| "What's the fee for withdrawing cash abroad?" | `rates_fees_limits_management_agent` |
| "Show my spending by category" | `spending_insights_agent` |
| "Tell me a joke" | `fallback_agent` |

Lower `INTENT_CONFIDENCE_THRESHOLD` if too many messages fall back; raise it if
mis-routes appear.

Run the unit tests with `pytest`.

## Troubleshooting

* **`ImportError: LiteLLM support requires: pip install google-adk[extensions]`** —
  `pip install litellm` (it is in `pyproject.toml`; re-run `pip install -e .`).
* **`torch.cuda.is_available()` is False** — you installed the CPU wheel;
  reinstall with the `cu124` index URL above.
* **Long path errors on Windows** — enable long paths:
  `git config --system core.longpaths true` and the *Enable Win32 long paths*
  group policy.
* **Hugging Face download blocked** — set `HF_ENDPOINT` to your mirror or copy
  the dataset cache from another machine.
