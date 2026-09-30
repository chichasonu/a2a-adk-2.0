# Benchmark reports

Committed copies of benchmark runs (`benchmark/results/` stays git-ignored for ad-hoc runs).
All runs score the full BANKING77 test split (3,080 rows, seed 42), so rows are directly comparable.
SetFit models were trained with `--few-shot 16 --num-iterations 10` on CPU (2 vCPU, no GPU).

| folder | systems | command |
|---|---|---|
| `2026-09-28_gemini-3.5-flash-lite/` | `google/gemini-3.5-flash-lite` via OpenRouter, 77-intent and 4-agent prompts | `run_benchmark.py --skip-setfit --llm-granularity both --concurrency 8` |
| `2026-09-28_setfit-bge-small/` | SetFit on `BAAI/bge-small-en-v1.5` (agent + intent models) | `run_benchmark.py --skip-llm` |
| `2026-09-30_setfit-minilm-vs-bge-small/` | SetFit on `sentence-transformers/all-MiniLM-L6-v2` (agent + intent) re-scored alongside the bge-small models | `run_benchmark.py --skip-llm --setfit-model models/setfit-minilm-agent --setfit-model models/setfit-minilm-intent --setfit-model models/setfit-agent --setfit-model models/setfit-intent` |

Each folder contains:

- `benchmark_<timestamp>.md`: summary table
- `benchmark_<timestamp>.json`: full metrics (per-agent precision/recall/F1, confusion matrix, latency, cost)
- `predictions_<timestamp>.csv`: every test row with the true intent/agent and each system's prediction

## Summary

| system | intent acc (77-way) | agent acc (4-way) | agent macro-F1 | p50 ms | p95 ms | cost USD |
|---|---|---|---|---|---|---|
| SetFit bge-small, agent model | — | 97.14% | 0.963 | 10.0 | 12.5 | 0 |
| SetFit bge-small, intent model | 84.22% | 97.37% | 0.964 | 9.9 | 17.6 | 0 |
| SetFit MiniLM, agent model | — | 96.69% | 0.956 | 5.4 | 6.9 | 0 |
| SetFit MiniLM, intent model | 82.92% | 97.14% | 0.962 | 5.4 | 9.3 | 0 |
| Gemini 3.5 Flash Lite, 77 intents | 83.34% | 97.11% | 0.967 | 700.9 | 888.1 | 1.22 |
| Gemini 3.5 Flash Lite, 4 agents | — | 90.03% | 0.870 | 688.5 | 910.4 | 0.27 |
