# Routing benchmark: prompt-based LLM vs System One routers

> **CPU-only experimental run — not a reference CLM measurement.**
> Environment: 2 vCPU / 8 GB RAM, no GPU. Embedder = Qwen3-8B **Q4_K_M GGUF** via `llama-server --embedding --pooling last`
> (replacing the reference bf16 vLLM pooling server); `clm-serve` heads on CPU (`CLM_DEVICE=cpu`), `--runs 1 --concurrency 1`.
> - CLM latency (~3.7 s) is the CPU embedding time of the utterance and says nothing about GPU CLM latency (reference claims ~16 ms).
> - CLM accuracy is measured after the shared `TYPESAFE_CONFIDENCE_FLOOR=0.5` and gates. Raw argmax accuracy of the CLM
>   probability vector on the same 45 utterances was 31.1% (top-3: 51.1%); most calls fell back because the distribution was flat.
>   The reference head was trained on vLLM bf16 Qwen3-8B hidden states; the 4-bit GGUF embedder is a likely (unverified) cause.
> - CLM cost is API-level 0 (self-hosted); hardware cost is not included.
> - Prompt and Jev numbers in this table are real OpenRouter calls made in the same run.

- Dataset: 45 labelled utterances across 8 sub-agents
- LLM router: `google/gemini-2.5-flash` with the full supervisor prompt + `transfer_to_agent` tool
- TypeSafe router: `typesafe/jev-1.13` Choice over 8 agents + 2 Noul gates (OpenRouter Decisions API)
- Contrastive router: `clm-latest` (CLM-8B via `clm-serve`), same typed questions; self-hosted so cost is reported as 0
- Latency = client-observed round trip for the routing call only
- Cost = `usage.cost` reported by the provider per call

| Metric | Prompt LLM routing | TypeSafe (Jev) routing | Contrastive LM routing | TypeSafe (Jev) routing vs LLM | Contrastive LM routing vs LLM |
|---|---:|---:|---:|---:|---:|
| Routing accuracy | 100.0% | 100.0% | 11.1% |  |  |
| Latency p50 (ms) | 632 | 158 | 3664 | -75.0% | +479.6% |
| Latency p95 (ms) | 978 | 235 | 4181 | -76.0% | +327.7% |
| Latency mean (ms) | 675 | 176 | 3705 | -74.0% | +448.7% |
| Input tokens / call | 1208 | 753 | 81 | -37.6% | -93.3% |
| Output tokens / call | 14 | 141 | 0 | +941.7% | -100.0% |
| Total tokens / call | 1221 | 894 | 81 | -26.8% | -93.4% |
| Cost / call (USD) | 0.000378 | 0.000032 | 0.000000 | -91.6% | -100.0% |
| Cost / 1M routing requests (USD) | 378.25 | 31.65 | 0.00 | -91.6% | -100.0% |
| Errors | 0 | 0 | 0 |  |  |

## Misroutes

| Router | Message | Expected | Predicted |
|---|---|---|---|
| contrastive | I lost my debit card yesterday, can you send me a new one? | card_management_agent | fallback_agent (conf 0.45) |
| contrastive | activate my credit card | card_management_agent | fallback_agent (conf 0.44) |
| contrastive | which of my cards still need activation? | card_management_agent | fallback_agent (conf 0.29) |
| contrastive | show fico score | card_management_agent | fallback_agent (conf 0.41) |
| contrastive | I forgot my PIN, how do I reset it? | card_management_agent | account_management_agent (conf 0.52) |
| contrastive | where is my replacement card, has it shipped? | card_management_agent | fallback_agent (conf 0.30) |
| contrastive | turn off my debit card for now | card_management_agent | fallback_agent (conf 0.28) |
| contrastive | what is my checking account balance? | account_management_agent | fallback_agent (conf 0.32) |
| contrastive | what's my full account number? | account_management_agent | fallback_agent (conf 0.39) |
| contrastive | switch to my savings account | account_management_agent | fallback_agent (conf 0.24) |
| contrastive | when did I open this account? | account_management_agent | fallback_agent (conf 0.35) |
| contrastive | I got married and need to change my last name on the account | account_management_agent | fallback_agent (conf 0.31) |
| contrastive | add my wife as an authorized user | account_management_agent | fallback_agent (conf 0.31) |
| contrastive | what's the payoff amount on my auto loan? | account_management_agent | fallback_agent (conf 0.43) |
| contrastive | am I eligible for a flex loan? | account_management_agent | fallback_agent (conf 0.63) |
| contrastive | I want to talk to a real person | transfer_to_human_agent | fallback_agent (conf 0.90) |
| contrastive | please close my account | transfer_to_human_agent | fallback_agent (conf 0.47) |
| contrastive | why was my account frozen? | transfer_to_human_agent | answer_hub_agent (conf 0.50) |
| contrastive | someone used my card without my permission, this is fraud | transfer_to_human_agent | fallback_agent (conf 0.45) |
| contrastive | I'm filing for bankruptcy, what happens to my loan? | transfer_to_human_agent | fallback_agent (conf 0.49) |
| contrastive | this is the third time I'm calling and nobody helps me, I'm really unhappy | transfer_to_human_agent | fallback_agent (conf 0.20) |
| contrastive | what is a routing number used for? | answer_hub_agent | fallback_agent (conf 0.23) |
| contrastive | how does a certificate of deposit work? | answer_hub_agent | fallback_agent (conf 0.19) |
| contrastive | what's the difference between a checking and savings account? | answer_hub_agent | fallback_agent (conf 0.27) |
| contrastive | what are your branch hours on Saturday? | answer_hub_agent | fallback_agent (conf 0.31) |
| contrastive | did my Amazon payment go through? | transaction_agent | fallback_agent (conf 0.74) |
| contrastive | what was that $42 charge at Shell on Tuesday? | transaction_agent | fallback_agent (conf 0.29) |
| contrastive | is my rent payment still pending? | transaction_agent | fallback_agent (conf 0.43) |
| contrastive | what is the interest rate on my savings account? | rates_fees_limits_management_agent | fallback_agent (conf 0.25) |
| contrastive | what's the minimum balance to avoid fees? | rates_fees_limits_management_agent | fallback_agent (conf 0.28) |
| contrastive | dispute a wire transfer fee | rates_fees_limits_management_agent | fallback_agent (conf 0.75) |
| contrastive | can I take a cash advance on my credit card? | rates_fees_limits_management_agent | fallback_agent (conf 0.20) |
| contrastive | please waive the overdraft fee you charged me | rates_fees_limits_management_agent | fallback_agent (conf 0.19) |
| contrastive | what is my daily ATM withdrawal limit? | rates_fees_limits_management_agent | fallback_agent (conf 0.34) |
| contrastive | how much does a wire transfer cost? | rates_fees_limits_management_agent | fallback_agent (conf 0.30) |
| contrastive | how much did I spend on groceries last month? | spending_insights_agent | fallback_agent (conf 0.29) |
| contrastive | what are my top spending categories this year? | spending_insights_agent | fallback_agent (conf 0.10) |
| contrastive | which subscriptions am I paying for every month? | spending_insights_agent | fallback_agent (conf 0.60) |
| contrastive | where do I spend the most money? | spending_insights_agent | fallback_agent (conf 0.65) |
| contrastive | what's the weather like in Dallas today? | fallback_agent | answer_hub_agent (conf 0.53) |
