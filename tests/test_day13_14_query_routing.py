from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app import document_answering, market_chat
from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.table_lookup import FinancialCell

NQ2 = DocumentRecord("nq2", "Nordea Q2 2026.pdf", "Nordea", 2026, 2, "factbook", "EUR")
NQ3 = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")
DOCS = [NQ2, NQ3]


def _cell(doc, metric, period, scope="group", country=None):
    values = {("Q226", "nq2"): "1232", ("Q326", "nq3"): "1181"}
    value = values[(period, doc.document_id)]
    return FinancialCell(metric, period, scope, Decimal(value), "EURm",
                         doc.filename, 7, "Net profit for the period", value)


def test_periodless_nordea_profit_uses_q3_and_cites_q2_pdf_for_context(monkeypatch):
    calls = []

    def one_cell(doc, metric, period, scope="group", country=None):
        calls.append((doc.document_id, metric, period))
        return _cell(doc, metric, period)

    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(document_answering, "verify_cell", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    monkeypatch.setattr(document_answering, "verify_operands", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    result = document_answering.answer_with_selection(
        "Tell me the net profit of Nordea", DocumentSelection(), DOCS
    )
    assert result["supported"] is True
    assert "EUR 1,181 million" in result["answer"]
    assert "EUR 1,232 million" in result["answer"]
    assert {item[0] for item in calls} == {"nq2", "nq3"}
    assert {source["document_id"] for source in result["sources"]} == {"nq2", "nq3"}


def test_unqualified_nordea_profit_is_labelled_as_net_profit(monkeypatch):
    monkeypatch.setattr(document_answering, "_one_cell", _cell)
    monkeypatch.setattr(document_answering, "verify_cell", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    monkeypatch.setattr(document_answering, "verify_operands", lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    result = document_answering.answer_with_selection("What is Nordea's profit?", documents=DOCS)
    assert result["supported"] is True
    assert "Interpreting ‘profit’ as net profit" in result["answer"]


def test_off_topic_question_is_not_sent_to_the_bank_resolver(monkeypatch):
    monkeypatch.setattr(document_answering, "resolve_documents", lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolver must not run")))
    result = document_answering.answer_with_selection("Am I handsome?", documents=DOCS)
    assert result["supported"] is False
    assert result["needs_clarification"] is False
    assert result["sources"] == []
    assert "outside my scope" in result["answer"]


def test_quarter_followup_without_history_asks_for_context_not_for_bank():
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3"))
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "repeat the complete question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()


def test_quarter_followup_reuses_previous_bank_and_metric(monkeypatch):
    monkeypatch.setattr("app.document_registry.list_documents", lambda: DOCS)
    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        captured["selection"] = selection
        return {"answer": "EUR 1,181 million", "supported": True,
                "sources": [], "needs_clarification": False}

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    history = [
        market_chat.ChatTurn(role="user", content="Tell me the net profit of Nordea"),
        market_chat.ChatTurn(role="assistant", content="Which PDF or report edition should I use?"),
    ]
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3", history=history))
    assert result["kind"] == "document"
    assert captured["question"] == "Tell me the net profit of Nordea in Q3 2026"
    assert captured["selection"].mode == "auto"


def test_direct_answer_api_does_not_treat_period_only_reply_as_bank_question():
    result = document_answering.answer_with_selection("Use Q3", documents=DOCS)
    assert result["needs_clarification"] is True
    assert "previous question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()
