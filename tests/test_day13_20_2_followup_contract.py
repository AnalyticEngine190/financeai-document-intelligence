from __future__ import annotations

import sys
from types import ModuleType

# Let the focused test file run in lightweight validation environments too.
# These stubs are only installed when the optional packages are genuinely absent.
if "chromadb" not in sys.modules:
    try:
        import chromadb  # noqa: F401
    except ImportError:
        chroma = ModuleType("chromadb")

        class _EmptyCollection:
            def get(self, **kwargs):
                return {"ids": [], "documents": [], "metadatas": []}

        class _EmptyClient:
            def get_or_create_collection(self, **kwargs):
                return _EmptyCollection()

        chroma.PersistentClient = lambda **kwargs: _EmptyClient()
        sys.modules["chromadb"] = chroma

if "sentence_transformers" not in sys.modules:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        st = ModuleType("sentence_transformers")
        st.SentenceTransformer = object
        sys.modules["sentence_transformers"] = st

from app import market_chat
from app.question_parser import parse_question


def _history(question: str):
    return [
        market_chat.ChatTurn(role="user", content=question),
        market_chat.ChatTurn(
            role="assistant",
            content="Nordea operating profit in Q2 2026: EUR 1,608 million.",
        ),
    ]


def test_comparison_followup_preserves_original_question_contract(monkeypatch):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea"] if "Nordea" in question else [],
    )
    captured = {}

    def fake_report(question, selection):
        captured["question"] = question
        return {
            "answer": "Comparison test response",
            "supported": True,
            "sources": [],
            "needs_clarification": False,
        }

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_report)
    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=_history("What was Nordea's operating profit in Q2 2026?"),
        )
    )

    assert result["kind"] == "document"
    assert result["supported"] is True
    assert captured["question"] == (
        "Compare What was Nordea's operating profit in Q2 2026 with Q3 2026."
    )


def test_merged_comparison_remains_parseable_by_existing_financial_parser(monkeypatch):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea"] if "Nordea" in question else [],
    )
    captured = {}

    def fake_report(question, selection):
        captured["question"] = question
        return {
            "answer": "Comparison test response",
            "supported": True,
            "sources": [],
            "needs_clarification": False,
        }

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_report)
    market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=_history("What was Nordea's operating profit in Q2 2026?"),
        )
    )

    parsed = parse_question(captured["question"])
    assert parsed.metric == "operating_profit"
    assert parsed.periods == ("Q226", "Q326")


def test_no_history_clarification_names_the_missing_previous_financial_question():
    result = market_chat.process_chat(
        market_chat.ChatRequest(question="How does that compare with Q3 2026?")
    )
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert result["context_required"] is True
    assert "previous financial question" in result["answer"].casefold()


def test_company_overview_is_not_mistaken_for_a_financial_metric(monkeypatch):
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda question: ["Nordea"])
    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=[market_chat.ChatTurn(
                role="user", content="Tell me about Nordea in Q2 2026."
            )],
        )
    )
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "specific financial measure" in result["answer"].casefold()


def test_same_quarter_comparison_is_rejected(monkeypatch):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea"] if "Nordea" in question else [],
    )
    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q2 2026?",
            history=_history("What was Nordea's operating profit in Q2 2026?"),
        )
    )
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "same quarter" in result["answer"].casefold()


def test_ambiguous_previous_issuer_requests_clarification(monkeypatch):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea", "SEB"] if "Nordea" in question and "SEB" in question else [],
    )
    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=[market_chat.ChatTurn(
                role="user",
                content="What was Nordea and SEB operating profit in Q2 2026?",
            )],
        )
    )
    assert result["kind"] == "document_clarification"
    assert "one specific bank" in result["answer"].casefold()
