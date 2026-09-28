from google.adk.models.lite_llm import LiteLlm

from setfit_router.agents import resolve_model
from setfit_router.config import DEFAULT_AGENT_MODEL


def test_openrouter_models_use_litellm():
    model = resolve_model(DEFAULT_AGENT_MODEL)
    assert isinstance(model, LiteLlm)
    assert model.model == "openrouter/google/gemini-3.5-flash-lite"


def test_native_adk_model_strings_pass_through():
    assert resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"
