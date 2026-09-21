import asyncio
import json
import math
import os
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import openai
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from typesafe_sdk import Noul

from .config import (
    JEV_IN_PER_M,
    MODELS,
    PROVIDERS,
    _status_and_message,
    account_error,
    budget,
    has_jev_key,
    mark_down,
    provider_state,
    reset_down,
    usable,
)
from .jev import jev_call, jev_state, mark_jev_down, probe_jev, reset_jev_down
from .keys import (
    HEADER,
    KEY_NAMES,
    current_user_keys,
    hosted,
    key_source,
    redact,
    set_user_keys,
    valid_key,
)
from .llm import Effort, complete, probe_llm
from .router import decide
from .samples import FEED_POSTS, ROUTER_PROMPTS, TICKETS, make_emails
from .triage import decide_action, draft_reply, triage_with_jev, triage_with_llm
from .usecases import (
    classify_email_with_jev,
    classify_email_with_llm,
    generate_titles,
    label_post,
    score_title,
)

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png"}
# Everything is served from this origin; inline styles are used by the charts. Keys never leave the
# browser except to this server.
SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
MAX_BODY = 200_000


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


async def _read_json(request: Request) -> dict[str, Any]:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY:
        raise HttpError(413, "Request too large")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HttpError(413, "Request too large")
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        raise HttpError(400, "Invalid JSON")
    return body if isinstance(body, dict) else {}


def _text(v: Any, field: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise HttpError(400, f'"{field}" must be a non-empty string')
    return v


def _js_number(v: Any) -> float:
    if isinstance(v, bool):
        return float(v)
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0.0
        except ValueError:
            return math.nan
    return math.nan


def _num(v: Any, fallback: float, lo: float, hi: float) -> float:
    """TS num(): finite numbers get rounded and clamped; anything else takes the fallback."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return min(hi, max(lo, math.floor(v + 0.5)))
    return fallback


def _provider(b: dict[str, Any]) -> str:
    if "provider" not in b or b["provider"] is None:
        return "claude"
    if b["provider"] in ("claude", "kimi"):
        return b["provider"]
    raise HttpError(400, '"provider" must be "claude" or "kimi"')


TIERS = ["light", "standard", "frontier"]


def _tier(v: Any) -> str:
    if v not in TIERS:
        raise HttpError(400, f'"tier" must be one of {", ".join(TIERS)}')
    return v


def _effort_for(p: str, t: str) -> Effort:
    """Thinking effort per tier, kept low on purpose: the demo should be cheap to run."""
    if t == "frontier":
        return "high" if p == "kimi" else "medium"
    return "low"


async def _status() -> dict[str, Any]:
    js = jev_state()
    return {
        "jev": js["state"],
        "jevNote": js["note"],
        "jevSource": key_source("jev"),
        "jevInPerM": JEV_IN_PER_M,
        "hosted": hosted(),
        "models": MODELS,
        "providers": {
            k: {
                **PROVIDERS[k],
                **provider_state(k),
                "keySource": key_source("openrouter"),
                "budget": {"spentUsd": budget.spent("openrouter"), "capUsd": budget.cap("openrouter")},
            }
            for k in PROVIDERS
        },
    }


def _friendly_key_error(err: BaseException) -> str:
    """Map a provider failure to something a visitor can act on, without ever echoing a key back."""
    status, message = _status_and_message(err)
    if status in (401, 403):
        return "The provider rejected this key. Check it is copied in full and still active."
    account = account_error(err)
    if account:
        return redact(account)[:200]
    if isinstance(err, (openai.APIConnectionError, openai.APITimeoutError)) or "timeout" in message.lower() or "fetch failed" in message.lower():
        return "Could not reach the provider. Try again in a moment."
    return redact(message or "Key check failed")[:200]


async def _check_keys() -> dict[str, Any]:
    """Validate only the keys the visitor supplied (never the server's own) with a one-token call
    each."""
    mine = current_user_keys()
    results: dict[str, dict[str, Any]] = {}

    async def one(n: str) -> None:
        try:
            if n == "jev":
                reset_jev_down()
                await probe_jev()
            else:
                reset_down("claude")
                reset_down("kimi")
                await probe_llm("claude")
            results[n] = {"ok": True, "note": "Key works"}
        except Exception as err:
            note = _friendly_key_error(err)
            results[n] = {"ok": False, "note": note}
            # A rejected key should show as unavailable straight away, not "live" until the first
            # demo click fails. One OpenRouter key serves both providers, so mark both down.
            if account_error(err):
                if n == "jev":
                    mark_jev_down(note)
                else:
                    mark_down("claude", note)
                    mark_down("kimi", note)

    await asyncio.gather(*(one(n) for n in KEY_NAMES if mine.get(n)))
    return {"results": results}


async def _probe(p: str) -> None:
    """One-token call that lets a bad key or spend cap surface immediately instead of on the first
    demo click."""
    if not usable(p):
        return
    try:
        await complete(PROVIDERS[p]["small"], "hi", max_tokens=1)
    except Exception:
        pass  # transient errors are fine here


async def _probe_jev_key() -> None:
    """Same idea for Jev: a rejected key is discovered at startup and shown as unavailable."""
    if jev_state()["state"] != "live":
        return
    try:
        await jev_call("ping", {"ok": Noul(instructions="Is this a greeting?")}, lambda a: 0, lambda: 0)
    except Exception:
        pass  # transient


# POST handlers: one endpoint per pipeline stage so the UI can animate each hop as it really happens.
POST: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "/api/route/decide": lambda b: decide(_text(b.get("prompt"), "prompt"), _provider(b)),
    "/api/route/answer": lambda b: complete(PROVIDERS[_provider(b)]["tiers"][_tier(b.get("tier"))], _text(b.get("prompt"), "prompt"), effort=_effort_for(_provider(b), _tier(b.get("tier")))),
    "/api/triage/jev": lambda b: _triage_jev(b),
    "/api/triage/llm": lambda b: triage_with_llm(_text(b.get("ticket"), "ticket"), _provider(b)),
    "/api/triage/reply": lambda b: draft_reply(_text(b.get("ticket"), "ticket"), _text(b.get("department"), "department"), _text(b.get("action"), "action"), _provider(b)),
    "/api/inbox/jev": lambda b: classify_email_with_jev(_text(b.get("email"), "email")),
    "/api/inbox/llm": lambda b: classify_email_with_llm(_text(b.get("email"), "email"), _provider(b)),
    "/api/feed/label": lambda b: label_post(_text(b.get("post"), "post")),
    "/api/titles/generate": lambda b: generate_titles(_text(b.get("topic"), "topic"), int(_num(b.get("n"), 8, 3, 16)), _provider(b)),
    "/api/titles/score": lambda b: score_title(_text(b.get("title"), "title"), _text(b.get("topic"), "topic")),
    "/api/providers/reset": lambda b: _provider_reset(b),
    "/api/budget": lambda b: _set_budget(b),
    "/api/keys/check": lambda b: _check_keys(),
    "/api/jev/reset": lambda b: _jev_reset(),
}


async def _triage_jev(b: dict[str, Any]) -> dict[str, Any]:
    jev = await triage_with_jev(_text(b.get("ticket"), "ticket"))
    return {"jev": jev, **decide_action(jev["triage"])}


async def _provider_reset(b: dict[str, Any]) -> dict[str, Any]:
    p = _provider(b)
    reset_down(p)
    await _probe(p)
    return await _status()


async def _set_budget(b: dict[str, Any]) -> dict[str, Any]:
    _provider(b)  # validate the field like the TS handler
    budget.set_cap(_num(_js_number(b.get("capUsd")) * 100, 50, 10, 5000) / 100, "openrouter")
    return await _status()


async def _jev_reset() -> dict[str, Any]:
    reset_jev_down()
    await _probe_jev_key()
    return await _status()


# Simple per-IP limiter for public hosting (RATE_LIMIT_PER_MIN, default 600 when HOSTED=1, off
# locally).
RATE = float(os.environ.get("RATE_LIMIT_PER_MIN") or (600 if hosted() else 0))
_hits: dict[str, list[float]] = {}


def _limited(request: Request) -> bool:
    if not RATE:
        return False
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip() if os.environ.get("TRUST_PROXY") else ""
    ip = fwd or (request.client.host if request.client else "unknown")
    now = time.time()
    if len(_hits) > 10_000:
        for k in [k for k, v in _hits.items() if v[1] < now]:
            del _hits[k]
    h = _hits.get(ip)
    if not h or h[1] < now:
        _hits[ip] = [1, now + 60]
        return False
    h[0] += 1
    return h[0] > RATE


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async def go() -> None:
        await asyncio.gather(*(_probe(p) for p in PROVIDERS), _probe_jev_key())
        print("  Probe:          " + " ".join([f"jev={jev_state()['state']}", *(f"{k}={provider_state(k)['state']}" for k in PROVIDERS)]))

    asyncio.create_task(go())
    yield


app = FastAPI(lifespan=_lifespan)


@app.middleware("http")
async def gate(request: Request, call_next):
    """Keys arrive as headers, are format-checked, and live only for this request. Rate limiting and
    security headers also live here."""
    if request.url.path.startswith("/api/"):
        if _limited(request):
            return _err(429, "Too many requests. Please slow down for a minute.")
        keys: dict[str, str] = {}
        for n in KEY_NAMES:
            v = request.headers.get(HEADER[n])
            if v is None or v == "":
                continue
            if not valid_key(v):
                return _err(400, f"The {n} key has an invalid format")
            keys[n] = v
        set_user_keys(keys)
    resp = await call_next(request)
    resp.headers["cache-control"] = "no-store"
    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    return resp


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def api(request: Request, path: str):
    p = "/api/" + path
    try:
        if request.method == "GET":
            if p == "/api/status":
                return await _status()
            if p == "/api/samples":
                return {"routerPrompts": ROUTER_PROMPTS, "tickets": TICKETS, "feedPosts": FEED_POSTS}
            if p == "/api/inbox/sample":
                n = _num(_js_number(request.query_params.get("n")), 60, 5, 500)
                return make_emails(int(n))
            raise HttpError(404, "Not found")
        handler = POST.get(p)
        if handler is None:
            raise HttpError(404, "Not found")
        return await handler(await _read_json(request))
    except HttpError as e:
        return _err(e.status, str(e))
    except Exception as err:
        _, raw = _status_and_message(err)
        message = redact(raw or "Internal error")
        print(message, file=sys.stderr)
        return _err(500, message)


@app.get("/{path:path}")
async def static_files(path: str):
    rel = os.path.normpath("index.html" if path in ("", "/") else path).lstrip("/\\")
    if rel.startswith(".."):
        return _err(403, "Forbidden")
    try:
        data = (PUBLIC_DIR / rel).read_bytes()
    except Exception:
        return _err(404, "Not found")
    return Response(content=data, media_type=MIME.get(os.path.splitext(rel)[1], "application/octet-stream"))


def main() -> None:
    port = int(os.environ.get("PORT", "3000"))
    print(f"Jev demos on http://localhost:{port}")
    print(f"  Jev (TypeSafe): {'key set' if has_jev_key() else 'SIMULATED (set TYPESAFE_API_KEY)'}")
    for k in PROVIDERS:
        print(f"  {PROVIDERS[k]['label']:<14}: {provider_state(k)['state']}")
    print(f"  Budget:         ${float(os.environ.get('DEMO_BUDGET_USD', '0.5')):.2f} per key (set DEMO_BUDGET_USD)")
    if hosted():
        print("  HOSTED mode:    server keys are ignored; visitors must bring their own")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
