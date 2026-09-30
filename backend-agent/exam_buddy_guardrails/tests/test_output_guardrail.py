"""The reply must not expose the prompt or a dated memory quote."""

from exam_buddy_guardrails.guardrails.output_guardrail import apply_output_guardrail


def test_dated_memory_disclosure_is_softened():
    raw = (
        "You previously told me on September 12 that you struggle with quadratic equations. "
        "The quadratic formula is x = (-b ± sqrt(b² - 4ac)) / 2a."
    )
    cleaned = apply_output_guardrail(raw)
    assert "September 12" not in cleaned
    assert "quadratic formula" in cleaned


def test_system_prompt_leak_is_removed():
    raw = (
        "STUDENT MEMORY CONTEXT says the graph_nodes hold the answer. "
        "Force equals mass times acceleration."
    )
    cleaned = apply_output_guardrail(raw)
    assert "graph_nodes" not in cleaned.casefold()
    assert "memory context" not in cleaned.casefold()
    assert "acceleration" in cleaned
