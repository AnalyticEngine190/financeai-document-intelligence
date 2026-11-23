"""Check table-derived answers bypass generation and retain precise citations."""

from unittest.mock import patch

from app.answering import answer_question
from app.table_lookup import TableFact


@patch("app.answering._call_ollama")
@patch("app.answering.lookup_fact")
@patch("app.answering.hybrid_search")
def test_verified_table_fact_has_exact_citation(mock_search, mock_lookup, mock_ollama):
    mock_search.return_value = [{
        "document": "report.pdf", "page": 4, "chunk_index": 0,
        "text": "Relevant source passage",
    }]
    mock_lookup.return_value = (
        TableFact("EUR 123 million", "report.pdf", 4,
                  "total operating income", "Q326", "123"),
        False,
    )
    result = answer_question("What was total operating income in Q3 2026?", "doc")
    assert result == {
        "answer": "EUR 123 million", "supported": True,
        "sources": [{"document": "report.pdf", "page": 4}],
    }
    mock_ollama.assert_not_called()


@patch("app.answering._call_ollama")
@patch("app.answering.lookup_fact")
@patch("app.answering.hybrid_search")
def test_unavailable_period_abstains_without_model(mock_search, mock_lookup, mock_ollama):
    mock_search.return_value = [{
        "document": "report.pdf", "page": 4, "chunk_index": 0,
        "text": "Historical period 2026",
    }]
    mock_lookup.return_value = (None, True)
    result = answer_question("What was total operating income in Q3 2035?", "doc")
    assert result["supported"] is False
    assert result["sources"] == []
    mock_ollama.assert_not_called()
