# A2A ADK 2.0 Agent

A reference Google ADK 2.0 agent demonstrating:

- **Team agents**: a root coordinator that delegates to specialized sub-agents via `sub_agents` and `transfer_to_agent`.
- **Route graphs**: a `Workflow` with a conditional router `FunctionNode` that dispatches to the right specialist.
- **Redis SessionService**: durable session memory shared across processes/workers.
- **Runner + AgentExecutor integration**: ADK `Runner` wired to Redis, plus A2A `AgentExecutor` for standard A2A JSON-RPC/SSE serving.
- **Callback plugin**: a `BasePlugin` that captures before/after tool and model callbacks and persists them to Redis.
- **Event & context streaming**: ADK events and context snapshots are written to Redis streams/hashes.
- **Generic HTTP invoke endpoint**: invoke any agent with `POST /invoke/{agent_type}` and optional SSE streaming.
- **Separate agent endpoints**: each agent (`team`, `graph`, `greeting`, `weather`, `math`, `mcp`, `orchestrator`) has its own `POST /run/{agent_type}` and A2A endpoint.
- **Remote A2A supervisor/orchestrator**: a main `supervisor_orchestrator` agent whose sub-agents are `RemoteA2aAgent`s that call the other agents over A2A.
- **Error handling & resiliency**: structured error responses, request validation, readiness probe, and LLM-failure isolation so Redis streams still capture the run.
- **Observability**: request logging middleware with `X-Request-ID`, per-request timing, and `/metrics` counters for runs, errors, events and tool calls.
- **Tool cache**: Redis-backed cache for tool declarations that automatically rebuilds when tool source changes; `/refresh-tools` forces a refresh.
- **MCP integration**: a Spring Boot MCP server (`mcp-server/`) exposes tools over streamable HTTP; the ADK agent discovers, caches, and invokes them, and can refresh the cache when tools change.
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

   The server is a standard Uvicorn/FastAPI application. You can start it with the bundled console script, as a Python module, or directly with Uvicorn:

   ```bash
   # Console script
   a2a-adk

   # Python module
   python -m a2a_adk --host 0.0.0.0 --port 8000 --reload

   # Uvicorn directly
   uvicorn a2a_adk.main:app --host 0.0.0.0 --port 8000
   ```

   The server will be available at `http://localhost:8000`.

   If you run on a different port or host, set `A2A_BASE_URL` so the A2A agent cards and the orchestrator's remote sub-agents point at the right address:

   ```bash
   A2A_BASE_URL=http://localhost:8000 GOOGLE_API_KEY=$GOOGLE_API_KEY a2a-adk
   ```

6. **Start the MCP server (optional)**

   The agent can also call tools from the Spring Boot MCP server in `mcp-server/`:

   ```bash
   cd mcp-server
   ./mvnw spring-boot:run
   ```

   The MCP server listens on `http://localhost:8080` with the MCP endpoint at `http://localhost:8080/mcp`.

7. **Test the endpoints**

   Health and readiness:

   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/health/ready
   ```

   List all available agents:

   ```bash
   curl http://localhost:8000/agents
   ```

   Run an agent directly:

   ```bash
   curl -X POST http://localhost:8000/run/team \
     -H "Content-Type: application/json" \
     -d '{"user_id":"user-1","message":"What is the weather in Paris?"}'

   curl -X POST http://localhost:8000/run/graph \
     -H "Content-Type: application/json" \
     -d '{"user_id":"user-1","message":"hello"}'

   curl -X POST http://localhost:8000/run/orchestrator \
     -H "Content-Type: application/json" \
     -d '{"user_id":"user-1","message":"What is the stock price of AAPL?"}'
   ```

   Inspect any A2A agent card:

   ```bash
   curl http://localhost:8000/a2a/team-agent/.well-known/agent-card.json
   curl http://localhost:8000/a2a/orchestrator-agent/.well-known/agent-card.json
   ```

   View cached tool declarations:

   ```bash
   curl http://localhost:8000/tools
   ```

   Force a tool cache refresh after editing `a2a_adk/tools.py` or the Spring MCP server tool classes:

   ```bash
   curl -X POST "http://localhost:8000/refresh-tools"
   ```

## Direct HTTP endpoints

- `GET /health` – liveness health check.
- `GET /health/ready` – readiness probe that pings Redis.
- `GET /metrics` – counters for runs, errors, events and tool calls.
- `GET /agents` – list all configured agents with run, invoke and A2A card URLs.
- `POST /run/{agent_type}` – run any configured root agent.
  - `agent_type` is one of: `team`, `graph`, `greeting`, `weather`, `math`, `mcp`, `orchestrator`.
- `POST /invoke/{agent_type}` – generic invoke endpoint for any configured agent.
  - `?stream=true` returns ADK events as `text/event-stream` (SSE).
- `GET /events/{user_id}/{session_id}` – read the Redis callback/event stream.
- `GET /context/{user_id}/{session_id}` – read the latest context snapshot from Redis.
- `GET /sessions/{user_id}` – list sessions stored in Redis.
- `GET /tools` – list cached local and MCP tool declarations.
- `POST /refresh-tools` – invalidate and rebuild local and MCP tool caches, and reconstruct all ADK runners so new/changed/removed tools are picked up.

Example:

```bash
# Non-streaming invocation
for agent in team graph greeting weather math mcp orchestrator; do
  curl -X POST http://localhost:8000/run/$agent \
    -H "Content-Type: application/json" \
    -d '{"user_id":"user-1","message":"hello"}'
done

# Streaming invocation (SSE)
curl -N -X POST "http://localhost:8000/invoke/team?stream=true" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","message":"hello"}'

# Read the persisted event stream and context
curl http://localhost:8000/events/user-1/<session-id>
curl http://localhost:8000/context/user-1/<session-id>

# Metrics and tool cache
curl http://localhost:8000/metrics
curl http://localhost:8000/tools
curl -X POST "http://localhost:8000/refresh-tools"
```

## A2A endpoints

Each agent is exposed as an A2A-compatible agent under `/a2a/{agent_type}-agent`:

| Agent | Agent card | JSON-RPC endpoint |
|---|---|---|
| `team` | `GET /a2a/team-agent/.well-known/agent-card.json` | `POST /a2a/team-agent/` |
| `graph` | `GET /a2a/graph-agent/.well-known/agent-card.json` | `POST /a2a/graph-agent/` |
| `greeting` | `GET /a2a/greeting-agent/.well-known/agent-card.json` | `POST /a2a/greeting-agent/` |
| `weather` | `GET /a2a/weather-agent/.well-known/agent-card.json` | `POST /a2a/weather-agent/` |
| `math` | `GET /a2a/math-agent/.well-known/agent-card.json` | `POST /a2a/math-agent/` |
| `mcp` | `GET /a2a/mcp-agent/.well-known/agent-card.json` | `POST /a2a/mcp-agent/` |
| `orchestrator` | `GET /a2a/orchestrator-agent/.well-known/agent-card.json` | `POST /a2a/orchestrator-agent/` |

You can test any card with `curl`, e.g.:

```bash
curl http://localhost:8000/a2a/team-agent/.well-known/agent-card.json
curl http://localhost:8000/a2a/orchestrator-agent/.well-known/agent-card.json
```

## Supervisor / orchestrator agent

`POST /run/orchestrator` (and `POST /a2a/orchestrator-agent`) runs a supervisor agent that delegates to the other agents over A2A. The supervisor's `sub_agents` are `RemoteA2aAgent`s backed by the same server's A2A endpoints, so you can call it like any other agent and it will route the request to the appropriate specialist.

## Project layout

```
a2a_adk/
├── __init__.py
├── agents.py          # team, graph, specialists and remote-A2A orchestrator
├── callbacks.py       # Redis callback plugin
├── config.py          # environment settings
├── main.py            # FastAPI + A2A server
├── mcp_tools.py       # MCP tool client, cache and remote execution
├── redis_client.py    # Redis / embedded fakeredis client factory
├── runner.py          # Runner factory and helpers
├── session_service.py # Redis-backed SessionService
├── tool_cache.py      # Redis-backed local tool declaration cache
├── tools.py           # local tool function definitions
└── cli.py             # CLI entrypoint
mcp-server/            # Spring Boot MCP server
├── pom.xml
├── src/main/java/com/example/mcp/server/tools/
│   ├── FinanceTools.java
│   ├── UtilityTools.java
│   └── WeatherTools.java
└── src/main/resources/application.yml
.devin/
└── blueprint.yaml     # Devin environment setup
pyproject.toml
.env.example
```

## Authentication

Both the ADK FastAPI server and the Spring MCP server can be protected with the same API key / Bearer token pattern.

1. Set `API_KEY` for the Python ADK server:

   ```bash
   API_KEY=change-me GOOGLE_API_KEY=$GOOGLE_API_KEY USE_FAKEREDIS=true a2a-adk
   ```

   Clients then send the key in every request except health/readiness/agents/docs:

   ```bash
   curl -X POST http://localhost:8000/run/team \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer change-me" \
     -d '{"user_id":"user-1","message":"hello"}'

   # or
   curl -X POST http://localhost:8000/run/team \
     -H "Content-Type: application/json" \
     -H "X-API-Key: change-me" \
     -d '{"user_id":"user-1","message":"hello"}'
   ```

   When `API_KEY` is set, the `supervisor_orchestrator` automatically sends it to the other A2A agents over `Authorization: Bearer <token>`.

2. Set `MCP_API_KEY` for the Spring MCP server:

   ```bash
   export MCP_API_KEY=change-me
   cd mcp-server
   ./mvnw spring-boot:run
   ```

   The Python ADK agent automatically sends this token when it discovers or calls MCP tools.

Leave these variables empty to disable authentication.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | required | Gemini API key |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `USE_FAKEREDIS` | `false` | Use embedded `fakeredis` instead of a real Redis server |
| `APP_NAME` | `a2a-adk-2-0` | ADK app name |
| `A2A_AGENT_URL` | `http://localhost:8000/a2a/team-agent` | Public A2A endpoint URL for the team agent card |
| `A2A_BASE_URL` | `http://localhost:8000` | Public base URL used for all A2A agent cards and the orchestrator's remote sub-agents |
| `MCP_ENABLED` | `true` | Enable MCP tool discovery |
| `MCP_SERVER_URL` | `http://localhost:8080/mcp` | Spring Boot MCP server endpoint |
| `MCP_API_KEY` | `""` | Bearer token for the MCP server (`Authorization: Bearer ...`) |
| `API_KEY` | `""` | Bearer token / API key for the ADK FastAPI and A2A endpoints |
| `PORT` | `8000` | Server port |
| `LOG_LEVEL` | `INFO` | Logging level |

## MCP tool caching and refresh

The agent caches tool declarations in Redis in two places:

- **Local tools** (`a2a_adk/tool_cache.py`): `adk:tools:{app_name}:{tool_name}` keyed by a hash of the Python source code. Changing `a2a_adk/tools.py` automatically invalidates the cached declaration.
- **MCP tools** (`a2a_adk/mcp_tools.py`): `adk:mcp_tools:{app_name}:{tool_name}` keyed by a hash of the MCP tool's JSON input schema. Adding or changing a tool in the Spring MCP server changes its schema hash, and calling `POST /refresh-tools` re-fetches the tool list, updates the cache, and rebuilds all ADK runners so the agents see the new declarations.
