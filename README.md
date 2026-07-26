# A2A ADK 2.0 Agent

A reference Google ADK 2.0 agent demonstrating:

- **Team agents**: a root coordinator that delegates to specialized sub-agents via `sub_agents` and `transfer_to_agent`.
- **Route graphs**: a `Workflow` with a conditional router `FunctionNode` that dispatches to the right specialist.
- **Redis SessionService**: durable session memory shared across processes/workers.
- **Runner + AgentExecutor integration**: ADK `Runner` wired to Redis, plus A2A `AgentExecutor` for standard A2A JSON-RPC/SSE serving.
- **Callback plugin**: a `BasePlugin` that captures before/after tool and model callbacks and persists them to Redis.
- **Event & context streaming**: ADK events and context snapshots are written to Redis streams/hashes.
- **Generic HTTP invoke endpoint**: invoke the team or graph agent with `POST /invoke/{team|graph}` and optional SSE streaming.
- **Gemini API key**: uses `GOOGLE_API_KEY` for Gemini models.

## Running locally

1. **Prerequisites**

   - Python 3.10+ and `pip`
   - A Gemini API key (set as `GOOGLE_API_KEY`)
   - Docker (or a local Redis instance) for the Redis session store

2. **Configure environment**

   ```bash
   cp .env.example .env
   # edit .env and set GOOGLE_API_KEY
   ```

3. **Install dependencies**

   It is recommended to use a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .
   ```

4. **Start Redis (or use embedded fakeredis)**

   The default `REDIS_URL` is `redis://localhost:6379/0`. The easiest way to run Redis locally is with Docker:

   ```bash
   docker run -d --rm --name redis -p 6379:6379 redis:7-alpine
   ```

   Alternatively, set `USE_FAKEREDIS=true` in `.env` (or pass `USE_FAKEREDIS=true`) to use an embedded in-memory Redis implementation:

   ```bash
   USE_FAKEREDIS=true GOOGLE_API_KEY=$GOOGLE_API_KEY a2a-adk
   ```

5. **Run the server**

   ```bash
   a2a-adk
   # or equivalently:
   uvicorn a2a_adk.main:app --host 0.0.0.0 --port 8000
   ```

   The server will be available at `http://localhost:8000`.

6. **Test the endpoints**

   Health check:

   ```bash
   curl http://localhost:8000/health
   ```

   Run the team agent:

   ```bash
   curl -X POST http://localhost:8000/run/team \
     -H "Content-Type: application/json" \
     -d '{"user_id":"user-1","message":"What is the weather in Paris?"}'
   ```

   Run the route-graph agent:

   ```bash
   curl -X POST http://localhost:8000/run/graph \
     -H "Content-Type: application/json" \
     -d '{"user_id":"user-1","message":"hello"}'
   ```

   Inspect the A2A agent card:

   ```bash
   curl http://localhost:8000/a2a/team-agent/.well-known/agent-card.json
   ```

## Direct HTTP endpoints

- `GET /health` – health check.
- `POST /run/team` – run the team coordinator agent.
- `POST /run/graph` – run the route-graph Workflow agent.
- `POST /invoke/{agent_type}` – generic invoke endpoint for `team` or `graph`.
  - `?stream=true` returns ADK events as `text/event-stream` (SSE).
- `GET /events/{user_id}/{session_id}` – read the Redis callback/event stream.
- `GET /context/{user_id}/{session_id}` – read the latest context snapshot from Redis.
- `GET /sessions/{user_id}` – list sessions stored in Redis.

Example:

```bash
# Non-streaming invocation
curl -X POST http://localhost:8000/invoke/team \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","message":"What is the weather in Paris?"}'

# Streaming invocation (SSE)
curl -N -X POST "http://localhost:8000/invoke/team?stream=true" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","message":"hello"}'

# Read the persisted event stream and context
curl http://localhost:8000/events/user-1/<session-id>
curl http://localhost:8000/context/user-1/<session-id>

# Example graph run
curl -X POST http://localhost:8000/run/graph \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","message":"hello"}'
```

## A2A endpoint

The server exposes an A2A agent at `/a2a/team-agent`:

- Agent card: `GET /a2a/team-agent/.well-known/agent-card.json`
- JSON-RPC: `POST /a2a/team-agent/`

You can test it with any A2A client, e.g.:

```bash
curl http://localhost:8000/a2a/team-agent/.well-known/agent-card.json
```

## Project layout

```
a2a_adk/
├── __init__.py
├── agents.py          # team + graph agent definitions
├── callbacks.py       # Redis callback plugin
├── config.py          # environment settings
├── main.py            # FastAPI + A2A server
├── redis_client.py    # Redis / embedded fakeredis client factory
├── runner.py          # Runner factory and helpers
├── session_service.py # Redis-backed SessionService
└── cli.py             # CLI entrypoint
.devin/
└── blueprint.yaml     # Devin environment setup
pyproject.toml
.env.example
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | required | Gemini API key |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `USE_FAKEREDIS` | `false` | Use embedded `fakeredis` instead of a real Redis server |
| `APP_NAME` | `a2a-adk-2-0` | ADK app name |
| `A2A_AGENT_URL` | `http://localhost:8000/a2a/team-agent` | Public A2A endpoint URL |
| `PORT` | `8000` | Server port |
| `LOG_LEVEL` | `INFO` | Logging level |
