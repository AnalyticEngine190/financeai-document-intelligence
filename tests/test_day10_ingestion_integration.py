"""Real PDF ingestion and metadata contract without requiring a running ChromaDB."""
from pathlib import Path
from unittest.mock import patch
import hashlib
import pytest

from app.document_ingest import ingest_pdf
from app.document_registry import list_documents
from app.document_resolver import resolve_documents, DocumentSelection


class FakeCollection:
    def __init__(self):
        self.records = {}
    def add(self, ids, documents, metadatas, embeddings):
        for i, text, meta in zip(ids, documents, metadatas):
            self.records[i] = (text, meta)
    def delete(self, ids):
        for id_ in ids:
            self.records.pop(id_, None)
    def get(self, where=None, include=()):
        matched = [(i,t,m) for i,(t,m) in self.records.items() if
                   not where or all(m.get(k)==v for k,v in where.items())]
        return {"ids":[i for i,_,_ in matched],
                "documents":[t for _,t,_ in matched] if "documents" in include else None,
                "metadatas":[m for _,_,m in matched] if "metadatas" in include else None}


def test_ingest_is_idempotent_and_auto_selects_new_report(tmp_path, monkeypatch):
    path = tmp_path / "seb_q3.pdf"
    path.write_bytes(b"%PDF-1.4 placeholder bytes for mocked text extraction")
    monkeypatch.setenv("FINANCE_AI_DOCUMENT_REGISTRY", str(tmp_path / "registry.json"))
    fake = FakeCollection()
    text = "SEB Q3 2026 interim report\nSEKm Q3 2026 Q2 2026\nNet interest income 100 95"
    with patch("app.vector_store.get_collection", return_value=fake), \
         patch("app.document_ingest._extract_pages", return_value=[text]), \
         patch("app.embeddings.embed_texts", side_effect=lambda xs:[[0.1,0.2] for _ in xs]):
        first = ingest_pdf(path, issuer="SEB", year=2026, quarter=3, currency="SEK")
        second = ingest_pdf(path, issuer="SEB", year=2026, quarter=3, currency="SEK")
        docs = list_documents()
        assert first["status"] == "indexed" and second["status"] == "already_indexed"
        assert len(docs) == 1 and len(fake.records) == 1
        assert first["document_id"] == hashlib.sha256(path.read_bytes()).hexdigest()
        decision = resolve_documents("What was SEB's net interest income in Q3 2026?", DocumentSelection(), docs)
        assert decision.documents[0].document_id == first["document_id"]
        with pytest.raises(ValueError, match="different report metadata"):
            ingest_pdf(path, issuer="Nordea", year=2026, quarter=3, currency="EUR")
