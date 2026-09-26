"""BERT-based intent classifier used by the supervisor route graph.

The fine-tuned model produced by ``scripts/train_intent_classifier.py`` is
loaded lazily from ``settings.INTENT_MODEL_PATH``. If the directory is absent
(or ``torch``/``transformers`` are not installed) the classifier reports itself
as unavailable and callers fall back to keyword routing.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

FALLBACK_LABEL = "fallback_agent"

AGENT_LABELS: tuple[str, ...] = (
    "card_management_agent",
    "account_management_agent",
    "transfer_to_human_agent",
    "answer_hub_agent",
    "transaction_agent",
    "rates_fees_limits_management_agent",
    "spending_insights_agent",
    FALLBACK_LABEL,
)


class IntentClassifier:
    """Lazily loaded ``BertForSequenceClassification`` wrapper."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        confidence_threshold: float | None = None,
    ) -> None:
        self.model_path = Path(model_path or settings.INTENT_MODEL_PATH)
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.INTENT_CONFIDENCE_THRESHOLD
        )
        self._lock = threading.Lock()
        self._loaded = False
        self._model = None
        self._tokenizer = None
        self._id2label: dict[int, str] = {}
        self._device = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def model_dir_exists(self) -> bool:
        return (self.model_path / "config.json").exists()

    def load(self) -> bool:
        """Load the model once. Returns True if the classifier is usable."""
        if self._loaded:
            return self._model is not None
        with self._lock:
            if self._loaded:
                return self._model is not None
            self._loaded = True
            if not self.model_dir_exists():
                logger.warning(
                    "Intent model directory %s not found; using keyword routing",
                    self.model_path,
                )
                return False
            try:
                import torch
                from transformers import BertForSequenceClassification, BertTokenizerFast
            except ImportError:
                logger.warning(
                    "torch/transformers not installed; using keyword routing"
                )
                return False
            try:
                self._tokenizer = BertTokenizerFast.from_pretrained(self.model_path)
                self._model = BertForSequenceClassification.from_pretrained(self.model_path)
                self._model.eval()
                self._device = torch.device(
                    "cuda" if torch.cuda.is_available() else "cpu"
                )
                self._model.to(self._device)
                self._id2label = self._read_label_map()
                logger.info(
                    "Loaded intent classifier from %s on %s (%d labels)",
                    self.model_path,
                    self._device,
                    len(self._id2label),
                )
                return True
            except Exception:
                logger.exception("Failed to load intent classifier from %s", self.model_path)
                self._model = None
                self._tokenizer = None
                return False

    def _read_label_map(self) -> dict[int, str]:
        label_map_path = self.model_path / "label_map.json"
        if label_map_path.exists():
            data = json.loads(label_map_path.read_text(encoding="utf-8"))
            return {int(k): v for k, v in data["id2label"].items()}
        return {int(k): v for k, v in self._model.config.id2label.items()}

    @property
    def available(self) -> bool:
        return self.load()

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, text: str) -> tuple[str, float]:
        """Return the raw ``(label, confidence)`` without threshold handling."""
        if not self.load():
            raise RuntimeError("Intent classifier is not available")
        import torch

        encoded = self._tokenizer(
            text,
            truncation=True,
            max_length=64,
            return_tensors="pt",
        ).to(self._device)
        with torch.no_grad():
            logits = self._model(**encoded).logits
        probs = torch.softmax(logits, dim=-1)[0]
        confidence, idx = probs.max(dim=-1)
        return self._id2label[int(idx)], float(confidence)

    def classify(self, text: str) -> tuple[str, float]:
        """Classify ``text`` into an agent label.

        Predictions whose max softmax probability is below
        ``confidence_threshold`` are routed to ``fallback_agent``.
        """
        label, confidence = self.predict(text)
        if confidence < self.confidence_threshold:
            logger.info(
                "Intent %s below threshold (%.3f < %.2f); routing to %s",
                label,
                confidence,
                self.confidence_threshold,
                FALLBACK_LABEL,
            )
            return FALLBACK_LABEL, confidence
        return label, confidence


intent_classifier = IntentClassifier()


def classify(text: str) -> tuple[str, float]:
    """Module-level convenience wrapper around the shared classifier.

    Falls back to keyword routing (``agents.route_by_keyword_rules``) when the
    model directory is absent; in that case confidence is reported as ``0.0``.
    """
    if intent_classifier.available:
        return intent_classifier.classify(text)
    from .agents import route_by_keyword_rules

    return route_by_keyword_rules(text), 0.0
