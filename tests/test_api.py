
"""Tests for the Financial Document Intelligence HTTP API."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import app


client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("app.api._document_id", return_value="test-doc")
@patch("app.api.answer_question")
def test_ask_supported(mock_answer, mock_document):
    mock_answer.return_value = {
        "answer": "15.8%",
        "supported": True,
        "sources": [
            {"document": "annual_report.pdf", "page": 40}
        ],
    }

    response = client.post(
        "/ask",
        json={"question": "What is the CET1 ratio?"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "15.8%"
    assert response.json()["supported"] is True
    assert response.json()["sources"][0]["page"] == 40

    mock_answer.assert_called_once_with(
        "What is the CET1 ratio?",
        document_id="test-doc",
    )


@patch("app.api._document_id", return_value="test-doc")
@patch("app.api.answer_question")
def test_ask_abstains(mock_answer, mock_document):
    mock_answer.return_value = {
        "answer": "I cannot verify the answer.",
        "supported": False,
        "sources": [],
    }

    response = client.post(
        "/ask",
        json={"question": "What was revenue in 2035?"},
    )

    assert response.status_code == 200
    assert response.json()["supported"] is False
    assert response.json()["sources"] == []


def test_rejects_empty_question():
    response = client.post(
        "/ask",
        json={"question": "   "},
    )

    assert response.status_code == 422


@patch("app.api._document_id", return_value="test-doc")
@patch("app.api.answer_question")
def test_model_failure_returns_503(
    mock_answer, mock_document
):
    mock_answer.side_effect = RuntimeError(
        "Ollama unavailable"
    )

    response = client.post(
        "/ask",
        json={"question": "What is the CET1 ratio?"},
    )

    assert response.status_code == 503



@patch("app.api._document_id", return_value="test-doc")
@patch("app.api.answer_question")
def test_api_returns_clarification(mock_answer, mock_document):
    mock_answer.return_value = {
        "answer": (
            "Which revenue measure do you mean: "
            "total income, net interest income, or "
            "net fee and commission income?"
        ),
        "supported": False,
        "sources": [],
        "needs_clarification": True,
    }

    response = client.post(
        "/ask",
        json={"question": "What was the revenue?"},
    )

    assert response.status_code == 200
    assert response.json()["needs_clarification"] is True
    assert response.json()["sources"] == []
