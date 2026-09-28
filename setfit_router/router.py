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


class Router(Protocol):
    def route(self, text: str) -> RouteDecision: ...


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

    def predict(self, texts: Sequence[str]) -> list[tuple[str, float]]:
        """Return ``(label, probability)`` for each text at the model's own granularity."""
        model = self._load()
        probs = model.predict_proba(list(texts), as_numpy=True)
        out = []
        for row in probs:
            idx = int(row.argmax())
            out.append((self.meta.labels[idx], float(row[idx])))
        return out

    def route(self, text: str) -> RouteDecision:
        start = time.perf_counter()
        ((label, confidence),) = self.predict([text])
        latency_ms = (time.perf_counter() - start) * 1000
        low = confidence < self.confidence_threshold
        return RouteDecision(
            agent=FALLBACK if low else self.meta.to_agent(label),
            confidence=confidence,
            predicted_label=label,
            intent=label if self.meta.granularity == "intent" else None,
            granularity=self.meta.granularity,
            low_confidence=low,
            latency_ms=latency_ms,
        )
