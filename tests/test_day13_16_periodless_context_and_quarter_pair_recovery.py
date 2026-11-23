from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.table_lookup import FinancialCell
from app import document_answering, market_chat
from app.question_parser import parse_question

NQ3 = DocumentRecord("nq3", "nq3.pdf", "Nordea", 2026, 3, "factbook", "EUR")
NQ2 = DocumentRecord("nq2", "nq2.pdf", "Nordea", 2026, 2, "factbook", "EUR")
SQ3 = DocumentRecord("sq3", "sq3.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
DOCS = [NQ2, NQ3]


def _cell(metric: str, period: str, value: str, doc: str) -> FinancialCell:
    return FinancialCell(
        metric=metric, period=period, scope="group", value=Decimal(value),
        unit="EURm", document=doc, page=7, row_label=metric, raw_value=value,
    )


def _cells():
    return {
        ("net_profit", "Q226", "nq2"): _cell("net_profit", "Q226", "1232", "nq2.pdf"),
        ("net_profit", "Q326", "nq3"): _cell("net_profit", "Q326", "1181", "nq3.pdf"),
        ("net_interest_income", "Q226", "nq2"): _cell("net_interest_income", "Q226", "1779", "nq2.pdf"),
        ("net_interest_income", "Q326", "nq3"): _cell("net_interest_income", "Q326", "1799", "nq3.pdf"),
        ("total_operating_income", "Q226", "nq2"): _cell("total_operating_income", "Q226", "3032", "nq2.pdf"),
        ("total_operating_income", "Q326", "nq3"): _cell("total_operating_income", "Q326", "2968", "nq3.pdf"),
    }


def _patch_cells(monkeypatch):
    cells = _cells()
    monkeypatch.setattr(
        document_answering, "generic_cell",
        lambda metric, period, document_id, **kwargs: cells.get((metric, period, document_id)),
    )
    monkeypatch.setattr(
        document_answering, "resolve_intent_cells",
        lambda intent, document_id: [],
    )
    monkeypatch.setattr(
        document_answering, "verify_cell",
        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"),
    )
    monkeypatch.setattr(
        document_answering, "verify_operands",
        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"),
    )


def test_periodless_nordea_profit_retrieves_and_cites_q3_and_q2_pdfs(monkeypatch):
    _patch_cells(monkeypatch)
    for question in ("What is Nordea's profit?", "Tell me Nordea's net profit."):
        result = document_answering.answer_with_selection(question, DocumentSelection(), DOCS)
        assert result["supported"] is True
        assert "EUR 1,181 million" in result["answer"]
        assert "EUR 1,232 million" in result["answer"]
        assert {x["document_id"] for x in result["sources"]} == {"nq2", "nq3"}
        assert "Q3 2026" in result["answer"] and "Q2 2026" in result["answer"]


def test_net_profit_absolute_change_without_year_infers_unique_q2_q3_pair(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "How much did Nordea's net profit change between Q2 and Q3?",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "EUR 51 million" in result["answer"]
    assert {x["document_id"] for x in result["sources"]} == {"nq2", "nq3"}


def test_net_profit_percentage_decline_without_year(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "Calculate the percentage decline in Nordea's net profit from Q2 to Q3.",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "4.1% decline" in result["answer"]


def test_net_interest_income_comparison_without_year(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "Compare Nordea's net interest income in Q2 and Q3.",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "EUR 20 million increase" in result["answer"]


def test_operating_income_alias_resolves_to_total_operating_income(monkeypatch):
    _patch_cells(monkeypatch)
    intent = parse_question("Did Nordea's operating income increase or decrease between Q2 2026 and Q3 2026?")
    assert intent.metric == "total_operating_income"
    result = document_answering.answer_with_selection(
        "Did Nordea's operating income increase or decrease between Q2 and Q3?",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "EUR 64 million decrease" in result["answer"]


def test_ambiguous_or_duplicate_quarter_pair_requests_year_instead_of_guessing():
    for reports in ([NQ3], [NQ2, NQ3, DocumentRecord(
        "nq3-copy", "Nordea Q3 duplicate.pdf", "Nordea", 2026, 3, "factbook", "EUR"
    )]):
        result = document_answering.answer_with_selection(
            "Compare Nordea's net profit between Q2 and Q3", DocumentSelection(), list(reports)
        )
        assert result["supported"] is False
        assert result["needs_clarification"] is True
        assert "year" in result["answer"].casefold()


def test_off_topic_answer_says_outside_my_scope_without_bank_clarification():
    result = document_answering.answer_with_selection("Am I handsome?", documents=DOCS)
    assert result["supported"] is False
    assert result["needs_clarification"] is False
    assert result["sources"] == []
    assert "outside my scope" in result["answer"].casefold()


def test_period_only_direct_answer_requests_previous_question_context():
    result = document_answering.answer_with_selection("Use Q3", documents=DOCS)
    assert result["supported"] is False
    assert result["needs_clarification"] is True
    assert "previous question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()


def test_chat_quarter_followup_without_history_requests_context_not_bank():
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3"))
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "repeat the complete question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()


def test_chat_quarter_followup_reuses_history_and_merges_q3(monkeypatch):
    monkeypatch.setattr("app.document_registry.list_documents", lambda: DOCS)
    captured = {}
    def fake_doc(question, selection):
        captured["question"] = question
        captured["selection"] = selection
        return {"answer": "EUR 1,181 million", "supported": True, "sources": [],
                "needs_clarification": False}
    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    history = [
        market_chat.ChatTurn(role="user", content="Tell me the net profit of Nordea"),
        market_chat.ChatTurn(role="assistant", content="Which report edition should I use?"),
    ]
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3", history=history))
    assert result["kind"] == "document"
    assert captured["question"] == "Tell me the net profit of Nordea in Q3 2026"
    assert captured["selection"].mode == "auto"
