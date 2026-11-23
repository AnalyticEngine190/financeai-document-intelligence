from app import market_chat
from app.question_parser import parse_question


def test_comparison_followup_resolves_from_previous_question(monkeypatch):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea"] if "Nordea" in question else [],
    )

    captured = {}

    def fake_report(question, selection):
        captured["question"] = question
        captured["selection"] = selection
        return {
            "answer": "Comparison test response",
            "supported": True,
            "sources": [],
            "needs_clarification": False,
        }

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_report)

    history = [
        market_chat.ChatTurn(
            role="user",
            content="What was Nordea's operating profit in Q2 2026?",
        ),
        market_chat.ChatTurn(
            role="assistant",
            content="Nordea operating profit in Q2 2026: EUR 1,608 million.",
        ),
    ]

    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=history,
        )
    )

    assert result["kind"] == "document"
    assert result["supported"] is True

    assert captured["question"] == (
        "Compare What was Nordea's operating profit in Q2 2026 with Q3 2026."
    )

    parsed = parse_question(captured["question"])

    assert parsed.metric == "operating_profit"
    assert parsed.periods == ("Q226", "Q326")
    assert parsed.scopes == ("group",)
    assert captured["selection"].mode == "auto"


def test_comparison_followup_without_history_requests_context():
    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?"
        )
    )

    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert result["context_required"] is True
    assert "previous financial question" in result["answer"].casefold()


def test_comparison_followup_with_ambiguous_previous_question_requests_clarification(
    monkeypatch,
):
    monkeypatch.setattr(
        market_chat,
        "_known_issuer_mentions",
        lambda question: ["Nordea"],
    )

    history = [
        market_chat.ChatTurn(
            role="user",
            content="Tell me about Nordea in Q2 2026.",
        ),
    ]

    result = market_chat.process_chat(
        market_chat.ChatRequest(
            question="How does that compare with Q3 2026?",
            history=history,
        )
    )

    assert result["kind"] == "document_clarification"
    assert result["needs_clarification"] is True
    assert result["supported"] is False