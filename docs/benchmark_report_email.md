**Subject:** BANKING77 intent router: SetFit (bge-small, MiniLM) vs Gemini 3.5 Flash Lite benchmark results

Hi all,

Below are the results of the banking intent-router work. The code is on the `adk-setfit-router`
branch of `chichasonu/a2a-adk-2.0`:
https://github.com/chichasonu/a2a-adk-2.0/tree/adk-setfit-router

## TL;DR

- A small SetFit classifier (33M-parameter embedding model, CPU only, 16 training examples per
  intent) routes customer messages to the right banking agent **97.1–97.4%** of the time.
- `google/gemini-3.5-flash-lite` (via OpenRouter) reaches the same routing accuracy (**97.1%**)
  only when it picks one of the 77 intents first. When asked for the agent name directly it
  drops to **90.0%**.
- SetFit is about **70x faster** (p50 ~10 ms vs ~700 ms per request) and costs nothing per
  request. The Gemini 77-way run over the 3,080-row test set cost **$1.22**.
- Swapping the SetFit base model to `sentence-transformers/all-MiniLM-L6-v2` halves latency
  again (p50 **5.4 ms**, ~130x faster than Gemini) and still routes **96.7–97.1%** correctly,
  i.e. on par with Gemini's best setup.

## 1. What has been done

We built a production-oriented banking assistant. A trained classifier decides which specialist
agent handles each customer message:

1. **Data preparation** (`training/prepare_data.py`): loads BANKING77 from Hugging Face (or a
   Kaggle CSV) and writes `text`, `intent` (original 77-class label) and `label` (mapped agent)
   columns. A declarative `INTENT_TO_AGENT` table maps all 77 intents to 4 agent labels:
   `cards` (23 intents), `transactions` (39), `accounts` (9) and `fallback` (6: FX, ATM and
   country/currency support). It supports `--few-shot N` and `--granularity intent|agent`.
2. **Training** (`training/train.py`): SetFit models at two granularities.
   - *Agent model*: learns the 4 agent labels directly (the production default).
   - *Intent model*: learns all 77 intents. The router maps each predicted intent to its agent.
3. **Evaluation and benchmark** (`training/evaluate.py`, `benchmark/run_benchmark.py`): every
   system is scored at 77-way intent accuracy (where applicable) and rolled up to 4-way agent
   accuracy. A wrong intent that still reaches the correct agent is not a routing error. SetFit
   and the LLM are scored on exactly the same rows.
4. **Agent runtime** (`setfit_router/`, `mcp_servers/`):
   - A Google ADK workflow supervisor runs the SetFit route node, then one of four sub-agents.
   - The three specialist sub-agents (cards, transactions, accounts) each call tools on their
     own MCP server over SSE. The servers run on an in-memory demo bank. The fallback agent has
     no tools.
   - FastAPI streaming endpoint `POST /chat/stream` (Server-Sent Events), plus `/route`,
     `/metrics` and `/health`.
   - Every routing decision is written to a JSONL metrics log (agent, confidence, latency,
     status; raw text is omitted by default).
5. **Documentation** (`README.md`): loading BANKING77, the full intent-to-agent table, how to
   remap intents when a new sub-agent is added, and run instructions.

## 2. Models used

| role | model | notes |
|---|---|---|
| Router (SetFit) | `BAAI/bge-small-en-v1.5` sentence-embedding model + logistic-regression head | SetFit 1.2.0, fine-tuned with CoSENT contrastive loss; 12 layers, ~33M params (production default) |
| Router (SetFit, alternative) | `sentence-transformers/all-MiniLM-L6-v2` + logistic-regression head | same SetFit recipe; 6 layers, ~22M params |
| LLM baseline | `google/gemini-3.5-flash-lite` via OpenRouter | temperature 0, max 64 output tokens, JSON label output |
| Sub-agent reasoning (chat) | `openrouter/google/gemini-3.5-flash-lite` via ADK's LiteLLM wrapper | set with `AGENT_MODEL`; not part of the routing benchmark |

SetFit training settings (identical for both granularities and both base models): 16 examples per intent
(1,232 rows), `num_iterations=10`, 1 epoch, batch size 16, seed 42.

## 3. System configuration

| item | value |
|---|---|
| Machine | Intel Xeon Platinum 8559C, 2 vCPUs, ~8 GB RAM, **no GPU** |
| OS | Ubuntu 22.04.5 LTS |
| Python | 3.12.13 |
| Key libraries | torch 2.14.0+cpu, setfit 1.2.0, sentence-transformers 6.1.0, transformers 5.17.0, datasets 3.6.0, google-adk 2.10.0, mcp 1.30.0, litellm 1.102.0, fastapi 0.141.1, scikit-learn 1.9.1, pandas 3.0.6 |
| Training time (CPU) | bge-small: agent 21.4 min, intent 22.8 min · MiniLM: agent 10.4 min, intent 9.9 min |
| LLM calls | OpenRouter chat completions API, 8 concurrent requests |

SetFit latency is measured per request (batch size 1) on this 2-vCPU CPU machine. LLM latency
is the round-trip time to OpenRouter's API.

## 4. Dataset

- **BANKING77** (`PolyAI/banking77` on Hugging Face): 13,083 real customer banking queries
  labelled with 77 fine-grained intents. Loaded with
  `datasets.load_dataset("PolyAI/banking77")`, so no Kaggle token is needed.
- **Training data**: 16 examples per intent (1,232 rows), sampled with a fixed seed from the
  official train split (10,003 rows).
- **Test data**: the full official test split, **3,080 rows** (40 per intent). This split was
  never used in training. Agent mix: transactions 1,560 · cards 920 · accounts 360 · fallback 240.
- `sample_data/` holds a tiny 22-row / 11-row CSV used only for smoke tests. None of the numbers
  below come from it.

## 5. Benchmark results (BANKING77 test split, 3,080 rows)

| system | predicts | intent accuracy (77-way) | agent accuracy (4-way) | agent macro-F1 | p50 latency | p95 latency | cost |
|---|---|---|---|---|---|---|---|
| SetFit agent model | 4 agents | — | **97.14%** | 0.963 | 10.0 ms | 12.5 ms | $0 |
| SetFit intent model | 77 intents | **84.22%** | **97.37%** | 0.964 | 9.9 ms | 17.6 ms | $0 |
| SetFit MiniLM agent model | 4 agents | — | 96.69% | 0.956 | **5.4 ms** | **6.9 ms** | $0 |
| SetFit MiniLM intent model | 77 intents | 82.92% | 97.14% | 0.962 | **5.4 ms** | 9.3 ms | $0 |
| Gemini 3.5 Flash Lite | 77 intents | 83.34% | 97.11% | 0.967 | 701 ms | 888 ms | $1.22 |
| Gemini 3.5 Flash Lite | 4 agents | — | 90.03% | 0.870 | 689 ms | 910 ms | $0.27 |

Per-agent F1:

| system | cards | transactions | accounts | fallback |
|---|---|---|---|---|
| SetFit agent model | 0.968 | 0.980 | 0.967 | 0.935 |
| SetFit intent model | 0.968 | 0.984 | 0.973 | 0.931 |
| SetFit MiniLM agent model | 0.960 | 0.978 | 0.966 | 0.919 |
| SetFit MiniLM intent model | 0.964 | 0.982 | 0.977 | 0.926 |
| Gemini, 77 intents | 0.969 | 0.975 | 0.993 | 0.930 |
| Gemini, 4 agents | 0.902 | 0.931 | 0.958 | 0.690 |

Observations:

- **Routing accuracy is on par.** SetFit (97.1–97.4%) matches the best Gemini setup (97.1%) at
  about 1/70th of the latency and no per-request cost.
- **MiniLM vs bge-small.** `all-MiniLM-L6-v2` is ~1.8x faster (p50 5.4 ms vs ~10 ms) and
  trains in half the time, but is slightly less accurate: 96.69% vs 97.14% (agent model) and
  97.14% vs 97.37% (intent model); 82.92% vs 84.22% exact intent. The MiniLM intent model ties
  Gemini's best routing accuracy (97.14% vs 97.11%) at ~1/130th of the latency. It had 102
  (agent model) and 88 (intent model) agent misroutes vs 88 and 81 for bge-small.
- **Wrong intents mostly still reach the right agent.** SetFit got 486 intents wrong but only
  81 reached the wrong agent. For Gemini it was 513 and 89. That's why agent accuracy is much
  higher than intent accuracy.
- **Gemini is sensitive to the label set.** Asked to name the agent directly, it routes much
  worse (90.0%). It over-predicts `fallback` (precision 0.54), so a richer label set with
  descriptions helps it.
- Gemini returned 2 unparseable answers out of 3,080 in the 77-way run (counted as errors). There
  were no request failures.
- Token usage: the 77-way prompt includes a description for every intent (3.77M prompt tokens
  across the run). The 4-way prompt is much smaller (0.61M).

## 6. What was tested and how

| area | how | result |
|---|---|---|
| Unit and integration tests | `pytest`, 21 tests: complete 77-intent mapping and label order matching Hugging Face, CSV output columns, few-shot determinism, Kaggle CSV input, roll-up scoring (a wrong intent within the right agent is not a routing error), stratified eval sampling, LLM prompt contents and answer parsing, metrics log, OpenRouter model wiring | 21 passed |
| Streaming app with real MCP over SSE | Test suite starts a real cards MCP server and drives `/chat/stream` with a scripted LLM: routing, tool call and result over SSE, fallback routing, session reuse, missing-session error | passed |
| Lint / format | `ruff check`, `ruff format --check` | clean |
| Full benchmark | Section 5, run on the complete test split. All four SetFit models were re-scored in one run on the same machine (bge-small latency matched the original run within 0.5 ms p50) | as reported |
| Live end-to-end chat | All 3 MCP servers + FastAPI app + Gemini sub-agents via OpenRouter, driven with `curl` against `/chat/stream` | see below |

Live chat checks:

- "My card was stolen, freeze card-001 now": routed to cards, which called `freeze_card`
  and confirmed.
- "Was I charged twice at the coffee shop?": routed to transactions, which called
  `find_duplicate_charges` and `get_transaction` and found the duplicate £3.80 charge.
- "What is the exchange rate for euros?": routed to fallback, which gave a general answer
  with no tool calls.
- **Known issue:** "What's my account balance?" was routed to transactions instead of accounts.
  BANKING77's only balance-related intents (`balance_not_updated_after_*`) map to
  transactions, so the model links "balance" with transactions. Fix: add a handful of
  balance-enquiry examples labelled `accounts` and retrain.

## 7. Limitations and next steps

- Results use few-shot training (16 per intent). Training on the full 10,003-row train split
  should improve SetFit further; this has not been measured yet.
- The MCP servers use an in-memory demo bank. They need to be connected to real banking
  backends.
- Only one LLM was benchmarked. Other models can be compared with
  `benchmark/run_benchmark.py --llm-model <openrouter model id>` on the same split.
- The session store is in memory. A persistent session store (e.g. Redis) is needed for
  multi-worker deployment.

## How to reproduce

```bash
.venv/bin/python training/prepare_data.py                        # BANKING77 -> training/data/*.csv
.venv/bin/python training/train.py --granularity both --few-shot 16 --num-iterations 10
for g in agent intent; do                                         # MiniLM variant
  .venv/bin/python training/train.py --granularity $g --few-shot 16 --num-iterations 10 \
    --base-model sentence-transformers/all-MiniLM-L6-v2 --output-dir models/setfit-minilm-$g
done
.venv/bin/python training/evaluate.py
OPENROUTER_API_KEY=... .venv/bin/python benchmark/run_benchmark.py --llm-granularity both --concurrency 8
.venv/bin/python benchmark/run_benchmark.py --skip-llm --setfit-model models/setfit-minilm-agent \
  --setfit-model models/setfit-minilm-intent                    # MiniLM rows, same split
```

Thanks,
