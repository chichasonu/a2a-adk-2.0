"""Zero-shot CLM diagnostic: raw top-1/top-3 accuracy of several agent-description
styles against a running clm-serve, fallback excluded, no confidence floor.

    python -m benchmarks.clm_description_probe examples:str action:str para:strraw

Suffixes: ``:str`` sends the utterance as a plain string state (default: ``{request: ...}``),
``raw`` uses the ``clm-raw`` ablation head. Set CLM_BASE_URL to override the server."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

DATA = json.loads(Path(__file__).with_name("routing_dataset.json").read_text())
ITEMS = DATA if isinstance(DATA, list) else DATA["items"]

VARIANTS = {}

VARIANTS["examples"] = {
    "card_management_agent": (
        "Card services. Examples: activate my new card; replace a lost or stolen "
        "card; turn my debit card off or on; reset or change my card PIN; track "
        "my replacement card shipment; which cards need activation; show my FICO "
        "credit score."
    ),
    "account_management_agent": (
        "Account servicing. Examples: what is my checking balance; what is my "
        "account number; switch to my savings account; when did I open this "
        "account; change my name on the account; add an authorized user; "
        "payoff amount on my auto loan; am I eligible for a flex loan."
    ),
    "transfer_to_human_agent": (
        "Live representative escalation. Examples: I want to talk to a real "
        "person; close my account; why is my account frozen or locked; someone "
        "used my card, this is fraud; I am filing for bankruptcy; I am unhappy "
        "and nobody helps me."
    ),
    "answer_hub_agent": (
        "General banking education and bank information. Examples: what is a "
        "routing number; how does a certificate of deposit work; difference "
        "between checking and savings; what are the branch hours."
    ),
    "transaction_agent": (
        "Transaction history and status. Examples: show my recent transactions; "
        "did my Amazon payment go through; find transactions over $500 last "
        "month; what was this $42 charge at Shell; is my rent payment pending."
    ),
    "rates_fees_limits_management_agent": (
        "Rates, fees and limits. Examples: interest rate on my savings; minimum "
        "balance to avoid fees; waive or dispute a fee; cash advance on my "
        "credit card; daily ATM withdrawal limit; how much does a wire cost."
    ),
    "spending_insights_agent": (
        "Spending analytics. Examples: how much did I spend on groceries; my top "
        "spending categories this year; which subscriptions do I pay monthly; "
        "where do I spend the most money."
    ),
}

VARIANTS["para"] = {
    "card_management_agent": (
        "Credit card and debit card servicing. Typical requests: order a new card "
        "because mine was lost or stolen; activate the card I just received; "
        "freeze or unfreeze my debit card; change my card PIN; where is the card "
        "you mailed me; do any of my cards need to be activated; what is my "
        "credit score."
    ),
    "account_management_agent": (
        "Deposit and loan account servicing. Typical requests: how much money is "
        "in my checking; tell me my account number; use my savings account "
        "instead; how long have I had this account; update the name on my "
        "account; add a joint owner or authorized user; how much to pay off my "
        "loan; can I get a flex loan."
    ),
    "transfer_to_human_agent": (
        "Hand off to a live human representative. Typical requests: let me speak "
        "to an agent; I want to cancel and close my account; my account was "
        "locked or suspended, why; unauthorized charges, someone stole my card; "
        "I am declaring bankruptcy; I am frustrated, no one is helping me."
    ),
    "answer_hub_agent": (
        "General banking knowledge and bank information, not about the user's "
        "own accounts. Typical requests: explain what an ACH transfer is; how do "
        "CDs or money market accounts work; savings vs checking explained; when "
        "is the branch open; what holidays are you closed."
    ),
    "transaction_agent": (
        "Transaction history and payment status. Typical requests: list my "
        "latest purchases; has my Netflix payment posted; show debits above "
        "$200 in March; what is this $15 charge from Starbucks; is my transfer "
        "still processing."
    ),
    "rates_fees_limits_management_agent": (
        "Interest rates, fees and account limits. Typical requests: what APY "
        "does my savings earn; how do I avoid the monthly maintenance fee; "
        "refund the overdraft charge; can I get a cash advance and what does it "
        "cost; how much can I withdraw at an ATM per day; foreign transaction "
        "fee amount."
    ),
    "spending_insights_agent": (
        "Spending analytics and budgeting insights. Typical requests: what did "
        "I spend on dining out this month; break down my spending by category; "
        "list my recurring monthly subscriptions; which merchants get most of "
        "my money; is my spending up compared with last year."
    ),
}

VARIANTS["action"] = {
    k: f"Call transfer_to_agent('{k}'). {v}" for k, v in VARIANTS["para"].items()
}

VARIANTS["short"] = {
    "card_management_agent": "Manage my credit or debit card: activate, replace, lock, PIN, delivery, credit score.",
    "account_management_agent": "Manage my bank account: balance, account number, account details, name change, authorized users, loans.",
    "transfer_to_human_agent": "Talk to a human representative: close account, frozen account, fraud, bankruptcy, complaint.",
    "answer_hub_agent": "Explain a general banking concept or bank information.",
    "transaction_agent": "Look up my transactions: recent, search by merchant/amount/date, payment status.",
    "rates_fees_limits_management_agent": "Interest rates, fees, limits, cash advance, fee waivers and fee disputes.",
    "spending_insights_agent": "Analyze my spending: categories, merchants, trends, subscriptions.",
}


MODEL = "clm-latest"


async def run(name, criteria, state_mode):
    correct = 0
    n = 0
    top3 = 0
    rows = []
    async with httpx.AsyncClient(timeout=600) as c:
        for it in ITEMS:
            msg, exp = it["message"], it["expected"]
            if exp not in criteria:
                continue
            state = {"request": msg} if state_mode == "dict" else msg
            payload = {
                "model": MODEL,
                "state": state,
                "questions": {
                    "agent": {
                        "type": "choice",
                        "instructions": "Which sub-agent of a banking assistant should handle the user's request?",
                        "criteria": criteria,
                    }
                },
            }
            t = time.perf_counter()
            r = await c.post(
                os.environ.get("CLM_BASE_URL", "http://127.0.0.1:8700").rstrip("/")
                + "/v1/systemone",
                json=payload,
            )
            r.raise_for_status()
            a = r.json()["answers"]["agent"]
            probs = a["probabilities"]
            ranked = sorted(probs, key=probs.get, reverse=True)
            n += 1
            correct += ranked[0] == exp
            top3 += exp in ranked[:3]
            rows.append(
                (
                    msg,
                    exp,
                    ranked[0],
                    round(a["confidence"], 2),
                    round(time.perf_counter() - t, 1),
                )
            )
    print(
        f"== {name}/{state_mode}: top1 {correct}/{n} = {correct / n:.1%}, top3 {top3 / n:.1%}"
    )
    for r in rows:
        print("  ", "OK " if r[1] == r[2] else "MISS", r)
    sys.stdout.flush()


async def main():
    for name in sys.argv[1:] or ["examples"]:
        global MODEL
        v, _, mode = name.partition(":")
        MODEL = "clm-raw" if mode.endswith("raw") else "clm-latest"
        mode = mode.replace("raw", "") or "dict"
        await run(v, VARIANTS[v], mode or "dict")


asyncio.run(main())
