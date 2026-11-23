from __future__ import annotations

from pathlib import Path

from app import market_chat
from app.question_parser import parse_question


def test_period_comparison_followup_merges_prior_metric_and_both_periods(monkeypatch):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea"] if "Nordea" in question else [],
    )
    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        return {
            "answer": "EUR 1,608 million in Q2 and EUR 1,541 million in Q3.",
            "supported": True,
            "sources": [],
            "needs_clarification": False,
        }

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    body = market_chat.ChatRequest(
        question="How does that compare with Q3 2026?",
        history=[
            market_chat.ChatTurn(
                role="user", content="What was Nordea's operating profit in Q2 2026?"
            ),
            market_chat.ChatTurn(
                role="assistant", content="Nordea operating profit in Q2 2026: EUR 1,608 million."
            ),
        ],
    )

    result = market_chat.process_chat(body)
    assert result["kind"] == "document"
    assert "Nordea" in captured["question"]
    assert "operating profit" in captured["question"]
    assert "Q2 2026" in captured["question"]
    assert "Q3 2026" in captured["question"]
    parsed = parse_question(captured["question"])
    assert parsed.metric == "operating_profit"
    assert parsed.periods == ("Q226", "Q326")


def test_period_comparison_followup_without_context_asks_for_full_question():
    result = market_chat.process_chat(
        market_chat.ChatRequest(question="How does that compare with Q3 2026?")
    )
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert result["context_required"] is True


def test_period_comparison_followup_after_company_overview_asks_for_metric(monkeypatch):
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda question: ["Nordea"])
    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=[market_chat.ChatTurn(role="user", content="Tell me about Nordea in Q2 2026.")],
        )
    )
    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert "specific financial measure" in result["answer"]


def test_chat_client_sends_prior_history_variable_not_the_current_turn_twice():
    source = (Path(__file__).parents[1] / "streamlit_market_app.py").read_text(encoding="utf-8")
    history_at = source.index("    history = [", source.index("def submit("))
    append_at = source.index('st.session_state.chat_messages.append({"role": "user"', history_at)
    payload_at = source.index('"history": history', append_at)
    assert history_at < append_at < payload_at


def test_portfolio_disclosure_mentions_variable_market_coverage():
    source = (Path(__file__).parents[1] / "streamlit_market_app.py").read_text(encoding="utf-8")
    assert "Twelve Data Basic" in source
    assert "instrument and exchange coverage varies" in source
    assert "Not investment advice" in source
