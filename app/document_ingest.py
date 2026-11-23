"""Idempotent multi-document PDF ingestion using existing Chroma/embeddings.

Does not delete or rewrite the original Nordea document. Uses the same
embed_texts() and collection as the existing project. Source pages are kept
separate and page_number is a physical 1-based PDF page for citations.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import re

from app.document_registry import record_from_pdf, save_document, get_document, remove_document_record


def _extract_pages(path: Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
        with fitz.open(str(path)) as pdf:
            if pdf.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported")
            return [page.get_text("text", sort=True) or "" for page in pdf]
    except ImportError:
        from pypdf import PdfReader
        pdf = PdfReader(str(path))
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        return [page.extract_text() or "" for page in pdf.pages]


def _page_chunks(text: str, target_chars: int = 1800) -> list[str]:
    """Line-aware chunks with no overlap: table rows stay in source order.

    Extremely long single lines can be larger than target_chars so that a
    single accounting table row is not split mid-value.
    """
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    group: list[str] = []
    length = 0
    for line in lines:
        if group and length + len(line) > target_chars:
            chunk = "".join(group).strip()
            if chunk:
                chunks.append(chunk)
            group, length = [], 0
        group.append(line)
        length += len(line)
    if group:
        chunk = "".join(group).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def ingest_pdf(path: str | Path, *, issuer: str, year: int, quarter: int,
               currency: str, report_type: str = "financial_report",
               title: str = "") -> dict:
    """Add a PDF once, keyed by actual SHA256. Never overwrite another PDF."""
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        raise FileNotFoundError(f"Not a readable PDF file: {source}")
    if not (2000 <= year <= 2100 and quarter in (1, 2, 3, 4)):
        raise ValueError("Year must be 2000–2100 and quarter must be 1–4")
    if not re.fullmatch(r"[A-Z]{3}", currency.upper()):
        raise ValueError("Currency must be an ISO-style three-letter code")
    if not issuer.strip():
        raise ValueError("Issuer is required")

    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    from app.vector_store import get_collection
    collection = get_collection()
    existing = collection.get(where={"document_id": sha}, include=["metadatas"])
    if existing.get("ids"):
        # Idempotent imports must never silently relabel a source document.
        known = get_document(sha)
        if known and (
            (known.issuer != "Unknown" and known.issuer.casefold() != issuer.strip().casefold())
            or (known.report_year is not None and known.report_year != year)
            or (known.report_quarter is not None and known.report_quarter != quarter)
            or (known.currency is not None and known.currency != currency.upper())
        ):
            raise ValueError("This PDF is already indexed with different report metadata")
        record = record_from_pdf(document_id=sha,
                                 filename=known.filename if known else source.name,
                                 issuer=known.issuer if known and known.issuer != "Unknown" else issuer.strip(),
                                 year=year, quarter=quarter, currency=currency.upper(),
                                 report_type=known.report_type if known else report_type,
                                 chunk_count=len(existing["ids"]),
                                 title=known.title if known else title)
        save_document(record)
        return {"status": "already_indexed", **record.public_dict()}

    pages = _extract_pages(source)
    if not pages or not any(t.strip() for t in pages):
        raise ValueError("The PDF has no extractable text. Scan/OCR ingestion is not enabled")
    ids, texts, metas = [], [], []
    for page_no, page_text in enumerate(pages, start=1):
        for chunk_index, chunk in enumerate(_page_chunks(page_text)):
            key = f"{sha}:p{page_no}:c{chunk_index}"
            ids.append(key)
            texts.append(chunk)
            metas.append({
                "document_id": sha,
                "filename": source.name,
                "page_number": page_no,
                "chunk_index": chunk_index,
                "issuer": issuer.strip(),
                "report_year": year,
                "report_quarter": quarter,
                "currency": currency.upper(),
            })
    if not ids:
        raise ValueError("No nonempty text chunks were extracted")
    from app.embeddings import embed_texts
    inserted = []
    try:
        for start in range(0, len(ids), 48):
            end = start + 48
            batch_ids, batch_texts, batch_meta = ids[start:end], texts[start:end], metas[start:end]
            embeddings = embed_texts(batch_texts)
            # Chroma accepts numpy ndarrays and Python lists; normalize both.
            if hasattr(embeddings, "tolist"):
                embeddings = embeddings.tolist()
            collection.add(ids=batch_ids, documents=batch_texts,
                           metadatas=batch_meta, embeddings=embeddings)
            inserted.extend(batch_ids)
        record = record_from_pdf(document_id=sha, filename=source.name,
                                 issuer=issuer.strip(), year=year, quarter=quarter,
                                 currency=currency.upper(), report_type=report_type,
                                 chunk_count=len(ids), title=title)
        save_document(record)
    except Exception:
        # No chunks with this SHA existed before the import, so delete all
        # intended IDs including an add() batch that failed partway through.
        if ids:
            try:
                collection.delete(ids=ids)
            except Exception:
                pass
        raise
    return {"status": "indexed", **record.public_dict()}



def delete_indexed_document(document_id: str) -> dict:
    """Delete one report's Chroma chunks and matching registry row by exact ID."""
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("A document ID is required")

    from app.vector_store import get_collection
    collection = get_collection()
    existing = collection.get(where={"document_id": document_id}, include=["metadatas"])
    chunk_ids = existing.get("ids") or []
    if not chunk_ids:
        return {"status": "not_found", "document_id": document_id, "deleted_chunks": 0}

    # The filter is the immutable document ID, never a bank or filename.
    collection.delete(where={"document_id": document_id})
    remove_document_record(document_id)
    return {"status": "deleted", "document_id": document_id, "deleted_chunks": len(chunk_ids)}
