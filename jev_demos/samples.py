import math
from collections.abc import Callable
from typing import TypedDict


class Sample(TypedDict):
    label: str
    text: str


ROUTER_PROMPTS: list[Sample] = [
    {"label": "Greeting", "text": "Hey! Can you say hi and tell me what you can help with in one sentence?"},
    {"label": "Capital lookup", "text": "What is the capital of Australia?"},
    {"label": "Rewrite", "text": "Rewrite this to sound more professional: 'hey, just checking if u got my invoice, need it paid asap thx'"},
    {"label": "Extract fields", "text": "Extract the name, company and email from: 'Hi, I'm Priya Nair from Acme Robotics, reach me at priya@acmerobotics.io'. Return JSON."},
    {"label": "SQL query", "text": "Write a SQL query that returns the top 5 customers by total order value in the last 90 days from tables customers(id, name) and orders(id, customer_id, total, created_at)."},
    {"label": "Refactor", "text": "Refactor this function to be easier to test and explain what you changed:\n\nfunction process(o){ if(o.items.length>0){ let t=0; for(let i=0;i<o.items.length;i++){ t+=o.items[i].p*o.items[i].q } if(o.coupon){ t=t-t*0.1 } db.save({id:o.id,total:t}); email.send(o.user,'Total: '+t) } }"},
    {"label": "Summarise trade-offs", "text": "Compare Postgres logical replication with Kafka-based CDC for syncing a 2TB orders database to an analytics warehouse. Give a recommendation for a 6-person team."},
    {"label": "Race condition", "text": "Our job queue occasionally processes the same payment twice under load. Workers use SELECT ... WHERE status='pending' LIMIT 1, then UPDATE status='processing' in a separate statement, with retries on timeout. Find the root cause, explain every way a duplicate can happen, and design a fix that stays correct across a primary failover."},
    {"label": "Design a system", "text": "Design a multi-region, eventually consistent rate limiter for an API gateway handling 200k req/s that must never allow more than 2x the configured limit even during a network partition. Justify each trade-off and describe failure modes."},
    {"label": "Proof", "text": "Prove that the sum of the first n odd numbers is n^2, then use that to derive a closed form for the sum of the first n cubes, showing each step."},
]

TICKETS: list[Sample] = [
    {"label": "Double charge", "text": "Subject: Charged twice\nI was charged $49 twice on the 3rd for the same plan. Please refund the duplicate charge."},
    {"label": "Prod outage", "text": "Subject: API returning 500s\nOur production integration has been returning 500 errors for the last 20 minutes and we are losing orders. This is URGENT, please get an engineer on this immediately."},
    {"label": "Cancel threat", "text": "Subject: Third time asking\nThis is the third time I'm asking about my broken export. This is unacceptable. If it isn't fixed this week we're switching to a competitor and cancelling our annual plan."},
    {"label": "Password reset", "text": "Subject: Can't log in\nHi, I forgot my password and the reset email isn't arriving. No rush, whenever you get a chance."},
    {"label": "Pricing question", "text": "Subject: Enterprise pricing\nWe're a team of 120 and are interested in the enterprise tier. Could someone send a quote and set up a demo next week?"},
    {"label": "SEO spam", "text": "Subject: Guaranteed #1 ranking\nClick here to buy 10,000 SEO backlinks for $9. Guaranteed first page on Google or your money back!!"},
    {"label": "Feature how-to", "text": "Subject: Question about webhooks\nHello! Is there a way to filter which events get sent to a webhook endpoint? Nothing urgent, just planning our integration."},
    {"label": "Angry billing", "text": "Subject: SCAM\nYou raised my price without telling me and charged my card $199. This is ridiculous and the worst billing experience I've had. Refund me right now or I'll dispute it with my bank!!"},
]

FEED_POSTS: list[Sample] = [
    {"label": "Launch", "text": "Just released: our new open-source vector index. 3x faster builds, MIT licensed. Repo + benchmarks in the thread."},
    {"label": "Slop", "text": "🚀 AI is a GAME-CHANGER and nobody is talking about it. Here's the thing: the future belongs to those who adapt. Let that sink in. 🔥"},
    {"label": "Nugget", "text": "Tip: cache your system prompt and put per-request data after the last cache breakpoint. We measured 71% lower input cost on a 6k token prefix."},
    {"label": "Promo", "text": "GIVEAWAY! Follow, retweet and DM me 'AI' to win a free course. Use code SAVE50 for 50% off today only!"},
    {"label": "Hot take", "text": "Unpopular opinion: RAG is overrated. Most teams just need better search and a good prompt."},
    {"label": "Noise", "text": "monday again. coffee."},
    {"label": "Breaking", "text": "Breaking: the regulator has announced new disclosure rules for AI-generated ads, effective next quarter."},
    {"label": "Slop", "text": "10 ways AI will unlock your productivity 🧵 Number 7 will BLOW YOUR MIND. Bookmark this and thank me later."},
    {"label": "Nugget", "text": "How to cut LLM router latency: classify with a small typed model first, then only call the big model when confidence is low. p50 went 2.1s → 0.8s."},
    {"label": "Launch", "text": "Now available: streaming structured outputs in our SDK. Docs and migration guide linked below."},
    {"label": "Hot take", "text": "Dashboards are dead. Everything is going to be a chat box in two years."},
    {"label": "Slop", "text": "In today's fast-paced world, leveraging cutting-edge AI is no longer optional. It's a game-changer. Are you ready to unlock your potential? 🚀"},
]

# Deterministic inbox generator so a "500 emails" run is repeatable.
NAMES = ["Priya", "Marcus", "Ana", "Kenji", "Sofia", "Omar", "Lena", "Tom", "Ivy", "Raj"]
COMPANIES = ["Northwind", "Acme Robotics", "Lumen Labs", "Bluepeak", "Orbit AI", "Fable Foods", "Helix", "Nimbus"]
TEMPLATES: list[Callable[[str, str], str]] = [
    lambda n, c: f"Subject: Paid partnership with {c}\nHi, I'm {n} from {c}. We loved your content and would like to sponsor a video. Budget is $4,000. Are you open to a brand deal?",
    lambda n, c: f"Subject: Your receipt from {c}\nPayment received: $49.00. Invoice #{1000 + len(n) * 37} is attached. Thanks for your business.",
    lambda n, c: f"Subject: This week in AI, {c} digest\nTop stories this week. Read online or unsubscribe at any time.",
    lambda n, c: f"Subject: Quick call?\nHi, {n} here from {c}. Our agency can 10x your leads. Can we book a demo this week?",
    lambda n, c: f"Subject: Help, export not working\nHi, it's {n}. The export button is broken again and I have a deadline today. Can someone help asap?",
    lambda n, c: f"Subject: Verify your account immediately\nYour {c} password will expire in 24 hours. Click the link to verify your account or it will be suspended.",
    lambda n, c: f"Subject: Thank you!\nJust wanted to say thanks, {n} here. The tutorial helped me ship my first automation. Question: does it support webhooks?",
    lambda n, c: f"Subject: Wire transfer needed\nHi, this is the CEO of {c}. I need you to buy gift cards urgently and send the codes. Do not tell anyone. - {n}",
    lambda n, c: f"Subject: Ambassador program at {c}\nHey! {n} here. We're launching an ambassador program with a collab package and paid partnership. Interested?",
    lambda n, c: f"Subject: lunch thursday?\nHey, it's {n}. Are we still on for lunch Thursday?",
]


def make_emails(n: int) -> list[Sample]:
    seed = 42

    def rnd() -> float:
        nonlocal seed
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        return seed / 2**32

    out: list[Sample] = []
    for i in range(n):
        t = TEMPLATES[math.floor(rnd() * len(TEMPLATES))]
        name = NAMES[math.floor(rnd() * len(NAMES))]
        company = COMPANIES[math.floor(rnd() * len(COMPANIES))]
        out.append({"label": f"#{i + 1}", "text": t(name, company)})
    return out
