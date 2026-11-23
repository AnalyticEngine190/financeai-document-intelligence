"""Replay the Day 14 gold set against the frozen PDFs without Chroma or HTTP.

This is a source/table/answer-policy diagnostic, not a retrieval benchmark. It
uses the original Day 14 JSON and PDF archive, verifies all SHA-256/page counts,
then supplies exact PDF page text directly to the app's normal chat answer path.
It never opens, rewrites, or rebuilds the active Chroma database.

Run from the project root:
    python -m tools.day14_offline_pdf_diagnostic
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "evaluation" / "day14_benchmark_80.json"
FREEZE_DIR = ROOT / "reports" / "evaluation" / "day14_freeze"
PDF_ARCHIVE = FREEZE_DIR / "source_pdfs_snapshot.zip"
DEFAULT_OUTPUT = ROOT / "reports" / "evaluation" / "day14_fixed_offline_replay.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_pdf_pages(dataset: dict[str, Any], extraction_dir: Path) -> tuple[dict, list]:
    from app.document_registry import DocumentRecord

    archive_hash = sha256_bytes(PDF_ARCHIVE.read_bytes())
    manifest = json.loads((FREEZE_DIR / "manifest.json").read_text(encoding="utf-8"))
    expected_archive_hash = manifest["artifacts"]["source_pdfs_snapshot_sha256"]
    if archive_hash != expected_archive_hash:
        raise RuntimeError("Frozen source-PDF archive hash does not match its manifest")

    pages_by_id: dict[str, list[dict[str, Any]]] = {}
    records: list[DocumentRecord] = []
    source_map = {
        "nq2": "Nordea_Q2_2026_Nordea second quarter 2026.pdf",
        "nq3": "Nordea_Q3_2026_annual_report.pdf",
        "seb": "SEB_Q3_2026_SEB Q3 2026.pdf",
        "hb": "Handelsbanken_Q3_2026_Handelsbanken Q3 2026.pdf",
    }
    with zipfile.ZipFile(PDF_ARCHIVE) as archive:
        for key, metadata in dataset["source_documents"].items():
            archive_name = "source_pdfs/" + source_map[key]
            raw = archive.read(archive_name)
            actual_hash = sha256_bytes(raw)
            if actual_hash != metadata["sha256"]:
                raise RuntimeError(f"PDF checksum mismatch for {key}")
            destination = extraction_dir / source_map[key]
            destination.write_bytes(raw)
            with fitz.open(destination) as pdf:
                if pdf.page_count != metadata["page_count"]:
                    raise RuntimeError(f"PDF page-count mismatch for {key}")
                pages_by_id[metadata["sha256"]] = [
                    {
                        "document": destination.name,
                        "page": page_number,
                        "text": page.get_text("text", sort=True) or "",
                    }
                    for page_number, page in enumerate(pdf, start=1)
                ]
            records.append(DocumentRecord(
                document_id=metadata["sha256"],
                filename=destination.name,
                issuer=metadata["issuer"],
                report_year=metadata["year"],
                report_quarter=metadata["quarter"],
                report_type=metadata["report_type"],
                currency=metadata["currency"],
                sha256=metadata["sha256"],
            ))
    return pages_by_id, records


def run(output_path: Path) -> dict[str, Any]:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    from app import document_answering as answering
    from app import document_registry as registry
    from app import document_tables, market_chat, market_routes, narrative_answering, table_lookup
    from app.document_resolver import DocumentSelection
    from scripts.evaluate_day14 import grade_answer

    with tempfile.TemporaryDirectory(prefix="financeai-day14-pdfs-") as temp:
        pages_by_id, documents = build_pdf_pages(dataset, Path(temp))

        def get_pages(document_id: str) -> list[dict[str, Any]]:
            return pages_by_id.get(document_id, [])

        # In-memory source injection only; no call is made to ChromaDB, model
        # embedding, the HTTP client, Twelve Data, or the network.
        table_lookup._get_pages = get_pages
        document_tables._get_pages = get_pages
        narrative_answering._get_pages = get_pages
        registry.list_documents = lambda: documents
        answering.list_documents = lambda: documents

        def answer_document(question: str, selection: Any) -> dict[str, Any]:
            return answering.answer_with_selection(
                question,
                DocumentSelection(
                    mode=selection.mode,
                    document_ids=tuple(selection.document_ids),
                ),
                documents=documents,
            )

        market_routes._doc = answer_document
        results = []
        for case in dataset["cases"]:
            if case["mode"] == "conversation":
                first = market_chat.process_chat(market_chat.ChatRequest(question=case["question"]))
                final = market_chat.process_chat(market_chat.ChatRequest(
                    question=case["followup"],
                    context_question=case["question"],
                    context_prompt=first.get("answer", ""),
                ))
                grade = grade_answer(case, final)
                initial_ok = (
                    first.get("needs_clarification") is True
                    and not first.get("supported")
                    and first.get("clarification_type") == case.get("clarification_type")
                    and not first.get("sources")
                )
                grade["initial_clarification_accuracy"] = initial_ok
                grade["passed"] = bool(grade["passed"] and initial_ok)
                response = final
            else:
                response = market_chat.process_chat(
                    market_chat.ChatRequest(question=case["question"])
                )
                grade = grade_answer(case, response)
            results.append({
                "case_id": case["id"],
                "question": case["question"],
                "grade": grade,
                "answer": response.get("answer"),
                "supported": response.get("supported"),
                "needs_clarification": response.get("needs_clarification"),
                "sources": response.get("sources", []),
            })

    passed = sum(bool(item["grade"].get("passed")) for item in results)
    summary = {
        "benchmark_id": dataset["benchmark_id"],
        "case_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "validation_mode": "fixed_source_pdf_replay",
        "chroma_retrieval_used": False,
        "http_server_used": False,
        "market_provider_called": False,
        "independent_holdout": False,
        "cases": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Day 14 fixed-source replay: {passed}/{len(results)} passed ({summary['pass_rate']:.1%})")
    print(f"Saved: {output_path}")
    if passed != len(results):
        for item in results:
            if not item["grade"].get("passed"):
                print(f"FAIL {item['case_id']}: {item['question']}\n  {item['answer']}\n  {item['sources']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="JSON result path (default: reports/evaluation/day14_fixed_offline_replay.json)")
    args = parser.parse_args()
    summary = run(args.output.resolve())
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
