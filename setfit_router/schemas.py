"""Strict request/response models for the typed classification API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from setfit_router.router import Classification

AgentName = Literal["cards", "transactions", "accounts", "fallback"]
Granularity = Literal["agent", "intent"]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ClassifyRequest(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    top_k: Annotated[int, Field(ge=1, le=10)] = 1


class ModelInfo(StrictModel):
    name: str = Field(description="Router model directory, e.g. setfit-agent or setfit-minilm-intent")
    base_model: str = Field(description="Sentence-embedding model the SetFit router was fine-tuned from")
    granularity: Granularity
    num_labels: int = Field(ge=1)


class Candidate(StrictModel):
    label: str = Field(description="Raw model label: a BANKING77 intent or an agent name")
    agent: AgentName
    confidence: Probability


class ClassifyResponse(StrictModel):
    agent: AgentName = Field(description="Sub-agent the message is routed to")
    intent: str | None = Field(description="BANKING77 intent; null for agent-granularity models")
    predicted_label: str
    confidence: Probability
    low_confidence: bool = Field(description="Confidence below threshold, so routed to fallback")
    granularity: Granularity
    model: ModelInfo
    candidates: list[Candidate] = Field(description="Top-k labels, most probable first")
    latency_ms: Annotated[float, Field(ge=0.0)]

    @classmethod
    def from_classification(cls, c: Classification) -> ClassifyResponse:
        d = c.decision
        return cls.model_validate(
            {
                "agent": d.agent,
                "intent": d.intent,
                "predicted_label": d.predicted_label,
                "confidence": _prob(d.confidence),
                "low_confidence": d.low_confidence,
                "granularity": d.granularity,
                "model": asdict(c.info),
                "candidates": [{**asdict(x), "confidence": _prob(x.confidence)} for x in c.candidates],
                "latency_ms": d.latency_ms,
            },
            strict=False,
        )


def _prob(p: float) -> float:
    """Clamp float32 rounding (e.g. 1.0000001) into [0, 1]."""
    return min(1.0, max(0.0, float(p)))
