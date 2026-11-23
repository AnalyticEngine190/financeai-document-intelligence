"""Run the Day 14 holdout using a pre-start attested, non-mutating freeze check.

Required sequence:
  1. Stop FastAPI and Streamlit.
  2. `python -m tools.day14.verify_freeze --restore-index` (only if needed).
  3. `python -m tools.day14.verify_freeze --check` (strict raw Chroma hashes;
     creates a 36-hour pre-start attestation).
  4. Start FastAPI and leave it running.
  5. `python -m tools.day14.run_evaluation --first-run`.

After API startup, Chroma may legitimately rewrite SQLite/HNSW durable bytes.
The evaluator therefore validates the immutable freeze artifacts and the
pre-start attestation, then compares the live API's registered document IDs and
per-document chunk counts with the frozen inventory before sending any question.
It never restores or modifies the index during evaluation.
"""
from __future__ import annotations

import argparse
import os
from typing import Any


def _attested_freeze_check() -> None:
    from tools.day14.verify_freeze import verify_for_evaluation

    status = verify_for_evaluation()
    if status != 0:
        raise RuntimeError(
            "Day 14 attested freeze check failed; benchmark not sent. "
            "Stop services, restore/check the index, then retry."
        )


def _document_inventory_check(result: dict, expected_docs: list[dict], expected_collection_count: int) -> dict:
    """Add strict report metadata/chunk-count validation to the API preflight."""
    checked = dict(result)
    errors = list(checked.get("errors") or [])
    actual_docs = checked.get("actual_documents") or []
    actual_by_id = {
        str(doc.get("document_id")): doc
        for doc in actual_docs if isinstance(doc, dict) and doc.get("document_id")
    }
    expected_by_id = {
        str(doc.get("document_id")): doc
        for doc in expected_docs if isinstance(doc, dict) and doc.get("document_id")
    }
    if set(actual_by_id) != set(expected_by_id):
        errors.append(
            "Live API document registry differs from the frozen four-report inventory "
            f"(expected {len(expected_by_id)}, found {len(actual_by_id)})"
        )
    fields = ("filename", "issuer", "report_year", "report_quarter", "report_type",
              "currency", "sha256", "chunk_count")
    doc_diffs = []
    for document_id in sorted(set(actual_by_id) & set(expected_by_id)):
        actual = actual_by_id[document_id]
        expected = expected_by_id[document_id]
        changed = [field for field in fields if actual.get(field) != expected.get(field)]
        if changed:
            doc_diffs.append(f"{document_id[:12]}: {', '.join(changed)}")
    if doc_diffs:
        errors.append("Live API report metadata/chunk counts differ from freeze: " + "; ".join(doc_diffs))
    actual_chunks = sum(int(d.get("chunk_count", 0) or 0) for d in actual_by_id.values())
    if actual_chunks != int(expected_collection_count):
        errors.append(
            f"Live API registry reports {actual_chunks} chunks; frozen Chroma collection recorded {expected_collection_count}"
        )
    checked["index_inventory_check"] = {
        "passed": not errors,
        "expected_reports": len(expected_by_id),
        "actual_reports": len(actual_by_id),
        "expected_chunks": int(expected_collection_count),
        "actual_registry_chunks": actual_chunks,
        "errors": [e for e in errors if e not in (result.get("errors") or [])],
    }
    checked["errors"] = errors
    checked["passed"] = not errors
    return checked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--first-run", action="store_true", help="submit the held-out benchmark once")
    group.add_argument("--resume", action="store_true", help="resume an interrupted held-out run")
    parser.add_argument("--api-url", default=os.getenv("FINANCE_AI_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--market-live", action="store_true",
                        help="perform one optional Twelve Data listing search and at most one quote")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    from scripts import evaluate_day14
    from scripts.day14_common import FREEZE_DIR
    import json

    manifest_path = FREEZE_DIR / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("Day 14 freeze manifest not found. Do not create a second freeze; investigate first.")
    freeze_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = freeze_manifest.get("state") or {}
    expected_docs = (state.get("index_inventory") or {}).get("documents") or []
    expected_count = int((state.get("index_inventory") or {}).get("collection_count", -1))

    original_preflight = evaluate_day14._api_preflight

    def freeze_preflight(client, dataset):
        result = original_preflight(client, dataset)
        return _document_inventory_check(result, expected_docs, expected_count)

    # These are in-memory substitutions only. Frozen scripts/evaluate_day14.py
    # and scripts/day14_freeze.py remain byte-for-byte unchanged.
    evaluate_day14._check_freeze = _attested_freeze_check
    evaluate_day14._api_preflight = freeze_preflight
    evaluate_day14.evaluate(
        resume=args.resume,
        base_url=args.api_url,
        timeout=args.timeout,
        market_live=args.market_live,
    )


if __name__ == "__main__":
    main()
