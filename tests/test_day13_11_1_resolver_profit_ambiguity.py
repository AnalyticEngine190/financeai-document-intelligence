from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection, resolve_documents


DOCS = [
    DocumentRecord("nq3", "nq3.pdf", "Nordea", 2026, 3, "factbook", "EUR"),
    DocumentRecord("sq3", "sq3.pdf", "SEB", 2026, 3, "quarterly_report", "SEK"),
]


def test_cross_bank_plain_profit_requires_clarification():
    result = resolve_documents(
        "Compare Nordea and SEB Q3 2026 profit", DocumentSelection(), DOCS
    )
    assert not result.documents
    assert result.clarification


def test_cross_bank_net_profit_is_explicit_enough_to_select_both_reports():
    result = resolve_documents(
        "Compare Nordea and SEB Q3 2026 net profit", DocumentSelection(), DOCS
    )
    assert {doc.document_id for doc in result.documents} == {"nq3", "sq3"}
    assert result.clarification is None


def test_mixed_qualified_and_unqualified_profit_still_requires_clarification():
    result = resolve_documents(
        "Compare Nordea profit and SEB net profit in Q3 2026",
        DocumentSelection(),
        DOCS,
    )
    assert not result.documents
    assert result.clarification


def test_single_bank_unqualified_profit_keeps_net_profit_default():
    result = resolve_documents(
        "What is SEB's profit?", DocumentSelection(), DOCS
    )
    assert [doc.document_id for doc in result.documents] == ["sq3"]
    assert result.clarification is None
