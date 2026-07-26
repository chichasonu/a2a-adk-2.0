# A2A ADK 2.0 Agent

A reference Google ADK 2.0 agent demonstrating:

- **Team agents**: a root coordinator that delegates to specialized sub-agents via `sub_agents` and `transfer_to_agent`.
- **Route graphs**: a `Workflow` with a conditional router `FunctionNode` that dispatches to the right specialist.
- **Redis SessionService**: durable session memory shared across processes/workers.
- **Runner + AgentExecutor integration**: ADK `Runner` wired to Redis, plus A2A `AgentExecutor` for standard A2A JSON-RPC/SSE serving.
- **Gemini API key**: uses `GOOGLE_API_KEY` for Gemini models.

## Quick start

1. Copy the environment file and fill in your Gemini API key:

   ```bash
   cp .env.example .env
   # edit .env and set GOOGLE_API_KEY
   ```

2. Install the package (optionally in a virtual environment):

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3. Make sure Redis is available (default `redis://localhost:6379/0`):

   ```bash
   docker run -d -p 6379:6379 --name redis redis:7-alpine
   ```

4. Start the FastAPI/A2A server:

   ```bash
   a2a-adk
   # or equivalently:
   uvicorn a2a_adk.main:app --host 0.0.0.0 --port 8000
   ```

## Direct HTTP endpoints

- `GET /health` – health check.
- `POST /run/team` – run the team coordinator agent.
- `POST /run/graph` – run the route-graph Workflow agent.
- `GET /sessions/{user_id}` – list sessions stored in Redis.

Example:

```bash
curl -X POST http://localhost:8000/run/team \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","message":"What is the weather in Paris?"}'

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
├── config.py          # environment settings
├── main.py            # FastAPI + A2A server
├── runner.py          # Runner factory and helpers
├── session_service.py # Redis-backed SessionService
└── cli.py             # CLI entrypoint
pyproject.toml
.env.example
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | required | Gemini API key |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `APP_NAME` | `a2a-adk-2-0` | ADK app name |
| `A2A_AGENT_URL` | `http://localhost:8000/a2a/team-agent` | Public A2A endpoint URL |
| `PORT` | `8000` | Server port |
| `LOG_LEVEL` | `INFO` | Logging level |
