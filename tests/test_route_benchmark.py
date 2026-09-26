"""The benchmark report must work with and without the optional CLM column."""

from benchmarks.route_benchmark import Sample, render_markdown, summarize


def _samples(router: str, latency: float, cost: float) -> list[Sample]:
    return [
        Sample(router, "lost my card", "card_management_agent", "card_management_agent",
               True, latency, 100, 10, cost),
        Sample(router, "balance", "account_management_agent", "fallback_agent",
               False, latency, 100, 10, cost),
    ]


def test_render_without_clm_has_two_router_columns():
    summaries = {
        "llm": summarize(_samples("llm", 1000, 0.0003)),
        "typesafe": summarize(_samples("typesafe", 200, 0.00003)),
    }
    md = render_markdown("google/gemini-2.5-flash", summaries, None, [])
    header = next(line for line in md.splitlines() if line.startswith("| Metric"))
    assert header == "| Metric | Prompt LLM routing | TypeSafe (Jev) routing | TypeSafe (Jev) routing vs LLM |"
    assert "Contrastive" not in md
    assert "| Latency p50 (ms) | 1000 | 200 | -80.0% |" in md


def test_render_with_clm_adds_third_column_and_zero_cost():
    summaries = {
        "llm": summarize(_samples("llm", 1000, 0.0003)),
        "typesafe": summarize(_samples("typesafe", 200, 0.00003)),
        "contrastive": summarize(_samples("contrastive", 20, 0.0)),
    }
    e2e = {
        "prompt": {"n": 2, "p50": 3000, "mean": 3000},
        "typesafe": {"n": 2, "p50": 1500, "mean": 1500},
        "contrastive": {"n": 2, "p50": 1200, "mean": 1200},
    }
    md = render_markdown("google/gemini-2.5-flash", summaries, e2e, [])
    assert "Contrastive LM routing vs LLM" in md
    assert "| Latency p50 (ms) | 1000 | 200 | 20 | -80.0% | -98.0% |" in md
    assert "| Cost / call (USD) | 0.000300 | 0.000030 | 0.000000 | -90.0% | -100.0% |" in md
    assert "| Latency p50 (ms) | 3000 | 1500 | 1200 | -50.0% | -60.0% |" in md
    assert "self-hosted so cost is reported as 0" in md


def test_render_llm_vs_clm_only_omits_typesafe():
    summaries = {
        "llm": summarize(_samples("llm", 1000, 0.0003)),
        "contrastive": summarize(_samples("contrastive", 20, 0.0)),
    }
    md = render_markdown("google/gemini-2.5-flash", summaries, None, [])
    header = next(line for line in md.splitlines() if line.startswith("| Metric"))
    assert header == "| Metric | Prompt LLM routing | Contrastive LM routing | Contrastive LM routing vs LLM |"
    assert "TypeSafe router:" not in md
    assert "| Latency p50 (ms) | 1000 | 20 | -98.0% |" in md
