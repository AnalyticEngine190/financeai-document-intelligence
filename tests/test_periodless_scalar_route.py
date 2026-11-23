"""Regression coverage for undated scalar financial questions."""

from app.question_parser import parse_question


def test_periodless_cet1_uses_legacy_grounded_fallback():
    intent = parse_question("What is the CET1 ratio?")
    assert intent.metric == "cet1_ratio"
    assert intent.periods == ()
    assert intent.operation == "lookup"
    assert intent.structured is False


def test_explicit_period_cet1_remains_supported():
    intent = parse_question("What was the CET1 capital ratio in Q3 2026?")
    assert intent.metric == "cet1_ratio"
    assert intent.periods == ("Q326",)
    assert intent.needs_clarification is False


def test_comparison_still_uses_verified_structured_route():
    intent = parse_question(
        "How much did net interest income rise from Q2 2026 to Q3 2026?"
    )
    assert intent.structured is True
    assert intent.operation == "absolute_change"
    assert intent.periods == ("Q226", "Q326")


def test_qualified_currency_does_not_fall_back_without_period():
    intent = parse_question("What is the euro-only LCR?")
    assert intent.metric == "lcr"
    assert intent.currency_scopes == ("EUR",)
    assert intent.structured is True
