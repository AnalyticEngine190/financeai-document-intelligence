
"""Tests for casual conversation and financial terminology."""

from unittest.mock import patch

from app.answering import answer_question
from app.hybrid_search import (
    expand_financial_terms,
    metric_phrases,
)
from app.question_understanding import small_talk_response


def test_greeting_skips_financial_retrieval():
    with (
        patch("app.answering.hybrid_search") as search,
        patch("app.answering._call_ollama") as model,
    ):
        result = answer_question(
            "How are you?",
            document_id="test-document",
        )

    assert result["is_small_talk"] is True
    assert result["sources"] == []
    search.assert_not_called()
    model.assert_not_called()


def test_financial_question_is_not_small_talk():
    result = small_talk_response(
        "How are you calculating Nordea's CET1 ratio?"
    )
    assert result is None


def test_total_income_terminology():
    expanded = expand_financial_terms(
        "What was Nordea's total income in Q3 2026?"
    )

    phrases = metric_phrases(
        "What was Nordea's total income in Q3 2026?"
    )

    assert "total operating income" in expanded.lower()
    assert "total operating income" in phrases
