from fastapi.testclient import TestClient
from unittest.mock import patch
from app.api_day10 import app
from app.document_registry import DocumentRecord

client = TestClient(app)
DOC = DocumentRecord("nq3", "annual_report.pdf", "Nordea", 2026, 3, "factbook", "EUR")


def test_existing_health_route_is_preserved():
    assert client.get("/health").json()["status"] == "ok"


def test_document_endpoint_returns_issuer_and_id():
    with patch("app.api_day10.list_documents", return_value=[DOC]):
        r = client.get("/documents")
    assert r.status_code == 200
    assert r.json()["documents"][0]["document_id"] == "nq3"


def test_ask_v2_defaults_to_auto_and_return_sources():
    fake = {"answer":"15.8%", "supported":True,
            "sources":[{"document":"annual_report.pdf", "page":40, "document_id":"nq3"}],
            "selection_reason":"Exact edition match", "selected_documents":[DOC.public_dict()]}
    with patch("app.api_day10.answer_with_selection", return_value=fake) as m:
        r = client.post("/ask_v2", json={"question":"What was the CET1 ratio?"})
    assert r.status_code == 200 and r.json()["supported"] is True
    assert m.call_args.args[1].mode == "auto"


def test_invalid_manual_id_is_400():
    with patch("app.api_day10.answer_with_selection", side_effect=ValueError("Missing id")):
        r = client.post("/ask_v2", json={
            "question":"What was net interest income?",
            "document_selection":{"mode":"manual", "document_ids":["bad"]}})
    assert r.status_code == 400


def test_blank_question_422():
    r = client.post("/ask_v2",json={"question":" "})
    assert r.status_code == 422
