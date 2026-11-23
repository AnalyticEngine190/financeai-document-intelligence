from decimal import Decimal
from unittest.mock import patch
from app.document_answering import answer_with_selection
from app.document_resolver import DocumentSelection
from app.document_registry import DocumentRecord
from app.table_lookup import FinancialCell

NQ3 = DocumentRecord("nq3", "nq3.pdf", "Nordea", 2026, 3, "factbook", "EUR")
NQ2 = DocumentRecord("nq2", "nq2.pdf", "Nordea", 2026, 2, "factbook", "EUR")
SQ3 = DocumentRecord("sq3", "sq3.pdf", "SEB", 2026, 3, "interim_report", "SEK")


def cell(value, doc="report.pdf", period="Q326", unit="EURm", page=7):
    return FinancialCell("net_interest_income", period, "group", Decimal(value), unit, doc, page,
                         "Net interest income", value)


def test_manual_restricts_legacy_answer_and_labels_source():
    with patch("app.answering.answer_question", return_value={
            "answer": "EUR 1,799 million", "supported": True,
            "sources": [{"document": "nq3.pdf", "page": 7}]}) as answered:
        result = answer_with_selection("Net interest income in Q3 2026?",
                DocumentSelection("manual", ("nq3",)), [NQ3, NQ2])
    answered.assert_called_once_with("Net interest income in Q3 2026?", "nq3")
    assert result["sources"][0]["document_id"] == "nq3"


def test_auto_selects_latest_nordea_report_for_periodless_scalar_lookup():
    with patch("app.answering.answer_question", return_value={
            "answer": "I cannot verify the answer from the retrieved document passages.",
            "supported": False, "sources": []}) as answered:
        r = answer_with_selection("What was Nordea's operating profit?", documents=[NQ3, NQ2])
    answered.assert_called_once_with("What was Nordea's operating profit?", "nq3")
    assert r["selected_documents"][0]["document_id"] == "nq3"
    assert r["selection_reason"].startswith("Latest indexed edition selected automatically")


def test_same_bank_two_edition_arithmetic_and_two_citations():
    def resolve(i, document_id):
        return [cell("1779", "q2.pdf", "Q226", page=8)] if document_id == "nq2" else [
            cell("1799", "q3.pdf", "Q326", page=7)]
    with patch("app.document_answering.resolve_intent_cells", side_effect=resolve):
        r = answer_with_selection(
            "Compare Nordea net interest income from Q2 2026 to Q3 2026",
            DocumentSelection("compare", ("nq2", "nq3")), [NQ2, NQ3])
    assert r["supported"] and "EUR 20 million" in r["answer"]
    assert {x["document_id"] for x in r["sources"]} == {"nq2", "nq3"}


def test_currency_mismatch_must_abstain():
    with patch("app.document_answering.resolve_intent_cells", return_value=[cell("2000", unit="EURm")]):
        with patch("app.document_answering.generic_cell", return_value=cell("20000", unit="SEKm")):
            r = answer_with_selection("Compare Nordea and SEB Q3 2026 net interest income",
                DocumentSelection("compare", ("nq3", "sq3")), [NQ3, SQ3])
    assert not r["supported"] and not r["sources"]


def test_missing_cross_document_operand_never_falls_back_to_another_pdf():
    with patch("app.document_answering.resolve_intent_cells", return_value=[cell("1800")]):
        with patch("app.document_answering.generic_cell", return_value=None):
            r = answer_with_selection("Compare Nordea and SEB Q3 2026 net interest income",
                DocumentSelection("compare", ("nq3", "sq3")), [NQ3, SQ3])
    assert not r["supported"]


def test_new_issuer_unverified_cell_abstains():
    with patch("app.document_answering.generic_cell", return_value=None):
        r = answer_with_selection("What was SEB's net interest income in Q3 2026?",
            DocumentSelection("manual", ("sq3",)), [SQ3])
    assert not r["supported"]
