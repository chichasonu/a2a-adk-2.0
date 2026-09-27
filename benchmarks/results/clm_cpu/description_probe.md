# CLM zero-shot description probe (CPU, Qwen3-8B Q4 GGUF embedder)

Question tested: is CLM's low routing accuracy a *description/config* problem
(fixable by rewording the agent criteria, dropping the catch-all
`fallback_agent`) or a *model* problem?

Setup: real `clm-serve` (`clm-latest` head, `clm-raw` ablation), llama.cpp
`--embedding --pooling last` with Qwen3-8B Q4_K_M on 2 CPUs. 42 labelled
utterances (the 3 `fallback_agent` items excluded). `fallback_agent` removed
from the candidate list; **no confidence floor** — raw argmax accuracy, i.e. the
best CLM could possibly do with each description style.
Reproduce: `python -m benchmarks.clm_description_probe <variant>[:str][raw]`.

| Description style | State format | Head | Top-1 | Top-3 |
|---|---|---|---:|---:|
| Original one-line criteria (`AGENT_CRITERIA`, benchmark run) | `{request: …}` | clm-latest | 31.1% | 51.1% |
| `short` — one terse line per agent | `{request: …}` | clm-latest | 23.8% | 50.0% |
| `examples` — the **exact benchmark utterances** pasted into each description | `{request: …}` | clm-latest | 26.2% | 69.0% |
| `examples` (same, leaked) | plain string | clm-latest | 31.0% | 78.6% |
| `para` — paraphrased example-rich descriptions | plain string | clm-latest | 21.4% | 69.0% |
| `action` — `para` prefixed with `Call transfer_to_agent('<name>').` | plain string | clm-latest | **38.1%** | 50.0% |
| `para` | plain string | clm-raw | 14.3% | 54.8% |

Prompt LLM (Gemini 2.5 Flash) and Jev on the same 45 utterances: 100%.

## Reading

- Every style shows one candidate absorbing most predictions regardless of the
  utterance (`account_management_agent` for `examples`/`para`,
  `rates_fees_limits_management_agent` for `action`,
  `fallback_agent` when it is a candidate). Scores are dominated by an
  action prior, not by the utterance — this is why the catch-all fallback
  "won" with 0.8–0.9 confidence in the full benchmark.
- Even with the test utterances copied verbatim into the descriptions
  (an unfair, leaked setup) top-1 stays around 30%. Rewording therefore cannot
  close the gap to the LLM; the zero-shot `clm-latest` head does not separate
  these 7 banking intents.
- Removing `fallback_agent` from the candidates changes *which* agent absorbs
  traffic, not the accuracy.

## Caveats

- Q4 GGUF embedder, not the bf16 vLLM Qwen3-8B the heads were trained on. The
  Windows Q8 run with the original criteria (8.9% after the 0.5 floor) is in
  line with the Q4 run (11.1%), so quantization is unlikely to be the main
  factor, but a bf16/GPU run is the only way to rule it out.
- 42–45 utterances; differences of a few points between rows are noise.

## Implication

Zero-shot CLM is not a candidate for this router. The remaining path is
fine-tuning CLM's projection head on labelled banking utterances
(`train/finetune.py` in the CLM repo, GPU), then re-running
`benchmarks.route_benchmark --no-typesafe --clm-url …`. Prompt routing (and
Jev, from the earlier comparison) remain the working options.
