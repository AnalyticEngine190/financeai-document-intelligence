"""Regression tests for deleting one indexed report by exact document ID."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import document_upload
from app.document_ingest import delete_indexed_document
from app.document_registry import DocumentRecord, save_document, remove_document_record


def _client():
    app = FastAPI()
    app.include_router(document_upload.router)
    return TestClient(app)


def test_delete_route_is_registered_on_existing_document_router():
    routes = {(route.path, method) for route in document_upload.router.routes
              for method in (getattr(route, "methods", None) or set())}
    assert ("/documents/{document_id}", "DELETE") in routes


def test_delete_route_deletes_selected_document_only():
    client = _client()
    with patch("app.document_upload.delete_indexed_document", return_value={
        "status": "deleted", "document_id": "jyske-id", "deleted_chunks": 8
    }) as delete:
        response = client.delete("/documents/jyske-id")
    assert response.status_code == 200
    assert response.json()["deleted_chunks"] == 8
    delete.assert_called_once_with("jyske-id")


def test_delete_route_returns_404_for_missing_document():
    client = _client()
    with patch("app.document_upload.delete_indexed_document", return_value={
        "status": "not_found", "document_id": "missing-id", "deleted_chunks": 0
    }):
        response = client.delete("/documents/missing-id")
    assert response.status_code == 404
    assert "not present" in response.json()["detail"]


def test_delete_helper_removes_only_chunks_matching_document_id(monkeypatch):
    class FakeCollection:
        def __init__(self):
            self.rows = {
                "jyske-1": {"document_id": "jyske-id"},
                "jyske-2": {"document_id": "jyske-id"},
                "nordea-1": {"document_id": "nordea-id"},
            }

        def get(self, where=None, include=None):
            ids = [key for key, meta in self.rows.items()
                   if not where or meta.get("document_id") == where["document_id"]]
            return {"ids": ids, "metadatas": [self.rows[key] for key in ids]}

        def delete(self, where=None):
            selected = [key for key, meta in self.rows.items()
                        if where and meta.get("document_id") == where["document_id"]]
            for key in selected:
                del self.rows[key]

    collection = FakeCollection()
    store = types.ModuleType("app.vector_store")
    store.get_collection = lambda: collection
    monkeypatch.setitem(sys.modules, "app.vector_store", store)
    with patch("app.document_ingest.remove_document_record") as remove:
        result = delete_indexed_document("jyske-id")
    assert result == {"status": "deleted", "document_id": "jyske-id", "deleted_chunks": 2}
    assert set(collection.rows) == {"nordea-1"}
    remove.assert_called_once_with("jyske-id")


def test_delete_helper_does_not_touch_other_records_when_id_is_missing(monkeypatch):
    class FakeCollection:
        def get(self, where=None, include=None):
            return {"ids": [], "metadatas": []}
        def delete(self, where=None):
            raise AssertionError("delete must not be called for a missing report")

    store = types.ModuleType("app.vector_store")
    store.get_collection = FakeCollection
    monkeypatch.setitem(sys.modules, "app.vector_store", store)
    with patch("app.document_ingest.remove_document_record") as remove:
        result = delete_indexed_document("missing-id")
    assert result["status"] == "not_found"
    remove.assert_not_called()


def test_registry_removal_preserves_other_report(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_AI_DOCUMENT_REGISTRY", str(tmp_path / "documents.json"))
    for doc_id, filename in (("jyske-id", "jyske.pdf"), ("nordea-id", "nordea.pdf")):
        save_document(DocumentRecord(document_id=doc_id, filename=filename, issuer=filename.split(".")[0]))
    assert remove_document_record("jyske-id") is True
    payload = json.loads((tmp_path / "documents.json").read_text(encoding="utf-8"))
    assert [item["document_id"] for item in payload["documents"]] == ["nordea-id"]
    assert remove_document_record("jyske-id") is False


def test_streamlit_ui_exposes_confirmed_single_report_deletion():
    from pathlib import Path
    import ast
    source = (Path(__file__).resolve().parents[1] / "streamlit_market_app.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert 'manage_documents_section(docs)' in source
    assert 'Delete selected report' in source
    assert 'I understand this permanently removes the selected report' in source
