from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import sys

# The comparison module only needs the Chroma/embedding symbols at import time;
# no database or model is used by these isolated, mocked-cell tests.
try:
    import chromadb  # noqa: F401
except ImportError:
    sys.modules.setdefault("chromadb", SimpleNamespace(PersistentClient=object))
try:
    import sentence_transformers  # noqa: F401
except ImportError:
    sys.modules.setdefault("sentence_transformers", SimpleNamespace(SentenceTransformer=object))

from app.document_answering import answer_with_selection
from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.table_lookup import FinancialCell


def _cell(*, metric: str, value: str, unit: str, document: str, page: int) -> FinancialCell:
    return FinancialCell(
        metric=metric, period="Q326", scope="group", value=Decimal(value),
        unit=unit, document=document, page=page,
        row_label="Net profit", raw_value=value,
    )


def test_cross_currency_net_profit_comparison_returns_verified_side_by_side_values():
    question = "Compare Nordea and SEB Q3 2026 net profit"
    nordea = DocumentRecord(
        document_id="nordea-q3-id", filename="nordea.pdf", issuer="Nordea",
        report_year=2026, report_quarter=3, report_type="factbook", currency="EUR",
    )
    seb = DocumentRecord(
        document_id="seb-q3-id", filename="seb.pdf", issuer="SEB",
        report_year=2026, report_quarter=3, report_type="quarterly_report", currency="SEK",
    )
    cells = [
        _cell(metric="net_profit", value="1181", unit="EURm", document="nordea.pdf", page=7),
        _cell(metric="net_profit", value="7498", unit="SEKm", document="seb.pdf", page=5),
    ]

    with patch("app.document_answering._one_cell", side_effect=cells):
        result = answer_with_selection(question, DocumentSelection(), [nordea, seb])

    assert result["supported"] is True
    assert result["needs_clarification"] is False
    assert "Side-by-side net profit comparison — Q3 2026" in result["answer"]
    assert "Nordea" in result["answer"] and "EUR 1,181 million" in result["answer"]
    assert "SEB" in result["answer"] and "SEK 7,498 million" in result["answer"]
    assert "different currencies" in result["answer"]
    assert "No subtraction" in result["answer"]
    assert len(result["sources"]) == 2
    assert {s["page"] for s in result["sources"]} == {5, 7}
    assert {s["document_id"] for s in result["sources"]} == {"nordea-q3-id", "seb-q3-id"}


def test_incompatible_nonmonetary_units_still_fail_closed():
    question = "Compare Nordea and SEB Q3 2026 net profit"
    nordea = DocumentRecord("n-id", "n.pdf", "Nordea", 2026, 3, "factbook", "EUR")
    seb = DocumentRecord("s-id", "s.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
    cells = [
        _cell(metric="net_profit", value="1181", unit="EURm", document="n.pdf", page=7),
        _cell(metric="net_profit", value="7498", unit="count", document="s.pdf", page=5),
    ]
    with patch("app.document_answering._one_cell", side_effect=cells):
        result = answer_with_selection(question, DocumentSelection(), [nordea, seb])
    assert result["supported"] is False
    assert "incompatible units" in result["answer"].casefold()


def test_nordea_q3_net_profit_cell_is_resolved_from_its_actual_report_table():
    from app.question_parser import FinancialIntent
    from app.table_lookup import resolve_intent_cells

    page_text = """Nordea Third-Quarter Factbook 2026 Q3
KEY FINANCIAL FIGURES
Income statement, 12-quarter overview*
EURm Q326 Q226 Q126 Q425 Q325 Q225 Q125 Q424 Q324 Q224 Q124 Q423
Net profit for the period 1,181 1,232 1,100 1,157 1,228 1,221 1,234 1,129 1,266 1,303 1,361 1,106
"""
    intent = FinancialIntent(
        metric="net_profit", periods=("Q326",), operation="lookup",
        scopes=("group",), country=None, currency_scopes=(),
        desired_unit=None, needs_clarification=False, uncertain=None, structured=True,
    )
    page = {"document": "annual_report.pdf", "page": 7, "text": page_text}
    with patch("app.table_lookup._get_pages", return_value=[page]):
        result = resolve_intent_cells(intent, "nordea-q3-id")

    assert result is not None and len(result) == 1
    assert result[0].value == Decimal("1181")
    assert result[0].unit == "EURm"
    assert result[0].page == 7
    assert result[0].row_label == "Net profit for the period"
