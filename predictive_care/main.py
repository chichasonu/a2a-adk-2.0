"""FastAPI application for the predictive card-care POC."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic import Field

from .config import settings
from .predictor import ACTION_LABELS
from .service import CareService
from .signals import Signal

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "ui"


class SignalRequest(BaseModel):
    """Behavioural signal emitted by the customer-facing app."""

    user_id: str = Field(..., min_length=1)
    type: str
    value: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    session_id: str | None = None


class ActionRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    action: str
    transaction_id: str = ""
    reason: str | None = None


class OfferResponseRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    accepted: bool
    action: str | None = None


class ResetRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    forget_memory: bool = True


def _memory_target() -> str:
    """Human-readable description of the selected long-term memory version."""
    backend = settings.MEMORY_BACKEND
    if backend == "mongo":
        return f"mongo {settings.MONGO_URL}/{settings.MONGO_DB}"
    if backend == "chroma":
        return (
            f"chroma vector {settings.CHROMA_HOST}:{settings.CHROMA_PORT}"
            f"/{settings.CHROMA_COLLECTION}"
        )
    if backend in ("graph", "neo4j"):
        return f"neo4j graph {settings.NEO4J_URL}/{settings.NEO4J_DATABASE}"
    return "redis"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the care service once per process."""
    app.state.care = CareService()
    logger.info(
        "Predictive care ready (engine=%s, redis=%s, long-term memory=%s)",
        "adk-agent" if settings.llm_enabled else "rule-engine",
        "fakeredis" if settings.USE_FAKEREDIS else settings.REDIS_URL,
        _memory_target(),
    )
    yield
    await app.state.care.close()


app = FastAPI(
    title="Predictive Card Care",
    description=(
        "Long-term-memory agentic POC: predicts a debit card service issue from "
        "customer behaviour and proactively resolves it."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def _care(request: Request) -> CareService:
    return request.app.state.care


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "engine": "adk-agent" if settings.llm_enabled else "rule-engine",
    }


@app.get("/api/config")
async def read_config() -> dict[str, Any]:
    """Expose the demo-relevant configuration to the UI."""
    return {
        "app_name": settings.APP_NAME,
        "engine": "adk-agent" if settings.llm_enabled else "rule-engine",
        "model": settings.GEMINI_MODEL if settings.llm_enabled else None,
        "memory_backend": settings.MEMORY_BACKEND,
        "intervene_threshold": settings.INTERVENE_THRESHOLD,
        "repeat_view_threshold": settings.REPEAT_VIEW_THRESHOLD,
        "signal_window_seconds": settings.SIGNAL_WINDOW_SECONDS,
        "actions": ACTION_LABELS,
    }


@app.post("/api/signals")
async def post_signal(request: Request, body: SignalRequest) -> dict[str, Any]:
    """Ingest one behavioural signal and return the refreshed prediction."""
    try:
        signal = Signal(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _care(request).ingest_signal(signal)


@app.get("/api/prediction/{user_id}")
async def get_prediction(
    request: Request,
    user_id: str,
    respect_cooldown: bool = Query(True),
) -> dict[str, Any]:
    """Re-score the user's friction without emitting a new signal."""
    prediction = await _care(request).get_prediction(
        user_id, respect_cooldown=respect_cooldown
    )
    return prediction.model_dump()


@app.get("/api/signals/{user_id}")
async def get_signals(
    request: Request, user_id: str, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    """Signal timeline plus the aggregated features fed to the predictor."""
    care = _care(request)
    features = await care.signals.features(user_id)
    return {
        "timeline": await care.signals.timeline(user_id, limit=limit),
        "features": features.model_dump(),
    }


@app.post("/api/chat")
async def post_chat(request: Request, body: ChatRequest) -> dict[str, Any]:
    """Talk to the care agent (ADK agent, or rule engine without an API key)."""
    return await _care(request).chat(
        user_id=body.user_id, message=body.message, session_id=body.session_id
    )


@app.post("/api/actions")
async def post_action(request: Request, body: ActionRequest) -> dict[str, Any]:
    """Execute a resolution action chosen from the proactive prompt."""
    try:
        return await _care(request).execute_action(
            user_id=body.user_id,
            action=body.action,
            transaction_id=body.transaction_id,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/offer-response")
async def post_offer_response(
    request: Request, body: OfferResponseRequest
) -> dict[str, Any]:
    """Record acceptance/dismissal of the proactive prompt in long-term memory."""
    return await _care(request).record_offer_response(
        body.user_id, accepted=body.accepted, action=body.action
    )


@app.get("/api/memory/{user_id}")
async def get_memory(
    request: Request, user_id: str, limit: int = Query(25, ge=1, le=200)
) -> dict[str, Any]:
    """Inspect the customer's long-term memory (profile + records)."""
    return await _care(request).memory_view(user_id, limit=limit)


@app.get("/api/memory/{user_id}/search")
async def search_memory(
    request: Request, user_id: str, q: str = Query(..., min_length=1)
) -> dict[str, Any]:
    """Lexical search over the customer's long-term memory."""
    records = await _care(request).memory.search_records(user_id=user_id, query=q)
    return {"query": q, "results": records}


@app.get("/api/cards/{user_id}")
async def get_card(request: Request, user_id: str) -> dict[str, Any]:
    """Current card state as shown on the card management page."""
    return await _care(request).cards.snapshot(user_id)


@app.post("/api/reset")
async def post_reset(request: Request, body: ResetRequest) -> dict[str, Any]:
    """Reset the demo. ``forget_memory=false`` keeps long-term memory."""
    return await _care(request).reset(body.user_id, forget_memory=body.forget_memory)


if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")


def build_app() -> FastAPI:
    return app
