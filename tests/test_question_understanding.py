
"""Tests for ambiguity handling."""

from unittest.mock import patch

from app.answering import answer_question
from app.question_understanding import clarification_for


def test_vague_revenue_question_requests_clarification():
    with patch("app.answering.hybrid_search") as search:
        result = answer_question(
            "What was the revenue?",
            document_id="test-document",
        )

    assert result["needs_clarification"] is True
    assert "total income" in result["answer"]
    search.assert_not_called()


def test_specific_metric_does_not_request_clarification():
    assert clarification_for(
        "What was Nordea's net interest income?"
    ) is None


def test_future_year_question_keeps_normal_handling():
    assert clarification_for(
        "What was Nordea's revenue in 2035?"
    ) is None
