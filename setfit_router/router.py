"""Loads a trained SetFit router and maps predictions to a sub-agent."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

META_FILE = "router_meta.json"
FALLBACK = "fallback"


@dataclass(frozen=True)
class RouteDecision:
    agent: str
    confidence: float
    predicted_label: str
    intent: str | None
    granularity: str
    low_confidence: bool
    latency_ms: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Candidate:
    label: str
    agent: str
    confidence: float


@dataclass(frozen=True)
class RouterInfo:
    name: str
    base_model: str
    granularity: str
    num_labels: int


@dataclass(frozen=True)
class Classification:
    decision: RouteDecision
    candidates: tuple[Candidate, ...]
    info: RouterInfo


class Router(Protocol):
    def route(self, text: str) -> RouteDecision: ...

    def classify(self, text: str, top_k: int = 1) -> Classification: ...


@dataclass(frozen=True)
class RouterMeta:
    granularity: str
    labels: list[str]
    intent_to_agent: dict[str, str]
    base_model: str

    @classmethod
    def load(cls, model_dir: Path) -> RouterMeta:
        data = json.loads((model_dir / META_FILE).read_text())
        return cls(
            granularity=data["granularity"],
            labels=list(data["labels"]),
            intent_to_agent=dict(data["intent_to_agent"]),
            base_model=data.get("base_model", ""),
        )

    def save(self, model_dir: Path, **extra: object) -> None:
        payload = {**asdict(self), **extra}
        (model_dir / META_FILE).write_text(json.dumps(payload, indent=2, sort_keys=True))

    def to_agent(self, label: str) -> str:
        if self.granularity == "intent":
            return self.intent_to_agent.get(label, FALLBACK)
        return label


class SetFitRouter:
    """Thread-safe lazy wrapper around a SetFit model directory written by ``training/train.py``."""

    def __init__(self, model_dir: str | Path, confidence_threshold: float = 0.0) -> None:
        self.model_dir = Path(model_dir)
        self.confidence_threshold = confidence_threshold
        self.meta = RouterMeta.load(self.model_dir)
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from setfit import SetFitModel

                    self._model = SetFitModel.from_pretrained(str(self.model_dir))
        return self._model

    def warmup(self) -> None:
        self.predict(["warmup"])

    @property
    def info(self) -> RouterInfo:
        return RouterInfo(
            name=self.model_dir.name,
            base_model=self.meta.base_model,
            granularity=self.meta.granularity,
            num_labels=len(self.meta.labels),
        )

    def predict_top_k(self, texts: Sequence[str], k: int) -> list[list[tuple[str, float]]]:
        """Return the ``k`` most probable ``(label, probability)`` pairs per text, best first."""
        model = self._load()
        probs = model.predict_proba(list(texts), as_numpy=True, show_progress_bar=False)
        return [
            [(self.meta.labels[int(i)], float(row[int(i)])) for i in row.argsort()[::-1][:k]] for row in probs
        ]

    def predict(self, texts: Sequence[str]) -> list[tuple[str, float]]:
        """Return ``(label, probability)`` for each text at the model's own granularity."""
        return [top[0] for top in self.predict_top_k(texts, 1)]

    def classify(self, text: str, top_k: int = 1) -> Classification:
        start = time.perf_counter()
        (top,) = self.predict_top_k([text], max(1, top_k))
        latency_ms = (time.perf_counter() - start) * 1000
        label, confidence = top[0]
        low = confidence < self.confidence_threshold
        decision = RouteDecision(
            agent=FALLBACK if low else self.meta.to_agent(label),
            confidence=confidence,
            predicted_label=label,
            intent=label if self.meta.granularity == "intent" else None,
            granularity=self.meta.granularity,
            low_confidence=low,
            latency_ms=latency_ms,
        )
        candidates = tuple(Candidate(lbl, self.meta.to_agent(lbl), conf) for lbl, conf in top)
        return Classification(decision=decision, candidates=candidates, info=self.info)

    def route(self, text: str) -> RouteDecision:
        return self.classify(text).decision
