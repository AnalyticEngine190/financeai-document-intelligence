"""Tests for POST /documents/ingest (upload one PDF, validate, index, report status).

The endpoint tests mock ingest_pdf so they need no Chroma or embedding model.
The pipeline tests run the real ingest_pdf against an in-memory fake collection
to check duplicate handling, failed-indexing rollback and document isolation.
"""
from __future__ import annotations

import hashlib
import sys
import types
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import fitz
import pytest

from app import document_upload
from app.document_registry import DocumentRecord

app = FastAPI()
app.include_router(document_upload.router)
client = TestClient(app)

META = {"issuer": "Jyske Bank", "year": "2026", "quarter": "3", "currency": "dkk",
        "report_type": "interim_report"}


def make_pdf(text: str = "Net interest income 1 234 5 678") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def post(data: bytes, filename: str = "jyske_q3_2026.pdf", **overrides):
    fields = {**META, **overrides}
    fields = {k: v for k, v in fields.items() if v is not None}
    return client.post("/documents/ingest", data=fields,
                       files={"file": (filename, data, "application/pdf")})


# ---------- endpoint: validation ----------

def test_valid_pdf_is_passed_to_existing_pipeline_with_normalised_metadata():
    fake = {"status": "indexed", "document_id": "abc", "filename": "jyske_q3_2026.pdf",
            "issuer": "Jyske Bank", "display_name": "Jyske Bank — Q3 2026 (interim_report)"}
    with patch("app.document_upload.ingest_pdf", return_value=fake) as m:
        r = post(make_pdf())
    assert r.status_code == 200
    assert r.json()["status"] == "indexed"
    assert r.json()["document"]["document_id"] == "abc"
    kwargs = m.call_args.kwargs
    assert kwargs["currency"] == "DKK" and kwargs["year"] == 2026 and kwargs["quarter"] == 3
    assert kwargs["issuer"] == "Jyske Bank" and kwargs["report_type"] == "interim_report"


def test_text_file_renamed_to_pdf_is_rejected_without_indexing():
    with patch("app.document_upload.ingest_pdf") as m:
        r = post(b"this is not a pdf at all", filename="fake.pdf")
    assert r.status_code == 422 and "not a PDF" in r.json()["detail"]
    m.assert_not_called()


def test_corrupt_pdf_with_valid_header_is_rejected_without_indexing():
    with patch("app.document_upload.ingest_pdf") as m:
        r = post(b"%PDF-1.7\n" + b"\x00garbage\xff" * 50, filename="broken.pdf")
    assert r.status_code == 422
    m.assert_not_called()


def test_empty_file_is_rejected():
    with patch("app.document_upload.ingest_pdf") as m:
        r = post(b"")
    assert r.status_code == 422
    m.assert_not_called()


def test_oversized_upload_is_413(monkeypatch):
    monkeypatch.setenv("FINANCE_AI_MAX_UPLOAD_MB", "0.0001")  # about 100 bytes
    with patch("app.document_upload.ingest_pdf") as m:
        r = post(make_pdf())
    assert r.status_code == 413
    m.assert_not_called()


def test_too_many_pages_is_rejected(monkeypatch):
    monkeypatch.setenv("FINANCE_AI_MAX_UPLOAD_PAGES", "1")
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "text")
    data = doc.tobytes()
    with patch("app.document_upload.ingest_pdf") as m:
        r = post(data)
    assert r.status_code == 422 and "limit" in r.json()["detail"]
    m.assert_not_called()


@pytest.mark.parametrize("override", [
    {"year": "1999"}, {"year": "2101"}, {"quarter": "0"}, {"quarter": "5"},
    {"currency": "EURO"}, {"currency": "EU"}, {"issuer": ""}, {"issuer": None},
    {"report_type": "poem"},
])
def test_incorrect_metadata_is_rejected_before_indexing(override):
    with patch("app.document_upload.ingest_pdf") as m:
        r = post(make_pdf(), **override)
    assert r.status_code == 422
    m.assert_not_called()


def test_path_traversal_filename_is_neutralised():
    with patch("app.document_upload.ingest_pdf", return_value={"status": "indexed"}) as m:
        r = post(make_pdf(), filename="../../etc/passwd")
    assert r.status_code == 200
    saved = m.call_args.args[0]
    assert saved.name == "passwd.pdf" and ".." not in str(saved)


# ---------- endpoint: pipeline outcomes ----------

def test_duplicate_upload_reports_already_indexed():
    fake = {"status": "already_indexed", "document_id": "abc", "issuer": "Jyske Bank"}
    with patch("app.document_upload.ingest_pdf", return_value=fake):
        r = post(make_pdf())
    assert r.status_code == 200 and r.json()["status"] == "already_indexed"


def test_same_file_with_conflicting_metadata_is_409():
    err = ValueError("This PDF is already indexed with different report metadata")
    with patch("app.document_upload.ingest_pdf", side_effect=err):
        r = post(make_pdf())
    assert r.status_code == 409


def test_scanned_pdf_gets_a_clear_unsupported_message():
    err = ValueError("The PDF has no extractable text. Scan/OCR ingestion is not enabled")
    with patch("app.document_upload.ingest_pdf", side_effect=err):
        r = post(make_pdf())
    assert r.status_code == 422 and "no extractable text" in r.json()["detail"]


def test_unexpected_indexing_failure_is_500_and_says_nothing_was_added():
    with patch("app.document_upload.ingest_pdf", side_effect=RuntimeError("chroma down")):
        r = post(make_pdf())
    assert r.status_code == 500
    assert "nothing was added" in r.json()["detail"]
    assert "chroma down" not in r.json()["detail"]  # no internal details leaked


def test_route_is_registered_on_the_real_api():
    from app.api_day10 import app as real_app
    paths = real_app.openapi()["paths"]  # independent of FastAPI's internal route classes
    assert "post" in paths["/documents/ingest"]
    assert "get" in paths["/documents"]  # existing list endpoint is untouched


# ---------- real ingest_pdf against a fake vector store ----------

class FakeCollection:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.texts: dict[str, str] = {}
        self.deleted: list[str] = []

    def get(self, where=None, include=None):
        ids = list(self.rows)
        if where:  # only the {"document_id": ...} filter is used by the pipeline
            ids = [i for i in ids if self.rows[i]["document_id"] == where["document_id"]]
        return {"ids": ids, "metadatas": [self.rows[i] for i in ids],
                "documents": [self.texts[i] for i in ids]}

    def add(self, ids, documents, metadatas, embeddings):
        for i, text, meta in zip(ids, documents, metadatas):
            self.rows[i], self.texts[i] = meta, text

    def delete(self, ids):
        self.deleted.extend(ids)
        for i in ids:
            self.rows.pop(i, None)
            self.texts.pop(i, None)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Fake Chroma + embeddings + temp registry so the real ingest_pdf can run."""
    collection = FakeCollection()
    store = types.ModuleType("app.vector_store")
    store.get_collection = lambda: collection
    emb = types.ModuleType("app.embeddings")
    emb.embed_texts = lambda texts: [[0.0, 1.0] for _ in texts]
    monkeypatch.setitem(sys.modules, "app.vector_store", store)
    monkeypatch.setitem(sys.modules, "app.embeddings", emb)
    monkeypatch.setenv("FINANCE_AI_DOCUMENT_REGISTRY", str(tmp_path / "documents.json"))
    return types.SimpleNamespace(collection=collection, emb=emb)


def test_second_upload_of_identical_bytes_adds_no_chunks(pipeline):
    data = make_pdf()
    first = post(data).json()
    count = len(pipeline.collection.rows)
    second = post(data).json()
    assert first["status"] == "indexed" and second["status"] == "already_indexed"
    assert len(pipeline.collection.rows) == count
    assert first["document"]["document_id"] == hashlib.sha256(data).hexdigest()


def test_identical_bytes_with_different_metadata_is_refused(pipeline):
    data = make_pdf()
    assert post(data).status_code == 200
    r = post(data, issuer="Nordea")
    assert r.status_code == 409
    assert len(pipeline.collection.rows) > 0


def test_failed_embedding_leaves_no_chunks_and_no_registry_entry(pipeline, tmp_path):
    def boom(texts):
        raise RuntimeError("embedding model unavailable")
    pipeline.emb.embed_texts = boom
    r = post(make_pdf())
    assert r.status_code == 500
    assert pipeline.collection.rows == {}
    assert not (tmp_path / "documents.json").exists()


def test_two_reports_never_share_a_document_id(pipeline):
    jyske, nordea = make_pdf("Jyske Bank net profit 100"), make_pdf("Nordea net profit 200")
    assert post(jyske).status_code == 200
    assert post(nordea, filename="nordea_q3_2026.pdf", issuer="Nordea", currency="EUR").status_code == 200
    by_issuer: dict[str, set] = {}
    for meta in pipeline.collection.rows.values():
        by_issuer.setdefault(meta["issuer"], set()).add(meta["document_id"])
    assert set(by_issuer) == {"Jyske Bank", "Nordea"}
    assert by_issuer["Jyske Bank"].isdisjoint(by_issuer["Nordea"])
    assert all(len(ids) == 1 for ids in by_issuer.values())
