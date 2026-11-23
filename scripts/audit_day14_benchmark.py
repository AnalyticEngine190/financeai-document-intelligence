"""Audit the 80-case Day 14 gold set against exact source-PDF hashes and pages."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from scripts.day14_common import DATASET, ROOT, find_pdf_files, load_dataset, normalized_text, sha256_file


def _extract_page_text(path: Path, page_number: int) -> str:
    if page_number < 1:
        raise ValueError(f"Physical page numbers are 1-indexed: {page_number}")
    try:
        import fitz
        with fitz.open(path) as pdf:
            if page_number > len(pdf):
                raise ValueError(f"PDF has {len(pdf)} pages, not {page_number}: {path.name}")
            return pdf[page_number - 1].get_text("text") or ""
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install PyMuPDF or pypdf to audit the PDFs") from exc
        reader = PdfReader(str(path))
        if page_number > len(reader.pages):
            raise ValueError(f"PDF has {len(reader.pages)} pages, not {page_number}: {path.name}")
        return reader.pages[page_number - 1].extract_text() or ""


def _pdf_page_count(path: Path) -> int:
    try:
        import fitz
        with fitz.open(path) as pdf:
            return len(pdf)
    except ImportError:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)


def _page_numbers(text: str) -> set[float]:
    """Extract numeric literals, normalizing comma/space thousands separators."""
    pattern = re.compile(r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:[, ]\d{3})+|\d+)(?:\.\d+)?")
    numbers: set[float] = set()
    for match in pattern.finditer(text or ""):
        try:
            numbers.add(float(match.group(0).replace(",", "").replace(" ", "")))
        except ValueError:
            continue
    return numbers


def audit(search_roots: list[Path], dataset_path: Path = DATASET) -> dict:
    data = load_dataset(dataset_path)
    cases = data["cases"]
    if data.get("case_count") != len(cases):
        raise ValueError("Benchmark case_count does not match its cases array")
    ids = [case.get("id") for case in cases]
    questions = [case.get("question", "").strip().casefold() for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate benchmark case IDs")
    if len(questions) != len(set(questions)):
        raise ValueError("Duplicate benchmark question strings")
    if not 60 <= len(cases) <= 100:
        raise ValueError(f"Expected 60-100 benchmark cases; found {len(cases)}")

    actual_counts: dict[str, int] = {}
    for case in cases:
        actual_counts[case["category"]] = actual_counts.get(case["category"], 0) + 1
    if actual_counts != data.get("category_counts"):
        raise ValueError("category_counts in benchmark metadata do not match cases")

    found = find_pdf_files(search_roots)
    doc_to_pdf: dict[str, Path] = {}
    missing_docs = []
    for key, metadata in data["source_documents"].items():
        digest = metadata["sha256"]
        if digest not in found:
            missing_docs.append({"document_key": key, "issuer": metadata["issuer"], "expected_sha256": digest,
                                 "expected_filename_hint": metadata["pdf"]})
        else:
            path = found[digest]
            doc_to_pdf[digest] = path
            expected_pages = metadata.get("page_count")
            if expected_pages is not None:
                try:
                    actual_pages = _pdf_page_count(path)
                    if actual_pages != int(expected_pages):
                        missing_docs.append({"document_key": key, "error": "page_count_mismatch",
                                             "expected_page_count": int(expected_pages),
                                             "actual_page_count": actual_pages, "path": str(path)})
                except Exception as exc:
                    missing_docs.append({"document_key": key, "error": f"page_count_unreadable: {exc}"})

    errors: list[str] = []
    checked_sources = 0
    checked_numeric_gold = 0
    checked_source_operand_values = 0
    calculation_checks = 0
    if not missing_docs:
        for case in cases:
            expected = case.get("expected", {})
            page_texts: list[tuple[dict, str, set[float], Path]] = []
            for source in expected.get("sources", []):
                doc_id = source["document_id"]
                path = doc_to_pdf.get(doc_id)
                if path is None:
                    errors.append(f"{case['id']}: source document ID not found in source_documents: {doc_id}")
                    continue
                try:
                    raw_text = _extract_page_text(path, int(source["page"]))
                    page_text = normalized_text(raw_text)
                    page_numbers = _page_numbers(raw_text)
                    for operand in source.get("numeric_values", []):
                        checked_source_operand_values += 1
                        if not any(abs(float(operand) - number) < 1e-9 for number in page_numbers):
                            errors.append(f"{case['id']}: source operand {operand} not found on physical page {source['page']} of {path.name}")
                except Exception as exc:
                    errors.append(f"{case['id']}: could not read {path.name} page {source['page']}: {exc}")
                    continue
                anchor = normalized_text(source["anchor"])
                if anchor not in page_text:
                    errors.append(f"{case['id']}: anchor {source['anchor']!r} not found on physical page {source['page']} of {path.name}")
                page_texts.append((source, page_text, page_numbers, path))
                checked_sources += 1

            # For direct single-value metric questions, verify that the gold
            # scalar is at least present on its cited physical page in addition
            # to the row-label anchor. This is a presence check, not a substitute
            # for human review of table column alignment/accounting semantics.
            numeric_values = expected.get("numeric_values") or []
            sources = expected.get("sources") or []
            if case.get("category") in {"single_metric", "segment_metric"} and len(numeric_values) == 1 and len(sources) == 1 and page_texts:
                target = float(numeric_values[0])
                checked_numeric_gold += 1
                if not any(abs(target - number) < 1e-9 for number in page_texts[0][2]):
                    source = sources[0]
                    errors.append(f"{case['id']}: numeric gold value {numeric_values[0]} not found on cited physical page {source['page']} of {page_texts[0][3].name}")

    for case in cases:
        check = (case.get("expected") or {}).get("calculation_check")
        if not check:
            continue
        calculation_checks += 1
        initial = float(check["initial"])
        final = float(check["final"])
        actual_change = final - initial
        expected_change = float(check["expected_signed_change"])
        if abs(actual_change - expected_change) > 1e-9:
            errors.append(f"{case['id']}: calculation gold mismatch; {final} - {initial} = {actual_change}, expected {expected_change}")
        if "expected_signed_percentage_change" in check:
            if initial == 0:
                errors.append(f"{case['id']}: percentage calculation has zero initial operand")
            else:
                digits = int(check.get("round_digits", 1))
                actual_pct = round((actual_change / initial) * 100, digits)
                expected_pct = float(check["expected_signed_percentage_change"])
                if abs(actual_pct - expected_pct) > 1e-9:
                    errors.append(f"{case['id']}: percentage gold mismatch; calculated {actual_pct}, expected {expected_pct}")

    return {
        "benchmark_id": data.get("benchmark_id"),
        "benchmark_sha256": sha256_file(dataset_path),
        "case_count": len(cases),
        "category_counts": actual_counts,
        "unique_question_count": len(set(questions)),
        "source_anchor_count": checked_sources,
        "single_value_gold_presence_checks": checked_numeric_gold,
        "source_operand_presence_checks": checked_source_operand_values,
        "calculation_gold_checks": calculation_checks,
        "source_pdfs_found": len(doc_to_pdf),
        "source_pdf_count": len(data["source_documents"]),
        "missing_source_pdfs": missing_docs,
        "errors": errors,
        "passed": not missing_docs and not errors,
        "source_pdf_hashes": {key: value["sha256"] for key, value in data["source_documents"].items()},
        "source_pdf_page_counts": {key: value.get("page_count") for key, value in data["source_documents"].items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", action="append", type=Path, default=[],
                        help="Additional directory or PDF path to search; repeat as needed")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    roots = [ROOT, ROOT / "data" / "raw", Path.home() / "FinanceAI_Local", *args.pdf_dir]
    result = audit(roots, args.dataset)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    if not result["passed"]:
        print("BENCHMARK PDF AUDIT FAILED. Do not run the independent first-run.", file=sys.stderr)
        raise SystemExit(2)
    print(f"BENCHMARK PDF AUDIT PASSED: {result['case_count']} cases, {result['source_anchor_count']} anchors, {result['single_value_gold_presence_checks']} direct-value checks, {result['source_operand_presence_checks']} operand checks, {result['calculation_gold_checks']} calculation checks, {result['source_pdf_count']} exact PDFs.")


if __name__ == "__main__":
    main()
