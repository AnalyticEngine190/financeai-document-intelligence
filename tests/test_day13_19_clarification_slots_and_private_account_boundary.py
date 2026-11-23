from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

# Allow this focused test module to run in light containers without Chroma.
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

from app import document_answering, market_chat
from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.question_parser import parse_question
from app.table_lookup import FinancialCell

NQ2 = DocumentRecord("nq2", "Nordea Q2 2026.pdf", "Nordea", 2026, 2, "factbook", "EUR")
NQ3 = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")
DOCS = [NQ2, NQ3]


def _cell(doc, metric, period, scope="group", country=None):
    assert metric == "net_fee_income"
    assert period == "Q326"
    return FinancialCell(
        metric, period, scope, Decimal("880"), "EURm",
        doc.filename, 7, "Net fee and commission income", "880",
    )


def test_commission_income_short_alias_maps_to_report_row():
    intent = parse_question("What was the commission income of Nordea in Q3 2026?")
    assert intent.metric == "net_fee_income"
    assert intent.periods == ("Q326",)
    assert intent.needs_clarification is False


def test_commission_income_of_nordea_uses_latest_indexed_report(monkeypatch):
    monkeypatch.setattr(document_answering, "_one_cell", _cell)
    monkeypatch.setattr(
        document_answering, "verify_cell",
        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )
    result = document_answering.answer_with_selection(
        "commission income of Nordea", DocumentSelection(), DOCS
    )
    assert result["supported"] is True
    assert "EUR 880 million" in result["answer"]
    assert result["sources"][0]["document_id"] == "nq3"
    assert result["sources"][0]["page"] == 7


def test_metric_clarification_reply_preserves_bank_and_period(monkeypatch):
    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        return {"answer": "EUR 880 million", "supported": True,
                "sources": [], "needs_clarification": False}

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="commission income",
        context_question="What was Nordea's revenue in Q3 2026?",
        context_prompt=(
            "Which financial measure do you mean: total operating income, "
            "net interest income, net fee and commission income, or net profit?"
        ),
    ))
    assert result["kind"] == "document"
    assert captured["question"] == "What was Nordea's net fee and commission income in Q3 2026"


def test_bank_clarification_reply_preserves_metric_and_period(monkeypatch):
    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        return {"answer": "EUR 1,181 million", "supported": True,
                "sources": [], "needs_clarification": False}

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    monkeypatch.setattr("app.document_registry.list_documents", lambda: DOCS)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="Nordea",
        context_question="What was the net profit in Q3 2026?",
        context_prompt="Which bank should I use? Choose a report from the dropdown or mention its bank name.",
    ))
    assert result["kind"] == "document"
    assert captured["question"] == "What was the net profit in Q3 2026 for Nordea"


def test_report_clarification_is_tagged_as_metric_slot(monkeypatch):
    monkeypatch.setattr(market_chat.legacy, "_doc", lambda *args: {
        "answer": "Which financial measure do you mean: total operating income or net profit?",
        "supported": False, "sources": [], "needs_clarification": True,
    })
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="What was Nordea's revenue in Q3 2026?"
    ))
    assert result["needs_clarification"] is True
    assert result["clarification_type"] == "metric"
    assert result["clarification_context_question"] == "What was Nordea's revenue in Q3 2026?"


def test_personal_account_balance_is_out_of_scope_in_chat_without_bank_clarification():
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="Can you tell me my personal bank account balance?"
    ))
    assert result["kind"] == "out_of_scope"
    assert result["needs_clarification"] is False
    assert result["sources"] == []
    assert "personal bank-account balances" in result["answer"]


def test_personal_account_balance_is_out_of_scope_in_direct_document_answerer(monkeypatch):
    monkeypatch.setattr(
        document_answering, "resolve_documents",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver must not run")),
    )
    result = document_answering.answer_with_selection(
        "Can you tell me my personal bank account balance?", documents=DOCS
    )
    assert result["supported"] is False
    assert result["needs_clarification"] is False
    assert result["sources"] == []
    assert result.get("out_of_scope") is True


def test_streamlit_client_sends_prior_clarification_prompt_and_question():
    source = (Path(__file__).parents[1] / "streamlit_market_app.py").read_text(encoding="utf-8")
    assert "def _context_for_followup(question: str)" in source
    assert '"context_question": context_question' in source
    assert '"context_prompt": context_prompt' in source
    assert 'result.get("clarification_context_question")' in source
    assert "SHORT_SLOT_REPLY.fullmatch(question or \"\")" in source
    assert '"history": history' in source
    assert 'st.session_state.chat_messages[-20:]' in source