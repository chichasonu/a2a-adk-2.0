# Routing benchmark: prompt-based LLM vs TypeSafe System One (Jev)

- Dataset: 90 labelled utterances across 8 sub-agents
- LLM router: `google/gemini-2.5-flash` with the full supervisor prompt + `transfer_to_agent` tool
- TypeSafe router: `typesafe/jev-1.13` Choice over 8 agents + 2 Noul gates
- Latency = client-observed round trip through OpenRouter for the routing call only
- Cost = `usage.cost` reported by OpenRouter per call

| Metric | Prompt LLM routing | TypeSafe routing | Change |
|---|---:|---:|---:|
| Routing accuracy | 100.0% | 100.0% |  |
| Latency p50 (ms) | 1021 | 227 | -77.7% |
| Latency p95 (ms) | 12907 | 550 | -95.7% |
| Latency mean (ms) | 4078 | 272 | -93.3% |
| Input tokens / call | 1208 | 753 | -37.6% |
| Output tokens / call | 14 | 141 | +934.1% |
| Total tokens / call | 1221 | 894 | -26.8% |
| Cost / call (USD) | 0.000349 | 0.000032 | -90.9% |
| Cost / 1M routing requests (USD) | 349.47 | 31.65 | -90.9% |
| Errors | 0 | 0 |  |

## End-to-end (supervisor + sub-agent reply via HTTP)

| Metric | /run/supervisor/prompt | /run/supervisor/typesafe | Change |
|---|---:|---:|---:|
| Latency p50 (ms) | 10464 | 1404 | -86.6% |
| Latency mean (ms) | 9028 | 1471 | -83.7% |
| Samples | 8 | 8 | |
