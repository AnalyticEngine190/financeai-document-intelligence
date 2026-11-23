from __future__ import annotations

from decimal import Decimal
from types import ModuleType, SimpleNamespace
import sys

# Keep these resolver/answer-orchestration tests runnable without loading a
# persistent Chroma index or a sentence-transformer model.
if "chromadb" not in sys.modules:
    module = ModuleType("chromadb")
    module.PersistentClient = lambda **kwargs: None
    sys.modules["chromadb"] = module
if "sentence_transformers" not in sys.modules:
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = object
    sys.modules["sentence_transformers"] = module

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection, resolve_documents
from app import document_answering

SEB = DocumentRecord("seb-q3", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
HANDELSBANKEN = DocumentRecord(
    "hb-q3", "Handelsbanken Q3 2026.pdf", "Handelsbanken", 2026, 3, "interim_report", "SEK"
)
NORDEA = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")
QUESTION = "Which had higher operating profit in Q3 2026, SEB or Handelsbanken, and by how much?"


def _cell(metric, period, value, doc):
    return document_answering.FinancialCell(
        metric=metric,
        period=period,
        scope="group",
        value=Decimal(value),
        unit="SEKm" if doc.currency == "SEK" else "EURm",
        document=doc.filename,
        page=5 if doc.issuer == "SEB" else 24,
        row_label="Operating profit",
        raw_value=str(value),
    )


def test_higher_adjective_is_recognized_as_cross_bank_comparison():
    result = resolve_documents(QUESTION, DocumentSelection(), [HANDELSBANKEN, SEB])
    assert result.clarification is None
    assert [doc.issuer for doc in result.documents] == ["SEB", "Handelsbanken"]


def test_which_had_higher_operating_profit_answers_winner_and_gap(monkeypatch):
    def one_cell(doc, metric, period, scope="group", country=None):
        assert metric == "operating_profit"
        assert period == "Q326"
        if doc.issuer == "SEB":
            return _cell(metric, period, "9412", doc)
        if doc.issuer == "Handelsbanken":
            return _cell(metric, period, "7353", doc)
        raise AssertionError(f"Unexpected issuer: {doc.issuer}")

    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(
        document_answering,
        "verify_operands",
        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )

    result = document_answering.answer_with_selection(
        QUESTION, DocumentSelection(), [HANDELSBANKEN, SEB]
    )

    assert result["supported"] is True
    assert "SEB had higher operating profit than Handelsbanken" in result["answer"]
    assert "SEK 2,059 million" in result["answer"]
    assert "SEK 9,412 million" in result["answer"]
    assert "SEK 7,353 million" in result["answer"]
    assert {source["document_id"] for source in result["sources"]} == {"seb-q3", "hb-q3"}


def test_higher_comparison_across_different_currencies_still_requires_fx(monkeypatch):
    def one_cell(doc, metric, period, scope="group", country=None):
        value = "9412" if doc.issuer == "SEB" else "1541"
        return _cell(metric, period, value, doc)

    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    result = document_answering.answer_with_selection(
        "Which had higher operating profit in Q3 2026, SEB or Nordea, and by how much?",
        DocumentSelection(), [SEB, NORDEA],
    )
    assert result["supported"] is False
    assert result["sources"] == []
    assert "exchange rate" in result["answer"].casefold()


def test_explicit_compare_and_than_phrasings_remain_supported():
    for question in (
        "Compare SEB and Handelsbanken Q3 2026 operating profit",
        "Did SEB have higher operating profit than Handelsbanken in Q3 2026?",
    ):
        resolution = resolve_documents(question, DocumentSelection(), [HANDELSBANKEN, SEB])
        assert resolution.clarification is None
        assert {doc.issuer for doc in resolution.documents} == {"SEB", "Handelsbanken"}


def test_chat_routes_higher_cross_bank_question_to_report_engine(monkeypatch):
    from app import market_chat

    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        return document_answering.answer_with_selection(
            question, DocumentSelection(), [HANDELSBANKEN, SEB]
        )

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    monkeypatch.setattr(document_answering, "_one_cell", lambda doc, metric, period, scope="group", country=None:
                       _cell(metric, period, "9412" if doc.issuer == "SEB" else "7353", doc))
    monkeypatch.setattr(document_answering, "verify_operands",
                        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"))

    response = market_chat.process_chat(market_chat.ChatRequest(question=QUESTION))
    assert response["kind"] == "document"
    assert response["supported"] is True
    assert "SEB had higher operating profit" in response["answer"]
    assert "Which bank" not in response["answer"]
    assert captured["question"] == QUESTION


def test_higher_percentage_metric_is_comparable_without_fx(monkeypatch):
    def ratio_cell(doc, metric, period, scope="group", country=None):
        value = "17.1" if doc.issuer == "SEB" else "17.0"
        return document_answering.FinancialCell(
            metric=metric, period=period, scope="group", value=Decimal(value),
            unit="percent", document=doc.filename, page=6 if doc.issuer == "SEB" else 20,
            row_label="Common Equity Tier 1 capital ratio", raw_value=value,
        )

    monkeypatch.setattr(document_answering, "_one_cell", ratio_cell)
    monkeypatch.setattr(document_answering, "verify_operands",
                        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"))
    result = document_answering.answer_with_selection(
        "Which had higher CET1 capital ratio in Q3 2026, SEB or Nordea?",
        DocumentSelection(), [SEB, NORDEA],
    )
    assert result["supported"] is True
    assert "SEB had higher cet1_ratio" not in result["answer"]
    assert "SEB had higher cet1 ratio than Nordea by 0.1 percentage points" in result["answer"]


def test_higher_monetary_metric_across_currencies_needs_fx_even_without_gap_request(monkeypatch):
    monkeypatch.setattr(document_answering, "_one_cell", lambda *args, **kwargs: None)
    result = document_answering.answer_with_selection(
        "Which had higher net profit in Q3 2026, SEB or Nordea?",
        DocumentSelection(), [SEB, NORDEA],
    )
    assert result["supported"] is False
    assert result["sources"] == []
    assert "exchange rate" in result["answer"].casefold()
