
"""Tests for grounded answer validation."""

from unittest.mock import patch

from app.answering import answer_question


DOCUMENT_ID = "test-document"

EVIDENCE = [{
    "document": "annual_report.pdf",
    "page": 40,
    "text": (
        "Capital position Q3 2026. "
        "Common Equity Tier 1 ratio 15.8%."
    ),
}]


@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_no_evidence_abstains(mock_search, mock_ollama):
    mock_search.return_value = []

    with patch(
            "app.answering.lookup_fact",
            return_value=(None, False),
    ):
        result = answer_question(
            "What was the combined LCR in Q3 2026?",
            DOCUMENT_ID,
        )

    assert result["supported"] is False
    assert result["sources"] == []
    mock_ollama.assert_not_called()


@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_answer_has_valid_citation(mock_search, mock_ollama):
    mock_search.return_value = EVIDENCE

    mock_ollama.return_value = {
        "answer": "The Q3 2026 CET1 ratio was 15.8%.",
        "citation_ids": [1],
        "supported": True,
    }

    result = answer_question(
        "What is the CET1 ratio?", DOCUMENT_ID
    )

    assert result["supported"] is True
    assert result["sources"][0]["page"] == 40
    assert "15.8%" in result["answer"]


@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_invalid_citation_abstains(mock_search, mock_ollama):
    mock_search.return_value = EVIDENCE

    mock_ollama.return_value = {
        "answer": "The CET1 ratio was 15.8%.",
        "citation_ids": [99],
        "supported": True,
    }

    result = answer_question(
        "What is the CET1 ratio?", DOCUMENT_ID
    )

    assert result["supported"] is False


@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_unsupported_percentage_abstains(
    mock_search, mock_ollama
):
    mock_search.return_value = EVIDENCE

    mock_ollama.return_value = {
        "answer": "The CET1 ratio was 99.9%.",
        "citation_ids": [1],
        "supported": True,
    }

    result = answer_question(
        "What is the CET1 ratio?", DOCUMENT_ID
    )

    assert result["supported"] is False


@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_model_abstention(mock_search, mock_ollama):
    mock_search.return_value = EVIDENCE

    mock_ollama.return_value = {
        "answer": "Insufficient information.",
        "citation_ids": [],
        "supported": False,
    }

    result = answer_question(
        "What was the CEO's childhood nickname?",
        DOCUMENT_ID,
    )

    assert result["supported"] is False




@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_lcr_chunks_merged_in_page_order(
    mock_search, mock_ollama
):
    # Deliberately return chunks in reverse reading order.
    mock_search.return_value = [
        {
            "document": "report.pdf",
            "page": 53,
            "chunk_index": 1,
            "text": (
                "Liquidity coverage ratio (%) "
                "160% 229% 170%"
            ),
        },
        {
            "document": "report.pdf",
            "page": 53,
            "chunk_index": 0,
            "text": (
                "Liquidity coverage ratio Q3 2026 "
                "Combined USD EUR"
            ),
        },
    ]

    mock_ollama.return_value = {
        "answer": "The combined LCR was 160%.",
        "citation_ids": [1],
        "supported": True,
    }

    with patch(
            "app.answering.lookup_fact",
            return_value=(None, False),
    ):
        result = answer_question(
            "What was the combined LCR in Q3 2026?",
            DOCUMENT_ID,
        )

    assert result["supported"] is True
    assert result["sources"] == [
        {"document": "report.pdf", "page": 53}
    ]

    evidence = mock_ollama.call_args.args[1]

    assert evidence.index("Combined USD EUR") < (
        evidence.index("160% 229% 170%")
    )



@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_percentage_substring_is_rejected(
    mock_search, mock_ollama
):
    mock_search.return_value = [{
        "document": "annual_report.pdf",
        "page": 40,
        "chunk_index": 0,
        "text": "Q3 2026 CET1 ratio: 15.8%",
    }]

    mock_ollama.return_value = {
        "answer": "The CET1 ratio was 5.8%.",
        "citation_ids": [1],
        "supported": True,
    }

    result = answer_question(
        "What was the CET1 ratio?",
        document_id="test-document",
    )

    assert result["supported"] is False
    assert result["sources"] == []




@patch("app.answering._call_ollama")
@patch("app.answering.hybrid_search")
def test_quarterly_income_uses_focused_evidence(
    mock_search, mock_ollama
):
    mock_search.return_value = [
        {
            "document": "annual_report.pdf",
            "page": 7,
            "chunk_index": 0,
            "text": (
                "KEY FINANCIAL FIGURES\n"
                "Income statement, 12-quarter overview\n"
                "EURm Q326 Q226 Q126\n"
                "Total operating income 2,968 3,032 2,910"
            ),
        },
        {
            "document": "annual_report.pdf",
            "page": 7,
            "chunk_index": 1,
            "text": "IRRELEVANT RATIO DETAIL",
        },
    ]

    mock_ollama.return_value = {
        "answer": "EUR 2,968 million",
        "citation_ids": [1],
        "supported": True,
    }

    with patch(
            "app.answering.lookup_fact",
            return_value=(None, False),
    ):
        result = answer_question(
            "What was Nordea's total operating income in Q3 2026?",
            document_id="test-document",
        )

    evidence = mock_ollama.call_args.args[1]

    assert "Total operating income 2,968" in evidence
    assert "IRRELEVANT RATIO DETAIL" not in evidence

    assert result["supported"] is True
    assert result["sources"][0]["page"] == 7
