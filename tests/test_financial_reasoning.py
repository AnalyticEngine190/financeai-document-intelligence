"""Synthetic calculation tests; figures here never enter application logic."""
from decimal import Decimal
import pytest
from app.financial_reasoning import reason_over_cells
from app.question_parser import parse_question
from app.table_lookup import FinancialCell


def cell(value, period, metric="net_interest_income", scope="group", unit="EURm", page=7):
    return FinancialCell(metric, period, scope, Decimal(str(value)), unit, "source.pdf", page, "row", str(value))


def test_absolute_change_and_source_citation():
    i = parse_question("By how many EUR million did net interest income rise from Q2 2026 to Q3 2026?")
    r = reason_over_cells(i, [cell(125, "Q226"), cell(148, "Q326")])
    assert r.result == Decimal(23) and r.sources == ({"document": "source.pdf", "page": 7},)


def test_decline_is_positive_magnitude():
    i = parse_question("By how many EUR million did total operating income decline between Q2 2026 and Q3 2026?")
    r = reason_over_cells(i, [cell(160, "Q226", metric="total_operating_income"), cell(150, "Q326", metric="total_operating_income")])
    assert r.result == 10


def test_percent_change_rounded():
    i = parse_question("How much did total operating income grow from Q3 2025 to Q3 2026 as a percentage?")
    r = reason_over_cells(i, [cell(2910, "Q325", metric="total_operating_income"), cell(2968, "Q326", metric="total_operating_income")])
    assert r.answer.startswith("2.0%")


def test_percentage_points_not_percentage_change():
    i = parse_question("How many percentage points did return on equity increase from Q1 2026 to Q3 2026?")
    r = reason_over_cells(i, [cell("13.4", "Q126", metric="roe", unit="percent"), cell("15.3", "Q326", metric="roe", unit="percent")])
    assert r.result == Decimal("1.9") and "percentage points" in r.answer


def test_scope_difference_cites_both_pages():
    i = parse_question("How much more total income did Personal Banking have than Business Banking in Q3 2026?")
    r = reason_over_cells(i, [cell(150, "Q326", metric="segment_total_income", scope="personal banking", page=13),
                              cell(120, "Q326", metric="segment_total_income", scope="business banking", page=20)])
    assert r.result == 30 and len(r.sources) == 2


def test_units_and_missing_operands_fail_closed():
    i = parse_question("How many EUR million did net interest income rise from Q2 2026 to Q3 2026?")
    with pytest.raises(ValueError):
        reason_over_cells(i, [cell(1, "Q226")])
    with pytest.raises(ValueError):
        reason_over_cells(i, [cell(1, "Q226", unit="EURbn"), cell(2, "Q326", unit="EURm")])


def test_no_division_by_zero():
    i = parse_question("How much did total operating income grow from Q3 2025 to Q3 2026 as a percentage?")
    with pytest.raises(ValueError):
        reason_over_cells(i, [cell(0, "Q325", metric="total_operating_income"),
                              cell(100, "Q326", metric="total_operating_income")])
