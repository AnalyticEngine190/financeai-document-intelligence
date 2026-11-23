"""Question understanding tests: no PDF, Chroma, or Ollama required."""
import pytest
from app.question_parser import parse_question


@pytest.mark.parametrize("question,metric,periods,operation", [
    ("During July-September 2026, how much interest income did Nordea retain after deducting interest expenses?", "net_interest_income", ("Q326",), "lookup"),
    ("For the quarter labelled Q326, what was the group-wide net amount from fees and commissions?", "net_fee_income", ("Q326",), "lookup"),
    ("What was Nordea's bottom-line profit for the three months ended September 2026?", "net_profit", ("Q326",), "lookup"),
    ("How large was Nordea's consolidated balance sheet at 30 September 2026?", "total_assets", ("Q326",), "lookup"),
    ("How much did Nordea manage on behalf of investors in Q3 2026?", "aum", ("Q326",), "lookup"),
    ("What was the ratio of costs to income with regulatory fees excluded in Q3 2026?", "cost_to_income_ex_fees", ("Q326",), "lookup"),
    ("What was net profit excluding items affecting comparability in Q1 2026, rather than reported net profit?", "net_profit_adjusted", ("Q126",), "lookup"),
    ("What was return on equity with amortised regulatory fees in Q3 2026, not ordinary ROE?", "roe_amortised", ("Q326",), "lookup"),
    ("How many EUR million did net interest income rise from Q2 2026 to Q3 2026?", "net_interest_income", ("Q226", "Q326"), "absolute_change"),
    ("By how many EUR million did total operating income decline between Q2 2026 and Q3 2026?", "total_operating_income", ("Q226", "Q326"), "decline"),
    ("How much did total operating income grow year-over-year from Q3 2025 to Q3 2026, as a percentage?", "total_operating_income", ("Q325", "Q326"), "percentage_change"),
    ("How many percentage points did return on equity increase from Q1 2026 to Q3 2026?", "roe", ("Q126", "Q326"), "percentage_points"),
    ("Using the 10-year income overview, what was full-calendar-year 2024 operating profit?", "operating_profit", ("2024",), "lookup"),
    ("Read the column headed Q425, not Q326: what was the group's net earnings from fees and commissions?", "net_fee_income", ("Q425",), "lookup"),
])
def test_interpretation(question, metric, periods, operation):
    intent = parse_question(question)
    assert (intent.metric, intent.periods, intent.operation) == (metric, periods, operation)
    assert intent.uncertain is None
    assert intent.structured is True


def test_negations_do_not_replace_requested_capital_ratio():
    i = parse_question("Do not give CET1 or total capital. What was the Tier 1 capital ratio in Q4 2025?")
    assert i.metric == "tier1_ratio" and i.periods == ("Q425",) and i.structured


def test_lcr_excluded_currencies_are_not_selected():
    i = parse_question("For Q3 2026, give the euro-only LCR, not combined and not dollar-only LCR")
    assert i.currency_scopes == ("EUR",)
    assert i.metric == "lcr" and i.structured


def test_two_currencies_and_one_period():
    i = parse_question("In Q3 2026 by how many percentage points did the USD LCR exceed the EUR LCR?")
    assert i.currency_scopes == ("USD", "EUR")
    assert i.operation == "currency_difference"
    assert i.periods == ("Q326",)


def test_business_countries_and_multi_segment_scope():
    i = parse_question("Within Personal Banking, how much Q3 2026 net interest income was attributed to Finland?")
    assert i.scopes == ("personal banking",) and i.country == "Finland"
    j = parse_question("How many EUR million more total income did Personal Banking have than Business Banking in Q3 2026?")
    assert j.scopes == ("personal banking", "business banking")
    assert j.operation == "scope_difference"


def test_ambiguous_income_requires_clarification():
    for question in ("How much income did Nordea make in Q3 2026?", "How much money did Nordea make in the third quarter of 2026?"):
        assert parse_question(question).needs_clarification


def test_legacy_single_cell_paths_remain_legacy():
    for q in (
        "What was the combined LCR in Q3 2026?",
        "What was Nordea's total operating income in Q3 2026?",
        "What was Personal Banking's total income in Q3 2026?",
    ):
        assert parse_question(q).structured is False


def test_incomplete_comparison_fails_closed():
    intent = parse_question("How much did Nordea's total assets increase in Q3 2026?")
    assert intent.structured and intent.uncertain
