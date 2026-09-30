# adk-setfit-router

A production-oriented banking intent router. A [SetFit](https://github.com/huggingface/setfit)
classifier trained on **BANKING77** picks the sub-agent for each request inside a Google ADK
supervisor. Three MCP-backed banking sub-agents (cards, transactions, accounts) talk to their
MCP servers over **SSE**, a FastAPI endpoint streams the run as Server-Sent Events, every routing
decision goes to a JSONL metrics log, and a benchmark compares SetFit with
`google/gemini-3.5-flash-lite` via OpenRouter on the same eval split.

```
                    POST /chat/stream (SSE)
                             │
                ┌────────────▼────────────┐
                │ ADK Workflow supervisor │
                │  START → setfit_route   │── routing_metrics.jsonl
                └──┬──────┬──────┬──────┬─┘
          cards_agent  transactions_agent  accounts_agent  fallback_agent
               │            │                  │
         McpToolset    McpToolset         McpToolset        (no tools)
          SSE :8101     SSE :8102          SSE :8103
               │            │                  │
       cards_server  transactions_server  accounts_server   (FastMCP, in-memory demo bank)
```

## Layout

| path | purpose |
|---|---|
| `training/prepare_data.py` | BANKING77 → `training/data/intents.csv` (+ `intents_eval.csv`); holds `INTENT_TO_AGENT` |
| `training/train.py` | SetFit training at `agent` (default) or `intent` granularity |
| `training/evaluate.py` | intent-level (77-way) + agent-level (3 + fallback) accuracy |
| `training/scoring.py` | shared eval split + roll-up scoring used by evaluate and the benchmark |
| `benchmark/run_benchmark.py` | SetFit vs OpenRouter LLM on the identical split |
| `benchmark/prompts.py` | label lists, per-intent descriptions, output parsing for the LLM |
| `setfit_router/` | router, ADK supervisor, FastAPI app, routing metrics |
| `mcp_servers/` | cards / transactions / accounts MCP servers (SSE) |
| `sample_data/` | tiny CSVs for smoke tests only |

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch   # CPU-only torch (optional)
.venv/bin/pip install -e ".[dev]"
```

Everything runs on CPU. `datasets` is pinned `<4` because `PolyAI/banking77` on the Hub is a
loading-script dataset and `datasets` 4.x removed script support.

## 1. Load BANKING77

BANKING77 (13,083 queries, 77 intents; 10,003 train / 3,080 test) is on Hugging Face, so
**no Kaggle account or token is needed**:

```python
import datasets

ds = datasets.load_dataset("PolyAI/banking77", trust_remote_code=True)
ds["train"].features["label"].names  # the 77 intent names
```

`prepare_data.py` does this and writes CSVs with columns `text`, `intent` (original 77-class
label) and `label` (the target at the chosen granularity):

```bash
# full train split, agent labels (cards / transactions / accounts / fallback)
.venv/bin/python training/prepare_data.py

# few-shot: 16 examples per intent, 77-way labels
.venv/bin/python training/prepare_data.py --few-shot 16 --granularity intent

# Kaggle / upstream CSV instead of the Hub (columns: text + category|intent|label)
.venv/bin/python training/prepare_data.py --source csv \
    --train-csv ~/Downloads/banking77/train.csv --test-csv ~/Downloads/banking77/test.csv
```

| flag | default | meaning |
|---|---|---|
| `--source hf\|csv` | `hf` | Hugging Face dataset or local CSV files |
| `--few-shot N` | all rows | keep at most N training examples per intent (seeded) |
| `--granularity intent\|agent` | `agent` | `label` column = 77-way intent or mapped agent |
| `--seed` | `42` | sampling seed |
| `--out` / `--eval-out` | `training/data/intents.csv` / `intents_eval.csv` | output paths |

The test split is always written in full to `intents_eval.csv`; it is the shared eval split.

## 2. Train

`train.py` derives targets from the `intent` column, so one `intents.csv` trains either
granularity. The production router is the agent-level model.

```bash
# production router: 3 agents + fallback  -> models/setfit-agent
.venv/bin/python training/train.py

# both granularities, few-shot for a quick CPU run
.venv/bin/python training/train.py --granularity both --few-shot 16 --num-iterations 10
```

The model directory contains the SetFit model plus `router_meta.json` (granularity, label order,
the `INTENT_TO_AGENT` snapshot used at training time). An intent-level model can also serve as
the router: its 77-way prediction is rolled up to an agent through that snapshot.

Defaults: base model `BAAI/bge-small-en-v1.5`, `--num-iterations 20`, `--num-epochs 1`,
`--batch-size 16`. On a 2-core CPU, `--few-shot 16 --num-iterations 10` takes roughly 20 min per
granularity.

## 3. Evaluate

```bash
.venv/bin/python training/evaluate.py                     # every models/setfit-* found
.venv/bin/python training/evaluate.py --model-dir models/setfit-intent --output benchmark/results/eval.json
```

Every model is scored at its own granularity and **rolled up to agents**. A 77-way prediction
of `card_delivery_estimate` for a true `card_arrival` is an intent error but not an agent error
(both are `cards`), and the report counts these separately as `intent_errors_within_agent`.
Reports include agent macro-F1, per-agent precision/recall, a 4×4 agent confusion matrix and
single-request latency.

## 4. Benchmark vs Gemini on OpenRouter

```bash
export OPENROUTER_API_KEY=sk-or-...
.venv/bin/python benchmark/run_benchmark.py --limit 500          # seeded stratified subsample
.venv/bin/python benchmark/run_benchmark.py --skip-llm            # SetFit only, no key needed
```

* SetFit models and the LLM see **the same rows** (`--eval-csv`, `--limit`, `--seed`); `--limit`
  draws a stratified-by-intent sample with largest-remainder allocation.
* The LLM (`--llm-model`, default `google/gemini-3.5-flash-lite`) runs at `intent`, `agent` or
  `both` granularities (`--llm-granularity`). The system prompt lists the valid labels for that
  granularity; for the 77-way prompt each intent has a short description
  (`benchmark/prompts.py::INTENT_DESCRIPTIONS`) because names like `verify_top_up` are terse.
  For the agent prompt each agent has a description.
* Output is parsed from `{"label": ...}` JSON, with a fallback to a bare label mentioned in the
  text. Unparseable answers count as errors (`invalid_predictions`).
* Results go to `benchmark/results/benchmark_<ts>.{json,md}` plus per-row
  `predictions_<ts>.csv`. Columns: intent accuracy, agent accuracy, agent macro-F1, within-agent
  misroutes, p50/p95 latency and OpenRouter-reported cost.

### Reference results (BANKING77 test split)

Raw outputs of these runs are committed under `benchmark/reports/` (one folder per run).

Full test split (3,080 rows, 77 intents). SetFit: `BAAI/bge-small-en-v1.5`, `--few-shot 16`
(1,232 training rows), `--num-iterations 10`, CPU-only (~22 min per granularity). MiniLM rows:
same settings with `--base-model sentence-transformers/all-MiniLM-L6-v2` (~10 min per granularity). LLM:
`google/gemini-3.5-flash-lite` via OpenRouter, concurrency 8.

| system | granularity | intent acc (77-way) | agent acc (3 + fallback) | agent macro-F1 | within-agent misroutes | p50 ms | p95 ms | cost USD |
|---|---|---|---|---|---|---|---|---|
| SetFit `setfit-agent` | agent | — | 97.14% | 0.963 | — | 10.0 | 12.5 | 0 |
| SetFit `setfit-intent` | intent | 84.22% | 97.37% | 0.964 | 405 | 9.9 | 17.6 | 0 |
| SetFit `setfit-minilm-agent` (MiniLM) | agent | — | 96.69% | 0.956 | — | 5.4 | 6.9 | 0 |
| SetFit `setfit-minilm-intent` (MiniLM) | intent | 82.92% | 97.14% | 0.962 | 438 | 5.4 | 9.3 | 0 |
| gemini-3.5-flash-lite | intent | 83.34% | 97.11% | 0.967 | 424 | 700.9 | 888.1 | 1.22 |
| gemini-3.5-flash-lite | agent | — | 90.03% | 0.870 | — | 688.5 | 910.4 | 0.27 |

About 400 of the 77-way errors stay inside the correct agent bucket, so for routing the 77-way
models are about as accurate as the agent model. The LLM does noticeably worse when asked for
agent names directly than when it picks an intent and the result is rolled up. The LLM gave 2
unparseable answers in the 77-way run. SetFit latency is single-request CPU inference.
`all-MiniLM-L6-v2` (6 layers) is about 1.8x faster than `bge-small-en-v1.5` (12 layers) and
trains in half the time, for 0.2–0.5 points less agent accuracy.

## 5. Run the router

```bash
.venv/bin/python -m mcp_servers.run_all          # cards :8101, transactions :8102, accounts :8103 (SSE)

OPENROUTER_API_KEY=sk-or-... .venv/bin/python -m setfit_router --port 8000

curl -N -X POST localhost:8000/chat/stream -H 'content-type: application/json' \
     -d '{"message": "I think someone stole my card", "user_id": "u1"}'
```

SSE event types: `session`, `route` (agent, confidence, predicted label), `tool_call`,
`tool_result`, `delta` (partial text), `message` (final text per agent), `error`, `done`. Pass the
returned `session_id` to continue a conversation. Other endpoints: `POST /route` (router only),
`GET /metrics`, `GET /health`.

| env var | default |
|---|---|
| `ROUTER_MODEL_DIR` | `models/setfit-agent` |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.35` (below this → `fallback`) |
| `AGENT_MODEL` | `openrouter/google/gemini-3.5-flash-lite` (`openrouter/...` runs through ADK's LiteLLM wrapper with `OPENROUTER_API_KEY`; any other ADK model string such as `gemini-2.5-flash` uses `GOOGLE_API_KEY`) |
| `CARDS_MCP_URL` / `TRANSACTIONS_MCP_URL` / `ACCOUNTS_MCP_URL` | `http://127.0.0.1:810{1,2,3}/sse` |
| `ROUTING_METRICS_LOG` | `logs/routing_metrics.jsonl` |
| `ROUTING_LOG_TEXT` | `false` (set `true` to log raw message text) |

### Routing metrics

Each routed request appends one JSON line: request/session/user ids, agent, predicted label,
confidence, low-confidence flag, router latency, end-to-end latency, status and message length.
`GET /metrics` returns live aggregates; summarize a log file offline with:

```bash
.venv/bin/python -m setfit_router.metrics logs/routing_metrics.jsonl
```

## Smoke tests vs real results

`sample_data/intents_sample.csv` (22 rows) and `intents_sample_eval.csv` (11 rows) exist only to
exercise the pipeline quickly. **They are not a benchmark**; real numbers come from BANKING77.

```bash
.venv/bin/python training/train.py --train-csv sample_data/intents_sample.csv \
    --num-iterations 2 --output-dir /tmp/smoke-agent
.venv/bin/python training/evaluate.py --model-dir /tmp/smoke-agent \
    --eval-csv sample_data/intents_sample_eval.csv
.venv/bin/pytest            # unit tests + FastAPI SSE test against a live cards MCP server
.venv/bin/ruff check .
```

## Intent → agent mapping

`INTENT_TO_AGENT` in `training/prepare_data.py` is the single source of truth: 23 `cards`,
39 `transactions`, 9 `accounts` and 6 `fallback` intents. The `fallback` intents are
general FX / availability questions that no sub-agent owns.

| agent | domain | BANKING77 intent | description |
|---|---|---|---|
| `cards` | card lifecycle, delivery and ordering | `activate_my_card` | how to activate a newly received card |
| `cards` | card lifecycle, delivery and ordering | `card_about_to_expire` | card expiring soon, getting a renewal |
| `cards` | card lifecycle, delivery and ordering | `card_arrival` | ordered card has not arrived yet |
| `cards` | card lifecycle, delivery and ordering | `card_delivery_estimate` | how long card delivery takes |
| `cards` | card lifecycle, delivery and ordering | `card_linking` | linking an existing card to the app/account |
| `cards` | card lifecycle, delivery and ordering | `get_physical_card` | getting a physical card (e.g. PIN for new physical card) |
| `cards` | card lifecycle, delivery and ordering | `getting_spare_card` | ordering an additional or spare card |
| `cards` | card lifecycle, delivery and ordering | `order_physical_card` | ordering a physical card |
| `cards` | virtual & disposable cards | `disposable_card_limits` | limits on disposable virtual cards |
| `cards` | virtual & disposable cards | `get_disposable_virtual_card` | how to get a disposable virtual card |
| `cards` | virtual & disposable cards | `getting_virtual_card` | how to get a virtual card |
| `cards` | virtual & disposable cards | `virtual_card_not_working` | virtual card not working |
| `cards` | security, PIN and malfunction | `card_not_working` | card not working in general |
| `cards` | security, PIN and malfunction | `card_swallowed` | ATM kept / swallowed the card |
| `cards` | security, PIN and malfunction | `change_pin` | how to change the card PIN |
| `cards` | security, PIN and malfunction | `compromised_card` | card details possibly compromised or used fraudulently |
| `cards` | security, PIN and malfunction | `contactless_not_working` | contactless payments not working |
| `cards` | security, PIN and malfunction | `lost_or_stolen_card` | card lost or stolen |
| `cards` | security, PIN and malfunction | `pin_blocked` | PIN blocked after wrong attempts |
| `cards` | networks, wallets and acceptance | `apple_pay_or_google_pay` | adding the card to Apple Pay or Google Pay |
| `cards` | networks, wallets and acceptance | `card_acceptance` | where the card is accepted by merchants |
| `cards` | networks, wallets and acceptance | `supported_cards_and_currencies` | which cards and currencies are supported for top-up/payments |
| `cards` | networks, wallets and acceptance | `visa_or_mastercard` | whether the card is Visa or Mastercard |
| `transactions` | card payments | `card_payment_fee_charged` | unexpected fee charged on a card payment |
| `transactions` | card payments | `card_payment_not_recognised` | card payment the customer does not recognise |
| `transactions` | card payments | `card_payment_wrong_exchange_rate` | wrong exchange rate applied to a card payment |
| `transactions` | card payments | `declined_card_payment` | card payment was declined |
| `transactions` | card payments | `direct_debit_payment_not_recognised` | unrecognised direct debit |
| `transactions` | card payments | `extra_charge_on_statement` | unexplained extra charge on the statement |
| `transactions` | card payments | `pending_card_payment` | card payment still pending |
| `transactions` | card payments | `reverted_card_payment?` | card payment was reverted |
| `transactions` | card payments | `transaction_charged_twice` | same transaction charged twice |
| `transactions` | cash withdrawals | `cash_withdrawal_charge` | fee charged for a cash withdrawal |
| `transactions` | cash withdrawals | `cash_withdrawal_not_recognised` | cash withdrawal the customer did not make |
| `transactions` | cash withdrawals | `declined_cash_withdrawal` | cash withdrawal was declined |
| `transactions` | cash withdrawals | `pending_cash_withdrawal` | cash withdrawal still pending |
| `transactions` | cash withdrawals | `wrong_amount_of_cash_received` | ATM dispensed the wrong amount of cash |
| `transactions` | cash withdrawals | `wrong_exchange_rate_for_cash_withdrawal` | wrong exchange rate on a cash withdrawal |
| `transactions` | transfers | `balance_not_updated_after_bank_transfer` | balance not updated after a bank transfer in |
| `transactions` | transfers | `beneficiary_not_allowed` | unable to add or pay a beneficiary / payee |
| `transactions` | transfers | `cancel_transfer` | cancel or reverse a transfer that was just made |
| `transactions` | transfers | `declined_transfer` | transfer was declined |
| `transactions` | transfers | `failed_transfer` | transfer failed |
| `transactions` | transfers | `pending_transfer` | transfer still pending |
| `transactions` | transfers | `receiving_money` | how to receive money / incoming payments |
| `transactions` | transfers | `transfer_fee_charged` | fee charged on a transfer |
| `transactions` | transfers | `transfer_into_account` | how to transfer money into the account |
| `transactions` | transfers | `transfer_not_received_by_recipient` | recipient has not received a transfer |
| `transactions` | transfers | `transfer_timing` | how long transfers take |
| `transactions` | refunds | `Refund_not_showing_up` | merchant refund not showing up |
| `transactions` | refunds | `request_refund` | asking for a refund of a purchase |
| `transactions` | top-ups | `automatic_top_up` | setting up or using auto top-up |
| `transactions` | top-ups | `balance_not_updated_after_cheque_or_cash_deposit` | balance not updated after a cheque or cash deposit |
| `transactions` | top-ups | `pending_top_up` | top-up still pending |
| `transactions` | top-ups | `top_up_by_bank_transfer_charge` | fee for topping up by bank transfer |
| `transactions` | top-ups | `top_up_by_card_charge` | fee for topping up by card |
| `transactions` | top-ups | `top_up_by_cash_or_cheque` | topping up with cash or a cheque |
| `transactions` | top-ups | `top_up_failed` | top-up failed |
| `transactions` | top-ups | `top_up_limits` | limits on top-up amounts |
| `transactions` | top-ups | `top_up_reverted` | top-up was reverted |
| `transactions` | top-ups | `topping_up_by_card` | how to top up using a card / problems topping up by card |
| `transactions` | top-ups | `verify_top_up` | verifying a top-up (e.g. card verification code) |
| `accounts` | identity verification & compliance | `unable_to_verify_identity` | identity verification failing |
| `accounts` | identity verification & compliance | `verify_my_identity` | how to verify identity |
| `accounts` | identity verification & compliance | `verify_source_of_funds` | verifying source of funds |
| `accounts` | identity verification & compliance | `why_verify_identity` | why identity verification is required |
| `accounts` | profile, access and lifecycle | `age_limit` | minimum age requirements to open or use an account |
| `accounts` | profile, access and lifecycle | `edit_personal_details` | change name, address or other personal details |
| `accounts` | profile, access and lifecycle | `lost_or_stolen_phone` | phone with the app lost or stolen |
| `accounts` | profile, access and lifecycle | `passcode_forgotten` | forgot the app passcode |
| `accounts` | profile, access and lifecycle | `terminate_account` | close or delete the account |
| `fallback` | general FX / product questions with no owning sub-agent | `atm_support` | which ATMs can be used / ATM availability |
| `fallback` | general FX / product questions with no owning sub-agent | `country_support` | which countries the service is available in |
| `fallback` | general FX / product questions with no owning sub-agent | `exchange_charge` | fees for currency exchange |
| `fallback` | general FX / product questions with no owning sub-agent | `exchange_rate` | what exchange rate is used / current rates |
| `fallback` | general FX / product questions with no owning sub-agent | `exchange_via_app` | how to exchange currencies in the app |
| `fallback` | general FX / product questions with no owning sub-agent | `fiat_currency_support` | which fiat currencies can be held or exchanged |

## Remapping when you add a sub-agent

Example: a new `fx` agent that owns currency exchange.

1. **Mapping**: in `training/prepare_data.py` add `"fx"` to `AGENTS` and move intents such as
   `exchange_rate`, `exchange_charge`, `exchange_via_app`, `fiat_currency_support`,
   `card_payment_wrong_exchange_rate` and `wrong_exchange_rate_for_cash_withdrawal` to `"fx"`.
   `tests/test_prepare_data.py` checks that all 77 intents are still mapped to known agents.
2. **Prompts**: add an `"fx"` entry to `AGENT_DESCRIPTIONS` in `benchmark/prompts.py`.
3. **Agent + MCP server**: add an `mcp_servers/fx_server.py` (copy an existing one and add its
   port to `DEFAULT_PORTS`), add `"fx"` to `SUB_AGENT_SPECS` in `setfit_router/agents.py` and
   an `FX_MCP_URL` entry to `Settings.mcp_urls`.
4. **Retrain**: agent-level models must be retrained (`training/train.py`) because the label set
   changed. An intent-level model does not need retraining: its predictions are rolled up
   through the `intent_to_agent` stored in `router_meta.json`, so update that file or retrain to
   pick up the new mapping.
5. **Re-benchmark** with `benchmark/run_benchmark.py` so agent-level numbers reflect the new buckets.
