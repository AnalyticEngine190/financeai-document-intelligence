"""Persistent metadata registry for source PDFs.

The Chroma index is the authority for *indexed content*. The JSON manifest
stores human-readable identity and report-edition metadata. Existing Day 9
Chroma documents are discovered without requiring re-ingestion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Iterable

_LOCK = threading.RLock()


def registry_path() -> Path:
    override = os.getenv("FINANCE_AI_DOCUMENT_REGISTRY")
    return Path(override).expanduser() if override else Path.home() / "FinanceAI_Local" / "documents.json"


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    filename: str
    issuer: str
    report_year: int | None = None
    report_quarter: int | None = None
    report_type: str = "financial_report"
    currency: str | None = None
    sha256: str | None = None
    title: str = ""
    chunk_count: int = 0
    indexed_at: str = ""

    @property
    def display_name(self) -> str:
        if self.title:
            return self.title
        edition = (f"Q{self.report_quarter} {self.report_year}"
                   if self.report_quarter and self.report_year else
                   str(self.report_year) if self.report_year else "Unknown edition")
        return f"{self.issuer} — {edition} ({self.report_type})"

    def public_dict(self) -> dict:
        d = asdict(self)
        d["display_name"] = self.display_name
        return d


def _read_manifest() -> dict[str, DocumentRecord]:
    path = registry_path()
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if obj.get("schema_version") != 1 or not isinstance(obj.get("documents"), list):
            raise ValueError("Unknown document registry format")
        return {d["document_id"]: DocumentRecord(**d) for d in obj["documents"]}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(f"Could not read the document registry at {path}: {exc}") from exc


def save_document(record: DocumentRecord) -> None:
    """Atomic manifest update, safe from partially-written JSON."""
    if not record.document_id or not record.filename or not record.issuer:
        raise ValueError("Document identity, filename and issuer are required")
    with _LOCK:
        documents = _read_manifest()
        documents[record.document_id] = record
        path = registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps({
                "schema_version": 1,
                "documents": [asdict(d) for d in sorted(documents.values(), key=lambda x: x.document_id)]
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()


def remove_document_record(document_id: str) -> bool:
    """Remove one document's metadata entry without changing other reports."""
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("A document ID is required")
    with _LOCK:
        documents = _read_manifest()
        if document_id not in documents:
            return False
        documents.pop(document_id)
        path = registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps({
                "schema_version": 1,
                "documents": [asdict(d) for d in sorted(documents.values(), key=lambda x: x.document_id)]
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return True


def _infer(source: str, filename: str) -> tuple[str, int | None, int | None, str]:
    """Infer only explicitly identifiable metadata; never guess the bank."""
    raw = (filename + " " + source[:2500]).replace("–", "-")
    low = raw.casefold()
    issuer = "Nordea" if re.search(r"\bnordea\b", low) else (
        "SEB" if re.search(r"\bseb\b|skandinaviska enskilda", low) else "Unknown")
    q = None
    yr = None
    # Prefer the source document's header/first pages over its filename.
    for pattern in (
        r"(?:first|second|third|fourth)[- ]quarter\s+(?:factbook|interim report)?\s*(20\d{2})\s*Q([1-4])",
        r"\bQ([1-4])\s*[- /]?\s*(20\d{2})\b",
        r"\b(20\d{2})\s*[- /]?\s*Q([1-4])\b",
        r"\bQ([1-4])(2\d)\b",
    ):
        m = re.search(pattern, raw, re.I)
        if not m:
            continue
        a, b = m.groups()
        if len(a) == 4:
            yr, q = int(a), int(b)
        elif len(b) == 4:
            yr, q = int(b), int(a)
        else:
            yr, q = 2000 + int(b), int(a)
        break
    if yr is None:
        y = re.search(r"\b(20\d{2})\b", raw)
        yr = int(y.group(1)) if y else None
    typ = "factbook" if "factbook" in low else "interim_report" if "interim" in low else "financial_report"
    return issuer, yr, q, typ


def _index_records() -> dict[str, DocumentRecord]:
    """Discover existing indexed PDFs, including the legacy annual_report.pdf."""
    from app.vector_store import get_collection
    records = get_collection().get(include=["metadatas", "documents"])
    groups: dict[str, list[tuple[dict, str]]] = {}
    for meta, chunk in zip(records.get("metadatas") or [], records.get("documents") or []):
        if not isinstance(meta, dict) or not meta.get("document_id"):
            continue
        groups.setdefault(str(meta["document_id"]), []).append((meta, chunk or ""))
    result = {}
    for document_id, items in groups.items():
        items.sort(key=lambda p: (int(p[0].get("page_number", 0)), int(p[0].get("chunk_index", 0))))
        meta, _ = items[0]
        filename = str(meta.get("filename") or f"{document_id[:12]}.pdf")
        front = " ".join(text for _, text in items[:4])
        issuer, year, quarter, kind = _infer(front, filename)
        result[document_id] = DocumentRecord(
            document_id=document_id, filename=filename, issuer=issuer,
            report_year=year, report_quarter=quarter, report_type=kind,
            sha256=document_id if re.fullmatch(r"[a-f0-9]{64}", document_id) else None,
            chunk_count=len(items),
        )
    return result


def list_documents() -> list[DocumentRecord]:
    """Only return PDFs that still have indexed chunks; never stale manifest entries."""
    with _LOCK:
        indexed = _index_records()
        manifest = _read_manifest()
        for doc_id, inferred in indexed.items():
            stored = manifest.get(doc_id)
            if stored:
                indexed[doc_id] = DocumentRecord(**{
                    **asdict(stored), "chunk_count": inferred.chunk_count,
                })
        return sorted(indexed.values(), key=lambda d: (
            d.issuer.casefold(), -(d.report_year or 0), -(d.report_quarter or 0), d.document_id
        ))


def get_document(document_id: str, documents: Iterable[DocumentRecord] | None = None) -> DocumentRecord | None:
    for item in documents if documents is not None else list_documents():
        if item.document_id == document_id:
            return item
    return None


def record_from_pdf(*, document_id: str, filename: str, issuer: str,
                    year: int | None, quarter: int | None, currency: str | None,
                    report_type: str, chunk_count: int, title: str = "") -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id, filename=filename, issuer=issuer,
        report_year=year, report_quarter=quarter, currency=currency,
        sha256=document_id, report_type=report_type, chunk_count=chunk_count,
        indexed_at=datetime.now(timezone.utc).isoformat(), title=title,
    )
