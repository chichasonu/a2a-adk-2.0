"""Tests for the predictive card-care POC (embedded fakeredis, no LLM calls)."""

from __future__ import annotations

import os

os.environ.setdefault("USE_FAKEREDIS", "true")
os.environ.setdefault("CARE_FORCE_RULES", "true")

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from predictive_care.config import settings
from predictive_care.main import app
from predictive_care.memory import RedisMemoryService
from predictive_care.predictor import IssuePredictor
from predictive_care.service import CareService
from predictive_care.signals import Signal
from predictive_care.signals import classify_issue_keywords

USER = "test_user"


@pytest.fixture
async def care() -> CareService:
    service = CareService()
    await service.reset(USER, forget_memory=True)
    yield service
    await service.reset(USER, forget_memory=True)
    await service.close()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        # Lifespan is not run by ASGITransport, so build the service explicitly.
        app.state.care = CareService()
        await app.state.care.reset("api_user", forget_memory=True)
        yield http
        await app.state.care.close()


async def test_keyword_classification() -> None:
    assert "blocked" in classify_issue_keywords("my debit card is blocked")
    assert "lost_or_stolen" in classify_issue_keywords("lost my card")
    assert classify_issue_keywords("hello there") == {}


async def test_memory_roundtrip_and_search() -> None:
    memory = RedisMemoryService()
    await memory.clear(user_id="mem_user")
    await memory.remember(
        user_id="mem_user", text="Replaced a stolen debit card ending 4821", kind="resolution"
    )
    await memory.remember(user_id="mem_user", text="Enrolled in paperless statements")

    records = await memory.list_records(user_id="mem_user")
    assert len(records) == 2

    hits = await memory.search_records(user_id="mem_user", query="stolen card replacement")
    assert hits and "stolen debit card" in hits[0]["text"]

    response = await memory.search_memory(
        app_name=settings.APP_NAME, user_id="mem_user", query="stolen card"
    )
    assert response.memories

    await memory.update_profile(
        user_id="mem_user", updates={"preferred_resolution": "replace"}
    )
    assert (await memory.get_profile(user_id="mem_user"))["preferred_resolution"] == "replace"
    await memory.clear(user_id="mem_user")
    await memory.close()


async def test_no_intervention_without_friction(care: CareService) -> None:
    result = await care.ingest_signal(
        Signal(user_id=USER, type="page_view", value="accounts")
    )
    prediction = result["prediction"]
    assert prediction["should_intervene"] is False
    assert prediction["confidence"] < settings.INTERVENE_THRESHOLD


async def test_search_plus_repeated_card_views_triggers_offer(care: CareService) -> None:
    await care.ingest_signal(Signal(user_id=USER, type="search", value="debit card blocked"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    result = await care.ingest_signal(
        Signal(user_id=USER, type="page_view", value="card_management")
    )

    prediction = result["prediction"]
    assert prediction["should_intervene"] is True
    assert prediction["issue_type"] == "blocked"
    assert prediction["confidence"] >= settings.INTERVENE_THRESHOLD
    assert [a["action"] for a in prediction["actions"]][0] == "unlock"
    assert {a["action"] for a in prediction["actions"]} == {
        "replace",
        "activate",
        "dispute",
        "unlock",
    }
    assert any(r["code"] == "repeated_card_page" for r in prediction["reasons"])


async def test_resolution_is_remembered_and_reorders_next_offer(care: CareService) -> None:
    result = await care.execute_action(user_id=USER, action="replace", reason="stolen")
    assert result["status"] == "completed"

    profile = await care.memory.get_profile(user_id=USER)
    assert profile["preferred_resolution"] == "replace"
    assert profile["accepted_offers"] == 1

    records = await care.memory.list_records(user_id=USER)
    assert any(r["kind"] == "resolution" for r in records)

    # A fresh visit: memory should promote the previously used resolution.
    await care.signals.clear(USER)
    await care.ingest_signal(Signal(user_id=USER, type="search", value="card blocked"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    prediction = await care.get_prediction(USER, respect_cooldown=False)
    assert prediction.actions[0].action == "replace"
    assert prediction.actions[0].reason == (
        "Recalled from long-term memory as your usual choice"
    )


async def test_memory_of_dismissal_suppresses_low_confidence_offer(
    care: CareService,
) -> None:
    await care.record_offer_response(USER, accepted=False)
    await care.ingest_signal(Signal(user_id=USER, type="search", value="card blocked"))
    result = await care.ingest_signal(
        Signal(user_id=USER, type="page_view", value="card_management")
    )
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    prediction = await care.get_prediction(USER, respect_cooldown=False)
    assert prediction.should_intervene is False
    assert prediction.suppressed_by == "memory_previously_dismissed"
    assert result["prediction"]["issue_type"] == "blocked"


async def test_dismissal_survives_high_confidence_and_yields_to_escalation(
    care: CareService,
) -> None:
    await care.memory.update_profile(
        user_id=USER, updates={"issue_history": {"blocked": 3}, "accepted_offers": 2}
    )
    await care.record_offer_response(USER, accepted=False)
    await care.ingest_signal(Signal(user_id=USER, type="search", value="card blocked"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))

    high = await care.get_prediction(USER, respect_cooldown=False)
    assert high.confidence >= 0.85
    assert high.should_intervene is False
    assert high.suppressed_by == "memory_previously_dismissed"

    # A declined payment is a new, hard signal: re-prompting is allowed again.
    await care.ingest_signal(
        Signal(user_id=USER, type="transaction_declined", value="atm withdrawal")
    )
    escalated = await care.get_prediction(USER, respect_cooldown=False)
    assert escalated.should_intervene is True


async def test_offer_never_pops_on_non_card_page(care: CareService) -> None:
    await care.ingest_signal(Signal(user_id=USER, type="search", value="card blocked"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    await care.record_offer_response(USER, accepted=False)

    off_card = await care.ingest_signal(
        Signal(user_id=USER, type="page_view", value="accounts")
    )
    assert off_card["prediction"]["should_intervene"] is False


async def test_cooldown_suppresses_repeat_prompt(care: CareService) -> None:
    await care.ingest_signal(Signal(user_id=USER, type="search", value="card blocked"))
    await care.ingest_signal(Signal(user_id=USER, type="page_view", value="card_management"))
    first = await care.ingest_signal(
        Signal(user_id=USER, type="page_view", value="card_management")
    )
    assert first["prediction"]["should_intervene"] is True

    second = await care.get_prediction(USER, respect_cooldown=True)
    assert second.should_intervene is False
    assert second.suppressed_by == "cooldown"


async def test_rule_engine_chat_resolves_and_updates_card(care: CareService) -> None:
    reply = await care.chat(user_id=USER, message="please unlock my card")
    assert reply["engine"] == "rule-engine"
    assert "unlocked" in reply["response"].lower()
    card = await care.cards.get_card(USER)
    assert card.status == "active"


async def test_dispute_creates_case_and_marks_transaction(care: CareService) -> None:
    result = await care.execute_action(user_id=USER, action="dispute")
    assert result["case_id"].startswith("DSP-")
    card = await care.cards.get_card(USER)
    assert any(t.status == "disputed" for t in card.transactions)


async def test_predictor_infers_issue_from_card_status_only() -> None:
    memory = RedisMemoryService()
    await memory.clear(user_id="status_user")
    predictor = IssuePredictor(memory)
    from predictive_care.signals import SignalFeatures

    features = SignalFeatures(user_id="status_user", window_seconds=900)
    prediction = await predictor.predict(
        user_id="status_user", features=features, card_status="inactive"
    )
    assert prediction.issue_type == "not_activated"
    assert prediction.should_intervene is False
    assert prediction.actions[0].action == "activate"
    await memory.clear(user_id="status_user")
    await memory.close()


async def test_api_journey(client: AsyncClient) -> None:
    user = "api_user"
    assert (await client.get("/health")).json()["status"] == "ok"
    assert (await client.get("/api/config")).json()["engine"] == "rule-engine"

    await client.post(
        "/api/signals",
        json={"user_id": user, "type": "search", "value": "debit card blocked"},
    )
    await client.post(
        "/api/signals", json={"user_id": user, "type": "page_view", "value": "card_management"}
    )
    response = await client.post(
        "/api/signals", json={"user_id": user, "type": "page_view", "value": "card_management"}
    )
    prediction = response.json()["prediction"]
    assert prediction["should_intervene"] is True

    action = await client.post(
        "/api/actions", json={"user_id": user, "action": "unlock"}
    )
    assert action.json()["status"] == "completed"
    assert (await client.get(f"/api/cards/{user}")).json()["status"] == "active"

    memory = (await client.get(f"/api/memory/{user}")).json()
    assert memory["profile"]["preferred_resolution"] == "unlock"

    search = (await client.get(f"/api/memory/{user}/search", params={"q": "unlock"})).json()
    assert search["results"]

    bad = await client.post("/api/actions", json={"user_id": user, "action": "nope"})
    assert bad.status_code == 400

    reset = await client.post("/api/reset", json={"user_id": user, "forget_memory": True})
    assert reset.json()["memory_cleared"] is True
    assert (await client.get(f"/api/memory/{user}")).json()["records"] == []
