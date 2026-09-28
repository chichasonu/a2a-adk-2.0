# Routing benchmark: prompt-based LLM vs System One routers

- Dataset: 45 labelled utterances across 8 sub-agents
- LLM router: `google/gemini-2.5-flash` with the full supervisor prompt + `transfer_to_agent` tool
- Contrastive router: `clm-latest` (CLM-8B via `clm-serve`), same typed questions; self-hosted so cost is reported as 0
- Latency = client-observed round trip for the routing call only
- Cost = `usage.cost` reported by the provider per call

| Metric | Prompt LLM routing | Contrastive LM routing | Contrastive LM routing vs LLM |
|---|---:|---:|---:|
| Routing accuracy | 100.0% | 8.9% |  |
| Latency p50 (ms) | 1417 | 13012 | +818.3% |
| Latency p95 (ms) | 2076 | 17206 | +728.8% |
| Latency mean (ms) | 1437 | 11800 | +721.4% |
| Input tokens / call | 1208 | 70 | -94.2% |
| Output tokens / call | 14 | 0 | -100.0% |
| Total tokens / call | 1221 | 70 | -94.3% |
| Cost / call (USD) | 0.000392 | 0.000000 | -100.0% |
| Cost / 1M routing requests (USD) | 392.16 | 0.00 | -100.0% |
| Errors | 0 | 0 |  |

## Misroutes

| Router | Message | Expected | Predicted |
|---|---|---|---|
| contrastive | activate my credit card | card_management_agent | fallback_agent (conf 0.72) |
| contrastive | which of my cards still need activation? | card_management_agent | fallback_agent (conf 0.27) |
| contrastive | show fico score | card_management_agent | fallback_agent (conf 0.36) |
| contrastive | I forgot my PIN, how do I reset it? | card_management_agent | fallback_agent (conf 0.37) |
| contrastive | where is my replacement card, has it shipped? | card_management_agent | fallback_agent (conf 0.29) |
| contrastive | turn off my debit card for now | card_management_agent | fallback_agent (conf 0.70) |
| contrastive | what is my checking account balance? | account_management_agent | fallback_agent (conf 0.20) |
| contrastive | what's my full account number? | account_management_agent | fallback_agent (conf 0.24) |
| contrastive | switch to my savings account | account_management_agent | fallback_agent (conf 0.29) |
| contrastive | when did I open this account? | account_management_agent | fallback_agent (conf 0.79) |
| contrastive | I got married and need to change my last name on the account | account_management_agent | fallback_agent (conf 0.39) |
| contrastive | add my wife as an authorized user | account_management_agent | fallback_agent (conf 0.18) |
| contrastive | what's the payoff amount on my auto loan? | account_management_agent | fallback_agent (conf 0.35) |
| contrastive | am I eligible for a flex loan? | account_management_agent | fallback_agent (conf 0.75) |
| contrastive | I want to talk to a real person | transfer_to_human_agent | fallback_agent (conf 0.92) |
| contrastive | please close my account | transfer_to_human_agent | fallback_agent (conf 0.65) |
| contrastive | why was my account frozen? | transfer_to_human_agent | fallback_agent (conf 0.40) |
| contrastive | someone used my card without my permission, this is fraud | transfer_to_human_agent | fallback_agent (conf 0.40) |
| contrastive | I'm filing for bankruptcy, what happens to my loan? | transfer_to_human_agent | fallback_agent (conf 0.39) |
| contrastive | this is the third time I'm calling and nobody helps me, I'm really unhappy | transfer_to_human_agent | fallback_agent (conf 0.58) |
| contrastive | what is a routing number used for? | answer_hub_agent | fallback_agent (conf 0.37) |
| contrastive | how does a certificate of deposit work? | answer_hub_agent | fallback_agent (conf 0.20) |
| contrastive | what's the difference between a checking and savings account? | answer_hub_agent | fallback_agent (conf 0.29) |
| contrastive | what are your branch hours on Saturday? | answer_hub_agent | fallback_agent (conf 0.39) |
| contrastive | show me my recent transactions | transaction_agent | fallback_agent (conf 0.19) |
| contrastive | did my Amazon payment go through? | transaction_agent | fallback_agent (conf 0.87) |
| contrastive | find all transactions over $500 last month | transaction_agent | fallback_agent (conf 0.45) |
| contrastive | what was that $42 charge at Shell on Tuesday? | transaction_agent | fallback_agent (conf 0.17) |
| contrastive | is my rent payment still pending? | transaction_agent | fallback_agent (conf 0.37) |
| contrastive | what is the interest rate on my savings account? | rates_fees_limits_management_agent | fallback_agent (conf 0.19) |
| contrastive | what's the minimum balance to avoid fees? | rates_fees_limits_management_agent | fallback_agent (conf 0.36) |
| contrastive | dispute a wire transfer fee | rates_fees_limits_management_agent | fallback_agent (conf 0.83) |
| contrastive | can I take a cash advance on my credit card? | rates_fees_limits_management_agent | fallback_agent (conf 0.28) |
| contrastive | please waive the overdraft fee you charged me | rates_fees_limits_management_agent | fallback_agent (conf 0.33) |
| contrastive | what is my daily ATM withdrawal limit? | rates_fees_limits_management_agent | fallback_agent (conf 0.16) |
| contrastive | how much does a wire transfer cost? | rates_fees_limits_management_agent | fallback_agent (conf 0.32) |
| contrastive | how much did I spend on groceries last month? | spending_insights_agent | fallback_agent (conf 0.51) |
| contrastive | what are my top spending categories this year? | spending_insights_agent | fallback_agent (conf 0.11) |
| contrastive | which subscriptions am I paying for every month? | spending_insights_agent | fallback_agent (conf 0.81) |
| contrastive | where do I spend the most money? | spending_insights_agent | fallback_agent (conf 0.85) |
| contrastive | what's the weather like in Dallas today? | fallback_agent | answer_hub_agent (conf 0.58) |
