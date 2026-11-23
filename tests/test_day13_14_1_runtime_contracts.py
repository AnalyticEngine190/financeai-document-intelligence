from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import sys

import pytest

# Let this focused file run in test sandboxes without the production Chroma dependency.
if "chromadb" not in sys.modules:
    class _Collection:
        def get(self, **kwargs):
            return {"ids": [], "documents": [], "metadatas": []}
    class _Client:
        def get_or_create_collection(self, **kwargs):
            return _Collection()
    _chroma = ModuleType("chromadb")
    _chroma.PersistentClient = lambda **kwargs: _Client()
    sys.modules["chromadb"] = _chroma
if "sentence_transformers" not in sys.modules:
    _st = ModuleType("sentence_transformers")
    _st.SentenceTransformer = object
    sys.modules["sentence_transformers"] = _st

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.question_parser import parse_question
from app.table_lookup import FinancialCell
from app import document_answering, market_chat, market_routes

NQ3 = DocumentRecord("nq3", "nq3.pdf", "Nordea", 2026, 3, "factbook", "EUR")
NQ2 = DocumentRecord("nq2", "nq2.pdf", "Nordea", 2026, 2, "factbook", "EUR")
SQ3 = DocumentRecord("sq3", "sq3.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
HB3 = DocumentRecord("hb3", "hb3.pdf", "Handelsbanken", 2026, 3, "interim_report", "SEK")
DOCS = [NQ2, NQ3, SQ3, HB3]


def _cell(metric="net_profit", value="1181", unit="EURm", document="nq3.pdf", page=7, period="Q326", scope="group"):
    return FinancialCell(metric, period, scope, Decimal(value), unit, document, page, metric, value)


def test_periodless_nordea_operating_profit_uses_latest_report_but_preserves_legacy_prompt():
    with patch.object(document_answering, "_one_cell", return_value=None):
        with patch("app.answering.answer_question", return_value={
            "answer": "EUR 1,541 million", "supported": True, "sources": [],
        }) as answered:
            result = document_answering.answer_with_selection(
                "What was Nordea's operating profit?", documents=[NQ3, NQ2]
            )
    answered.assert_called_once_with("What was Nordea's operating profit?", "nq3")
    assert result["supported"] is True
    assert "latest indexed report (Q3 2026)" in result["answer"]


def test_periodless_nordea_net_profit_uses_latest_quarter_cell():
    with patch.object(document_answering, "generic_cell", return_value=_cell()):
        with patch("app.document_answering.verify_cell", return_value=SimpleNamespace(accepted=True, reason="ok")):
            result = document_answering.answer_with_selection(
                "Tell me the net profit of Nordea", documents=[NQ2, NQ3]
            )
    assert result["supported"] is True
    assert "EUR 1,181 million" in result["answer"]
    assert "Q3 2026" in result["answer"]
    assert [x["document_id"] for x in result["sources"]] == ["nq3"]


def test_periodless_unqualified_nordea_profit_defaults_to_net_profit():
    with patch.object(document_answering, "generic_cell", return_value=_cell()):
        with patch("app.document_answering.verify_cell", return_value=SimpleNamespace(accepted=True, reason="ok")):
            result = document_answering.answer_with_selection(
                "What is Nordea's profit?", documents=[NQ2, NQ3]
            )
    assert result["supported"] is True
    assert "Interpreting ‘profit’ as net profit" in result["answer"]
    assert "EUR 1,181 million" in result["answer"]


def test_seb_unqualified_profit_shows_net_profit_and_operating_profit():
    from pathlib import Path
    import json
    fixture = Path(__file__).parent / "fixtures" / "seb_q3_2026_income_statement_page.json"
    row = json.loads(fixture.read_text(encoding="utf-8"))
    with patch("app.document_tables._get_pages", return_value=[row]):
        result = document_answering.answer_with_selection("What is SEB's profit", documents=[SQ3])
    assert result["supported"] is True
    assert "SEK 7,498 million" in result["answer"]
    assert "operating profit" in result["answer"].casefold()
    assert "SEK 9,412 million" in result["answer"]


def test_profit_for_period_is_parsed_as_net_profit():
    intent = parse_question("What was Handelsbanken's profit for the period in Q3 2026?")
    assert intent.metric == "net_profit"
    assert intent.periods == ("Q326",)


def test_baltic_is_a_hard_segment_scope():
    intent = parse_question("What was SEB Baltic division's operating profit in Q3 2026?")
    assert intent.metric == "operating_profit"
    assert intent.periods == ("Q326",)
    assert intent.scopes == ("baltic",)


def test_direct_off_topic_question_does_not_ask_which_bank():
    result = document_answering.answer_with_selection("Am I handsome?", documents=DOCS)
    assert result["supported"] is False
    assert result["needs_clarification"] is False
    assert result.get("out_of_scope") is True
    assert "financial reports" in result["answer"].casefold()


def test_direct_period_only_reply_asks_for_previous_question_not_bank():
    result = document_answering.answer_with_selection("Use Q3", documents=DOCS)
    assert result["needs_clarification"] is True
    assert "previous question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()


def test_chat_period_only_reply_without_context_is_clear_document_clarification():
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3"))
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "repeat the complete question" in result["answer"].casefold()
    assert "which bank" not in result["answer"].casefold()


def test_chat_history_merges_quarter_followup_into_prior_question(monkeypatch):
    monkeypatch.setattr("app.document_registry.list_documents", lambda: DOCS)
    captured = {}
    def fake_doc(question, selection):
        captured["question"] = question
        captured["selection"] = selection
        return {"answer": "EUR 1,181 million", "supported": True, "sources": []}
    monkeypatch.setattr(market_routes, "_doc", fake_doc)
    history = [
        market_chat.ChatTurn(role="user", content="Tell me the net profit of Nordea"),
        market_chat.ChatTurn(role="assistant", content="Which PDF or report edition should I use?"),
    ]
    result = market_chat.process_chat(market_chat.ChatRequest(question="Use Q3", history=history))
    assert result["kind"] == "document"
    assert captured["question"] == "Tell me the net profit of Nordea in Q3 2026"
    assert captured["selection"].mode == "auto"


def test_api_day13_5_registers_chat_router_for_the_existing_start_command():
    from pathlib import Path
    source = Path(__file__).parents[1] / "app" / "api_day13_5.py"
    code = source.read_text(encoding="utf-8")
    assert "from app.market_chat import router as chat_router" in code
    assert "app.include_router(chat_router)" in code
