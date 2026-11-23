from __future__ import annotations

from decimal import Decimal
from types import ModuleType, SimpleNamespace
import sys

import pytest

# The focused tests use mocked evidence cells and do not need Chroma or the
# embedding model merely to import the answer orchestration module. Keep real
# installations untouched when running on the user's Windows environment.
if "chromadb" not in sys.modules:
    _chromadb = ModuleType("chromadb")
    _chromadb.PersistentClient = lambda **kwargs: None
    sys.modules["chromadb"] = _chromadb
if "sentence_transformers" not in sys.modules:
    _sentence_transformers = ModuleType("sentence_transformers")
    _sentence_transformers.SentenceTransformer = object
    sys.modules["sentence_transformers"] = _sentence_transformers

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection, resolve_documents
from app import document_answering, market_chat, market_routes

NQ3 = DocumentRecord("nq3", "nq3.pdf", "Nordea", 2026, 3, "factbook", "EUR")
NQ2 = DocumentRecord("nq2", "nq2.pdf", "Nordea", 2026, 2, "factbook", "EUR")
SQ3 = DocumentRecord("sq3", "sq3.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
DOCS = [NQ2, NQ3, SQ3]


def test_nordea_without_period_defaults_to_latest_indexed_edition():
    result = resolve_documents("What is Nordea's profit?", DocumentSelection(), DOCS)
    assert [doc.document_id for doc in result.documents] == ["nq3"]
    assert result.clarification is None
    assert "Q3 2026" in result.reason
    assert "older editions were not mixed" in result.reason


def test_nordea_explicit_q2_still_selects_q2():
    result = resolve_documents("Tell me Nordea's net profit in Q2 2026", DocumentSelection(), DOCS)
    assert [doc.document_id for doc in result.documents] == ["nq2"]


def test_question_without_bank_still_asks_for_bank_when_it_is_financial():
    result = resolve_documents("What is the net profit?", DocumentSelection(), DOCS)
    assert not result.documents
    assert "bank" in (result.clarification or "").casefold()


def test_nonfinancial_gibberish_does_not_enter_report_resolver(monkeypatch):
    monkeypatch.setattr(market_routes, "_doc", lambda *_: pytest.fail("report resolver must not run"))
    result = market_chat.process_chat(market_chat.ChatRequest(question="Am I handsome?"))
    assert result["kind"] == "out_of_scope"
    assert result["needs_clarification"] is False
    assert not result["supported"]
    assert result["sources"] == []
    assert "financial reports" in result["answer"].casefold()


def test_no_context_period_reply_does_not_ask_which_bank():
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3"))
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "repeat the complete question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()


def test_period_followup_is_merged_with_prior_question(monkeypatch):
    monkeypatch.setattr("app.document_registry.list_documents", lambda: DOCS)
    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        captured["selection"] = selection
        return {"answer": "EUR 1,181 million", "supported": True, "sources": []}

    monkeypatch.setattr(market_routes, "_doc", fake_doc)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="Use Q3", context_question="Tell me the net profit of Nordea"
    ))
    assert result["kind"] == "document"
    assert captured["question"] == "Tell me the net profit of Nordea in Q3 2026"
    assert captured["selection"].mode == "auto"


def test_periodless_nordea_net_profit_uses_latest_quarter_and_prior_pdf_context(monkeypatch):
    captured = []

    def fake_cell(doc, metric, period, scope="group", country=None):
        captured.append((metric, period, doc.document_id, scope))
        value = "1181" if period == "Q326" else "1232"
        return document_answering.FinancialCell(
            metric=metric, period=period, scope=scope, value=Decimal(value),
            unit="EURm", document=doc.filename, page=7,
            row_label="Net profit for the period", raw_value=value,
        )

    monkeypatch.setattr(document_answering, "_one_cell", fake_cell)
    monkeypatch.setattr(document_answering, "verify_cell", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    monkeypatch.setattr(document_answering, "verify_operands", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    result = document_answering.answer_with_selection(
        "Tell me the net profit of Nordea", DocumentSelection(), DOCS
    )
    assert result["supported"] is True
    assert set(captured) == {
        ("net_profit", "Q326", "nq3", "group"),
        ("net_profit", "Q226", "nq2", "group"),
    }
    assert "Q3 2026" in result["answer"] and "Q2 2026" in result["answer"]
    assert "EUR 1,181 million" in result["answer"]
    assert "EUR 1,232 million" in result["answer"]
    assert {source["document_id"] for source in result["sources"]} == {"nq2", "nq3"}


def test_plain_nordea_profit_is_explicitly_defined_as_net_profit(monkeypatch):
    captured = {}

    def fake_cell(doc, metric, period, scope="group", country=None):
        captured["metric"] = metric
        return document_answering.FinancialCell(
            metric=metric, period=period, scope=scope, value=Decimal("1181"),
            unit="EURm", document="annual_report.pdf", page=7,
            row_label="Net profit for the period", raw_value="1,181",
        )

    monkeypatch.setattr(document_answering, "_one_cell", fake_cell)
    monkeypatch.setattr(document_answering, "verify_cell", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    result = document_answering.answer_with_selection(
        "What is Nordea's profit?", DocumentSelection(), DOCS
    )
    assert result["supported"] is True
    assert captured["metric"] == "net_profit"
    assert "Interpreting ‘profit’ as net profit" in result["answer"]
