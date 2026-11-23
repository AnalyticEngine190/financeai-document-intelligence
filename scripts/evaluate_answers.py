"""Day 8: evaluate the running Financial Document Intelligence /ask API.

Uses only Python's standard library. The gold answers are NEVER sent to the API.

Examples:
    python -m scripts.evaluate_answers --validate-only
    python -m scripts.evaluate_answers --limit 5
    python -m scripts.evaluate_answers
    python -m scripts.evaluate_answers --category unsupported_period
    python -m scripts.evaluate_answers --case F03
"""

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import statistics
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "evaluation" / "nordea_q3_2026_50.json"
DEFAULT_OUTPUT = ROOT / "reports" / "evaluation"

# Match whole numbers, with thousands separators and optional decimal places.
# It must NOT accept 968 as a match inside 2,968.
NUMBER_RE = re.compile(
    r"(?<![\w.,])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,])"
)


def numeric_values(text: str) -> list[Decimal]:
    """Extract literal numeric values for exact matching (not substrings)."""
    results = []
    for match in NUMBER_RE.finditer(text):
        try:
            results.append(Decimal(match.group().replace(",", "")))
        except InvalidOperation:
            pass
    return results


def value_present(answer: str, expected: str, unit: str = "") -> bool:
    """Check the entire expected number, allowing comma/no-comma spelling.

    Equivalent EURm/EURbn scaling is accepted if the answer explicitly
    states the alternate scale. Other computations are intentionally excluded.
    """
    target = Decimal(expected.replace(",", ""))
    found = numeric_values(answer)
    lowered = answer.lower()
    mentions_million = bool(re.search(r"\b(?:millions?|eurm|mn)\b", lowered))
    mentions_billion = bool(re.search(r"\b(?:billions?|eurbn|bn)\b", lowered))

    # Explicit scales must be respected. Saying "2,968 billion" is NOT
    # an acceptable rendition of 2,968 million.
    if unit == "EURm" and mentions_billion and not mentions_million:
        return any(value * 1000 == target for value in found)
    if unit == "EURbn" and mentions_million and not mentions_billion:
        return any(value / 1000 == target for value in found)
    return target in found


def unit_status(answer: str, unit: str) -> str:
    """Presentation check: correct / missing / wrong / not_applicable.

    Missing units are flagged but don't invalidate an otherwise correct
    value+source result. Explicit incompatible units do invalidate it.
    """
    lower = answer.lower()
    has_pct = "%" in answer or bool(re.search(r"\bpercent(?:age)?\b", lower))
    has_usd = bool(re.search(r"\busd\b|\bdollars?\b|\$", lower))
    has_eur = "€" in answer or bool(re.search(r"\beur\b|\beuros?\b|\beurm\b|\beurbn\b", lower))
    has_million = bool(re.search(r"\bmillions?\b|\beurm\b|\bmn\b", lower))
    has_billion = bool(re.search(r"\bbillions?\b|\beurbn\b|\bbn\b", lower))

    if unit == "count":
        return "not_applicable"
    if unit == "percent":
        if has_pct:
            return "correct"
        if has_eur or has_usd:
            return "wrong"
        return "missing"
    if has_pct or has_usd:
        return "wrong"
    if unit == "EURm":
        if has_eur and has_million:
            return "correct"
        if has_eur and has_billion:
            return "correct"  # quantity conversion checked by value_present()
        if has_billion and not has_eur:
            return "missing"  # billion implied but currency unspecified
        return "missing"
    if unit == "EURbn":
        if has_eur and has_billion:
            return "correct"
        if has_eur and has_million:
            return "correct"  # scaled equivalent if numeric check passes
        return "missing"
    return "not_applicable"


def parse_cited_pages(sources) -> list[int]:
    if not isinstance(sources, list):
        return []
    pages = []
    for source in sources:
        if not isinstance(source, dict) or type(source.get("page")) is not int:
            continue
        pages.append(source["page"])
    return pages


def grade(case: dict, response: dict) -> dict:
    """Grade based on reported answer and citation; never the model's confidence."""
    gold = case["expected"]
    kind = gold["outcome"]
    answer = response.get("answer", "")
    answer = answer if isinstance(answer, str) else ""
    supported = response.get("supported") is True
    cited_pages = parse_cited_pages(response.get("sources"))
    clarification = response.get("needs_clarification") is True
    small_talk = response.get("is_small_talk") is True

    graded = {
        "passed": False,
        "numeric_ok": None,
        "citation_ok": None,
        "unit_status": None,
        "issue": "",
    }

    if kind == "answer":
        numeric_ok = value_present(answer, gold["value"], gold["unit"])
        expected_pages = set(gold["pages"])
        source_items = response.get("sources", [])
        filename_ok = all(
            isinstance(source, dict) and source.get("document") == "annual_report.pdf"
            for source in source_items
        )
        citation_ok = (
            bool(cited_pages)
            and len(cited_pages) == len(source_items)
            and set(cited_pages).issubset(expected_pages)
            and filename_ok
        )
        unit = unit_status(answer, gold["unit"])
        passed = supported and numeric_ok and citation_ok and not clarification and not small_talk and unit != "wrong"
        issues = []
        if not supported:
            issues.append("abstained_or_unsupported")
        if supported and not numeric_ok:
            issues.append("incorrect_or_missing_number")
        if not citation_ok:
            issues.append("wrong_or_missing_citation")
        if unit == "wrong":
            issues.append("incompatible_unit")
        if unit == "missing" and passed:
            issues.append("unit_missing_presentation_only")
        graded.update(passed=passed, numeric_ok=numeric_ok, citation_ok=citation_ok,
                      unit_status=unit, issue="; ".join(issues))

    elif kind == "abstain":
        # Refusal must be explicit; a false flag attached to a fabricated
        # number is not a safe abstention.
        lower = answer.casefold()
        refusal = any(marker in lower for marker in (
            "cannot verify", "can't verify", "could not verify",
            "not available", "not reported", "not provided",
            "not disclosed", "not included", "does not contain",
            "insufficient", "no information", "no verified",
            "unable to verify", "unable to confirm", "not found",
            "i don't have", "i cannot determine", "i can't determine",
        ))
        passed = (
            refusal and not supported and not clarification
            and not small_talk and not cited_pages
        )
        graded.update(passed=passed, issue="" if passed else "failed_to_abstain_cleanly")

    elif kind == "clarify":
        passed = clarification and not supported and not cited_pages and not small_talk and bool(answer.strip())
        graded.update(passed=passed, issue="" if passed else "did_not_ask_for_clarification")

    else:
        raise ValueError(f"Unrecognized expected outcome: {kind}")

    return graded


def validate_dataset(dataset: dict) -> None:
    """Reject invalid or duplicate gold cases before contacting the server."""
    cases = dataset.get("questions")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset requires a nonempty 'questions' list")
    ids = set()
    for case in cases:
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError(f"Missing or duplicate ID: {identifier!r}")
        ids.add(identifier)
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError(f"{identifier}: blank question")
        if not isinstance(case.get("category"), str):
            raise ValueError(f"{identifier}: category missing")
        gold = case.get("expected", {})
        kind = gold.get("outcome")
        if kind not in {"answer", "abstain", "clarify"}:
            raise ValueError(f"{identifier}: invalid expected outcome")
        if kind == "answer":
            if gold.get("unit") not in {"EURm", "EURbn", "percent", "count"}:
                raise ValueError(f"{identifier}: invalid unit")
            try:
                Decimal(str(gold["value"]).replace(",", ""))
            except (KeyError, InvalidOperation) as exc:
                raise ValueError(f"{identifier}: invalid reference value") from exc
            pages = gold.get("pages")
            if not isinstance(pages, list) or not pages or any(type(p) is not int or not 1 <= p <= 59 for p in pages):
                raise ValueError(f"{identifier}: invalid PDF page references")


def _get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        return json.load(reply)


def ask_api(api_url: str, question: str, timeout: float) -> dict:
    request = urllib.request.Request(
        api_url.rstrip("/") + "/ask",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        result = json.load(reply)
    if not isinstance(result, dict):
        raise ValueError("/ask returned a non-object JSON response")
    if (
        not isinstance(result.get("answer"), str)
        or type(result.get("supported")) is not bool
        or not isinstance(result.get("sources"), list)
    ):
        raise ValueError("/ask response is missing answer, supported or sources")
    return result


def summarize(results: list[dict]) -> str:
    total = len(results)
    passed = sum(item["passed"] for item in results)
    lines = [f"Overall strict result: {passed}/{total} ({100*passed/total:.1f}%)" if total else "No cases run"]
    kinds = [("answer", "Answerable"), ("abstain", "Must abstain"), ("clarify", "Must clarify")]
    for kind, label in kinds:
        subset = [item for item in results if item["expected_outcome"] == kind]
        if subset:
            success = sum(item["passed"] for item in subset)
            lines.append(f"  {label:<15}: {success}/{len(subset)} ({100*success/len(subset):.1f}%)")
    answerable = [item for item in results if item["expected_outcome"] == "answer"]
    if answerable:
        numeric = sum(item["numeric_ok"] is True for item in answerable)
        citations = sum(item["citation_ok"] is True for item in answerable)
        unit_scored = [item for item in answerable if item["unit_status"] != "not_applicable"]
        units = sum(item["unit_status"] == "correct" for item in unit_scored)
        lines += [f"  Number correct   : {numeric}/{len(answerable)}",
                  f"  Citation correct : {citations}/{len(answerable)}",
                  f"  Unit displayed   : {units}/{len(unit_scored)}"]
    for category in sorted(set(item["category"] for item in results)):
        subset = [item for item in results if item["category"] == category]
        lines.append(f"  {category:<23}: {sum(item['passed'] for item in subset)}/{len(subset)}")
    latencies = [item["latency_seconds"] for item in results if item["latency_seconds"] is not None]
    if latencies:
        lines.append(f"Median response time: {statistics.median(latencies):.2f}s")
    return "\n".join(lines)


def save_results(output_dir: Path, results: list[dict], dataset: dict, api_url: str) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_dir / f"nordea_eval_{suffix}"
    json_path, csv_path, md_path = [base.with_suffix(ext) for ext in (".json", ".csv", ".md")]
    report = {"benchmark": dataset.get("benchmark"), "dataset_version": dataset.get("version"),
              "api_url": api_url, "run_at_local": datetime.now().isoformat(),
              "summary": summarize(results), "results": results}
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    fields = ["id", "category", "expected_outcome", "question", "passed", "numeric_ok",
              "citation_ok", "unit_status", "expected_value", "expected_pages", "answer",
              "supported", "needs_clarification", "cited_pages", "latency_seconds", "issue", "error"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = {key: result.get(key) for key in fields}
            for key in ("expected_pages", "cited_pages"):
                row[key] = ",".join(map(str, row[key] or []))
            writer.writerow(row)

    def clean(text: object) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ")[:230]

    lines = ["# Nordea Q3 2026 RAG Evaluation", "", "```text", summarize(results), "```", "",
             "## Failed cases (manual review)", "",
             "| ID | Expected | Question | Assistant answer | Issue |", "|---|---|---|---|---|"]
    for result in results:
        if not result["passed"]:
            lines.append(f"| {result['id']} | {result['expected_outcome']} "
                         f"{result['expected_value']} | {clean(result['question'])} | "
                         f"{clean(result['answer'])} | {clean(result['issue'] or result['error'])} |")
    lines += ["", "## Notes", "",
              "- A correct numerical value without the correct PDF page does not pass.",
              "- Missing units are a presentation warning; explicitly incompatible units fail.",
              "- Abstention cases are judged against THIS PDF only, not public knowledge.",
              "- Manual review is still needed for misleading explanations or numbers on the wrong rows.", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, csv_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--api-url", default=os.getenv("FINANCE_AI_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=float, default=210.0)
    parser.add_argument("--category", help="Run just one category (e.g. unsupported_period)")
    parser.add_argument("--case", help="Run just one ID (e.g. F03)")
    parser.add_argument("--limit", type=int, help="Run first N questions in selected group")
    parser.add_argument("--validate-only", action="store_true", help="Check gold file without calling API")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    validate_dataset(dataset)
    cases = dataset["questions"]
    print(f"Dataset valid: {len(cases)} distinct questions")
    print(f"Reference PDF SHA256: {dataset.get('source_pdf_sha256', '')}")
    if args.validate_only:
        for kind in ("answer", "abstain", "clarify"):
            print(f"  {kind}: {sum(c['expected']['outcome'] == kind for c in cases)}")
        return

    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.case:
        cases = [c for c in cases if c["id"].lower() == args.case.lower()]
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be >= 1")
        cases = cases[:args.limit]
    if not cases:
        parser.error("No questions match this selection")

    try:
        health = _get_json(args.api_url.rstrip("/") + "/health", 4.0)
        if health.get("status") != "ok":
            raise ValueError(f"Unexpected health status: {health}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"FastAPI is unavailable at {args.api_url}: {exc}. Start uvicorn first.")

    print(f"Running {len(cases)} questions against {args.api_url}/ask")
    results = []
    try:
        for number, case in enumerate(cases, 1):
            expected = case["expected"]
            started = time.perf_counter()
            response, error = {}, ""
            try:
                response = ask_api(args.api_url, case["question"], args.timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    ValueError, OSError, json.JSONDecodeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            latency = round(time.perf_counter() - started, 2)
            result = grade(case, response)
            if error:
                result["passed"] = False
                result["issue"] = "api_or_network_error"
            record = {
                "id": case["id"], "category": case["category"],
                "expected_outcome": expected["outcome"], "question": case["question"],
                "expected_value": expected.get("value", ""), "expected_pages": expected.get("pages", []),
                "answer": response.get("answer", ""), "supported": response.get("supported"),
                "needs_clarification": response.get("needs_clarification", False),
                "cited_pages": parse_cited_pages(response.get("sources")),
                "latency_seconds": latency, "error": error, **result,
            }
            results.append(record)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"[{number:02d}/{len(cases):02d}] {case['id']} {status:<4} "
                  f"{latency:>6.2f}s | {case['question'][:76]}", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted. Saving completed results.")
    finally:
        if results:
            print("\n" + summarize(results))
            paths = save_results(args.output_dir, results, dataset, args.api_url)
            print("\nSaved reports:")
            for path in paths:
                print("  " + str(path))


if __name__ == "__main__":
    main()
