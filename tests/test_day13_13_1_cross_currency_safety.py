from decimal import Decimal
from unittest.mock import patch

from app.document_answering import answer_with_selection
from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.table_lookup import FinancialCell


NORDEA = DocumentRecord(
    "nq3", "nordea.pdf", "Nordea", 2026, 3, "factbook", "EUR"
)
SEB = DocumentRecord(
    "sq3", "seb.pdf", "SEB", 2026, 3, "quarterly_report", "SEK"
)


def _cell(metric: str, value: str, unit: str, document: str, page: int) -> FinancialCell:
    return FinancialCell(
        metric=metric,
        period="Q326",
        scope="group",
        value=Decimal(value),
        unit=unit,
        document=document,
        page=page,
        row_label=metric.replace("_", " "),
        raw_value=value,
    )


def test_directional_cross_currency_question_explains_fx_requirement_before_lookup():
    question = "How many more million total assets did SEB report than Nordea in Q3 2026?"
    selection = DocumentSelection("compare", ("sq3", "nq3"))

    with patch("app.document_answering._one_cell") as lookup:
        result = answer_with_selection(question, selection, [NORDEA, SEB])

    lookup.assert_not_called()
    assert result["supported"] is False
    assert result["sources"] == []
    assert "exchange rate" in result["answer"].casefold()
    assert "EUR" in result["answer"] and "SEK" in result["answer"]


def test_ordinary_cross_currency_compare_for_non_net_profit_metric_still_fails_closed():
    question = "Compare Nordea and SEB Q3 2026 net interest income"
    selection = DocumentSelection("compare", ("nq3", "sq3"))
    cells = [
        _cell("net_interest_income", "2000", "EURm", "nordea.pdf", 7),
        _cell("net_interest_income", "20000", "SEKm", "seb.pdf", 5),
    ]

    with patch("app.document_answering._one_cell", side_effect=cells):
        result = answer_with_selection(question, selection, [NORDEA, SEB])

    assert result["supported"] is False
    assert result["sources"] == []
    assert "incompatible units" in result["answer"].casefold()


def test_explicit_side_by_side_allows_non_arithmetic_cross_currency_presentation():
    question = "Compare Nordea and SEB Q3 2026 net interest income side by side"
    selection = DocumentSelection("compare", ("nq3", "sq3"))
    cells = [
        _cell("net_interest_income", "2000", "EURm", "nordea.pdf", 7),
        _cell("net_interest_income", "20000", "SEKm", "seb.pdf", 5),
    ]

    with patch("app.document_answering._one_cell", side_effect=cells):
        result = answer_with_selection(question, selection, [NORDEA, SEB])

    assert result["supported"] is True
    assert len(result["sources"]) == 2
    assert "No subtraction" in result["answer"]
