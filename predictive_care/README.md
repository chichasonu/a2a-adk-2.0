# Predictive Card Care — long-term-memory agentic POC

Proves the concept of **predictive service issue resolution**: the app watches
what the customer does, predicts the card problem *before* they ask, and an
agent with long-term memory offers to fix it.

Demo storyline (the one in the brief):

1. Customer searches "debit card blocked".
2. Customer opens the **Card management** page repeatedly.
3. The app pops up: *"Having trouble with your card? I can help replace,
   activate, dispute or unlock it."*
4. The customer picks an action (or chats); the agent performs it and writes the
   outcome to long-term memory.
5. On the next visit the same friction is detected faster and the offer is
   re-ordered around what the customer chose last time.

## Architecture

| Layer | Module | Role |
| --- | --- | --- |
| Behavioural signals | `signals.py` | Searches, page views, declines and IVR calls in a rolling Redis window; aggregated into predictor features |
| Long-term memory | `memory.py`, `mongo_memory.py` | ADK 2.0 `BaseMemoryService` over **MongoDB** (`memories` + `profiles` collections) or Redis: durable facts, resolutions, offer feedback, plus a structured profile; lexical (IDF + recency) retrieval, no vector DB needed |
| Short-term memory | `session_store.py` | ADK `BaseSessionService` over Redis for the conversation itself |
| Prediction | `predictor.py` | Explainable friction score = in-session cues + long-term memory, issue taxonomy, action ranking, cooldown / "previously dismissed" suppression |
| Resolution | `cards.py`, `tools.py` | Mock card system of record and the four agent tools: `replace_card`, `activate_card`, `dispute_transaction`, `unlock_card` |
| Agent | `agent.py` | ADK 2.0 `LlmAgent` (Gemini) with the resolution tools plus `preload_memory` / `load_memory` |
| Orchestration | `service.py` | Signal ingestion → prediction → intervention → resolution → memory write-back |
| API + UI | `main.py`, `ui/` | FastAPI endpoints and a mock banking front end with an "agent brain" inspector |

Every prediction carries the reasons that produced it (with weights and whether
they came from signals or memory), so the POC can show *why* the prompt fired.

The prompt is deliberately held back (`suppressed_by`) when the customer is not
in a card context, while an intervention cooldown is active, or once the
customer has dismissed the offer — after a dismissal only a hard new signal (a
declined payment or a call to the card IVR) may re-open it, regardless of how
high the score climbs.

## Run it (Docker Redis + MongoDB)

Redis holds behavioural signals, card state and conversation sessions; MongoDB
holds long-term memory. Start both from the bundled compose file:

```bash
docker compose -f predictive_care/docker-compose.yml up -d   # redis:6379, mongo:27017
pip install -e .
CARE_MEMORY_BACKEND=mongo predictive-care --port 8100
```

Windows PowerShell:

```powershell
docker compose -f predictive_care\docker-compose.yml up -d
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\pip install -e .
$env:REDIS_URL="redis://localhost:6379/0"
$env:CARE_MEMORY_BACKEND="mongo"
$env:MONGO_URL="mongodb://localhost:27017"
$env:MONGO_DB="predictive_care"
$env:CARE_FORCE_RULES="true"
.\.venv\Scripts\python -m predictive_care --port 8100
```

Open <http://localhost:8100>, search for "debit card blocked", then click
**Card management** twice. No API key is needed: without `GOOGLE_API_KEY` the
agent answers with a deterministic rule engine instead of Gemini, and
`CARE_FORCE_RULES=true` forces that path even when a key is present.

Inspect what was persisted:

```bash
docker exec -it predictive-care-mongo-1 mongosh predictive_care \
  --eval 'db.memories.find().limit(5); db.profiles.find()'
docker exec -it predictive-care-redis-1 redis-cli keys 'care:*'
```

`CARE_MEMORY_BACKEND=redis` keeps long-term memory in Redis instead (no Mongo
needed). `USE_FAKEREDIS=true` swaps Redis for an embedded fake — tests only, not
the runtime default.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GOOGLE_API_KEY` | *(unset)* | Enables the Gemini-backed ADK agent |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Model for the care agent |
| `USE_FAKEREDIS` | `false` | Embedded in-memory Redis (tests/offline only) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection (signals, card state, sessions) |
| `CARE_MEMORY_BACKEND` | `redis` | Long-term memory backend: `mongo` or `redis` |
| `MONGO_URL` | `mongodb://localhost:27017` | MongoDB connection for long-term memory |
| `MONGO_DB` | `predictive_care` | MongoDB database name |
| `CARE_PORT` | `8100` | Default HTTP port |
| `CARE_SIGNAL_WINDOW_SECONDS` | `900` | Rolling window scored by the predictor |
| `CARE_REPEAT_VIEW_THRESHOLD` | `2` | Card page opens counted as "repeatedly" |
| `CARE_INTERVENE_THRESHOLD` | `0.6` | Confidence needed to pop the prompt |
| `CARE_INTERVENTION_COOLDOWN_SECONDS` | `60` | Minimum gap between prompts |
| `CARE_MEMORY_TTL_SECONDS` | `0` | `0` = long-term memory never expires (Redis key TTL / Mongo TTL index) |

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/signals` | Ingest a behavioural signal, returns the refreshed prediction |
| `GET /api/prediction/{user_id}` | Re-score friction without emitting a signal |
| `GET /api/signals/{user_id}` | Signal timeline + aggregated features |
| `POST /api/chat` | Talk to the care agent (ADK agent or rule engine) |
| `POST /api/actions` | Execute `replace` / `activate` / `dispute` / `unlock` |
| `POST /api/offer-response` | Record acceptance or dismissal of the prompt |
| `GET /api/memory/{user_id}` | Inspect long-term memory (profile + records) |
| `GET /api/memory/{user_id}/search?q=` | Lexical memory search |
| `GET /api/cards/{user_id}` | Card state shown on the card management page |
| `POST /api/reset` | Reset the demo (`forget_memory=false` keeps memory) |

Scripted version of the demo:

```bash
U=cust_1001
curl -s -XPOST localhost:8100/api/signals -H 'content-type: application/json' \
  -d "{\"user_id\":\"$U\",\"type\":\"search\",\"value\":\"debit card blocked\"}"
for i in 1 2; do curl -s -XPOST localhost:8100/api/signals -H 'content-type: application/json' \
  -d "{\"user_id\":\"$U\",\"type\":\"page_view\",\"value\":\"card_management\"}"; done
curl -s -XPOST localhost:8100/api/actions -H 'content-type: application/json' \
  -d "{\"user_id\":\"$U\",\"action\":\"unlock\"}"
curl -s localhost:8100/api/memory/$U
```

## Tests

```bash
pytest tests/test_predictive_care.py
```

The suite pins `USE_FAKEREDIS=true` and the rule engine itself, so it needs no
Redis. The MongoDB backend tests run against `MONGO_URL` when it is reachable
(`docker compose -f predictive_care/docker-compose.yml up -d mongo`) and skip
otherwise.
