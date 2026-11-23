"""Run the held-out Day 14 benchmark against the frozen local FinanceAI API.

The benchmark is submitted only after the Day 13.19 freeze has been checked.
Results are append-only and never modify the gold dataset.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.day14_common import (
    DATASET, FIRST_RUN_DIR, ROOT, load_dataset, percentile, sha256_file,
)
from scripts.audit_day14_benchmark import audit as audit_benchmark

NUM_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:[, ]\d{3})+|\d+)(?:\.\d+)?")


def numeric_tokens(text: str) -> list[float]:
    values: list[float] = []
    for match in NUM_RE.finditer(text or ""):
        try:
            values.append(float(match.group(0).replace(",", "").replace(" ", "")))
        except ValueError:
            continue
    return values


def _consume_expected(actual: list[float], expected: list[float], tolerance: float) -> tuple[bool, list[dict]]:
    remaining = list(actual)
    detail = []
    all_ok = True
    for target in expected:
        found_index = next((i for i, value in enumerate(remaining)
                            if math.isclose(value, float(target), rel_tol=0.0, abs_tol=tolerance)), None)
        if found_index is None:
            detail.append({"expected": target, "found": False})
            all_ok = False
        else:
            value = remaining.pop(found_index)
            detail.append({"expected": target, "found": True, "matched": value})
    return all_ok, detail


def _source_pairs(sources: Any) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    if not isinstance(sources, list):
        return pairs
    for source in sources:
        if not isinstance(source, dict):
            continue
        document_id = source.get("document_id")
        page = source.get("page")
        if document_id is None or page is None:
            continue
        try:
            pairs.add((str(document_id), int(page)))
        except (TypeError, ValueError):
            continue
    return pairs


def _grade_sources(actual_sources: Any, expected_sources: list[dict]) -> dict:
    actual_pairs = _source_pairs(actual_sources)
    raw_source_count = len(actual_sources) if isinstance(actual_sources, list) else 0
    expected_pairs = {(str(s["document_id"]), int(s["page"])) for s in expected_sources}
    missing = sorted(expected_pairs - actual_pairs)
    # Do not fail a correct answer for an additional valid page in the same
    # expected document; however, an entirely unrelated document citation is
    # a source-contamination failure.
    expected_doc_ids = {doc_id for doc_id, _ in expected_pairs}
    actual_doc_ids = {doc_id for doc_id, _ in actual_pairs}
    unexpected_docs = sorted(actual_doc_ids - expected_doc_ids)
    return {
        "passed": not missing and not unexpected_docs and (bool(actual_pairs) if expected_pairs else raw_source_count == 0),
        "missing_expected_sources": [{"document_id": d, "page": p} for d, p in missing],
        "unexpected_document_ids": unexpected_docs,
        "actual_sources": [{"document_id": d, "page": p} for d, p in sorted(actual_pairs)],
    }


def _fragments(answer: str, required: list[str], forbidden: list[str] | None = None, required_any: list[str] | None = None) -> dict:
    folded = (answer or "").casefold()
    required = required or []
    required_any = required_any or []
    forbidden = forbidden or []
    missing = [item for item in required if item.casefold() not in folded]
    present_forbidden = [item for item in forbidden if item.casefold() in folded]
    any_matches = [item for item in required_any if item.casefold() in folded]
    any_failed = bool(required_any) and not any_matches
    return {"passed": not missing and not present_forbidden and not any_failed,
            "missing": missing, "present_forbidden": present_forbidden,
            "required_any_matched": any_matches,
            "required_any_failed": any_failed}


def grade_answer(case: dict, response: dict) -> dict:
    expected = case.get("expected", {})
    answer = str(response.get("answer") or "")
    supported_expected = bool(expected.get("supported", False))
    supported_actual = bool(response.get("supported", False))
    needs_actual = bool(response.get("needs_clarification", False))
    sources = response.get("sources") or []
    expected_sources = expected.get("sources") or []
    fragment_grade = _fragments(answer, expected.get("required_fragments", []),
                                expected.get("forbidden_fragments", []),
                                expected.get("required_any_fragments", []))
    citation_grade = _grade_sources(sources, expected_sources)

    numeric_detail = []
    numeric_ok = True
    if "numeric_values" in expected:
        numeric_ok, numeric_detail = _consume_expected(
            numeric_tokens(answer), expected.get("numeric_values") or [],
            float(expected.get("numeric_tolerance", 0.0)),
        )

    # Period correctness is anchored to the precise source-document IDs and
    # pages tied to the requested reporting edition, not merely a phrase in
    # the generated prose (which can repeat a correct-looking quarter label).
    period_ok = citation_grade["passed"] if expected_sources else not sources

    category = case["category"]
    clarification_grade = None
    abstention_grade = None
    out_of_scope_grade = None
    scope_grade = None

    if category == "clarification":
        expected_type = expected.get("clarification_type")
        actual_type = response.get("clarification_type")
        clarification_grade = (
            needs_actual and not supported_actual and not sources
            and (actual_type == expected_type if expected_type else True)
            and fragment_grade["passed"]
        )
    elif category in {"unsupported_period", "unsupported_forecast", "unsupported_field", "currency_safety"}:
        abstention_grade = (not supported_actual and not needs_actual and not sources
                            and fragment_grade["passed"])
    elif category == "out_of_scope":
        actual_oos = bool(response.get("out_of_scope")) or response.get("kind") == "out_of_scope"
        out_of_scope_grade = (actual_oos and not needs_actual and not sources
                              and fragment_grade["passed"])
    else:
        if expected.get("scope") == "segment":
            # Segment correctness requires citations to the dedicated segment
            # evidence pages supplied in the PDF-audited gold, never merely a
            # matching group-level amount on a group overview page.
            scope_grade = citation_grade["passed"]

    status_ok = (supported_actual == supported_expected
                 and needs_actual == bool(expected.get("needs_clarification", False)))
    component_values = [status_ok, fragment_grade["passed"], citation_grade["passed"], period_ok]
    if "numeric_values" in expected:
        component_values.append(numeric_ok)
    if clarification_grade is not None:
        component_values.append(clarification_grade)
    if abstention_grade is not None:
        component_values.append(abstention_grade)
    if out_of_scope_grade is not None:
        component_values.append(out_of_scope_grade)
    if scope_grade is not None:
        component_values.append(scope_grade)

    return {
        "passed": all(component_values),
        "status_correct": status_ok,
        "numeric_accuracy": numeric_ok if "numeric_values" in expected else None,
        "numeric_matches": numeric_detail,
        "period_accuracy": period_ok,
        "citation_accuracy": citation_grade["passed"],
        "citation_detail": citation_grade,
        "semantic_accuracy": fragment_grade["passed"],
        "semantic_detail": fragment_grade,
        "scope_accuracy": scope_grade,
        "clarification_accuracy": clarification_grade,
        "abstention_accuracy": abstention_grade,
        "out_of_scope_accuracy": out_of_scope_grade,
        "actual_supported": supported_actual,
        "actual_needs_clarification": needs_actual,
        "actual_kind": response.get("kind"),
    }


class APIClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = os.getenv("FINANCE_AI_MARKET_ACCESS_TOKEN", "")
        self.request_latencies: list[float] = []

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base_url + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Market-Token"] = self.token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.request_latencies.append(elapsed_ms)
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"_non_json_body": raw[:500].decode("utf-8", errors="replace")}
            return {"ok": 200 <= status < 300, "status_code": status,
                    "latency_ms": round(elapsed_ms, 2), "body": body}
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.request_latencies.append(elapsed_ms)
            raw = exc.read()
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"_non_json_body": raw[:500].decode("utf-8", errors="replace")}
            return {"ok": False, "status_code": exc.code,
                    "latency_ms": round(elapsed_ms, 2), "body": body}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.request_latencies.append(elapsed_ms)
            return {"ok": False, "status_code": None,
                    "latency_ms": round(elapsed_ms, 2),
                    "error": f"{type(exc).__name__}: {exc}"}


def _load_existing_results(path: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    if not path.exists():
        return results
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed results.jsonl line {line_no}; do not resume a corrupted run") from exc
        case_id = record.get("case_id")
        if case_id:
            results[str(case_id)] = record
    return results


def _api_preflight(client: APIClient, dataset: dict) -> dict:
    checks: dict[str, Any] = {}
    checks["health"] = client.request("GET", "/health")
    checks["documents"] = client.request("GET", "/documents")
    checks["market_status"] = client.request("GET", "/market/status")
    checks["openapi"] = client.request("GET", "/openapi.json")

    errors = []
    health = checks["health"]
    if not health["ok"] or (health.get("body") or {}).get("status") != "ok":
        errors.append("/health did not return status=ok")
    docs_resp = checks["documents"]
    actual_docs = (docs_resp.get("body") or {}).get("documents", []) if docs_resp["ok"] else []
    actual_ids = {str(d.get("document_id")) for d in actual_docs if isinstance(d, dict)}
    expected_ids = {str(meta["sha256"]) for meta in dataset["source_documents"].values()}
    if not docs_resp["ok"]:
        errors.append("/documents did not return HTTP 2xx")
    elif actual_ids != expected_ids:
        errors.append(f"/documents document IDs differ from four-report benchmark set; expected {len(expected_ids)}, found {len(actual_ids)}")
    if not checks["market_status"]["ok"]:
        errors.append("/market/status did not return HTTP 2xx (check X-Market-Token)")
    openapi = checks["openapi"].get("body") or {}
    if not checks["openapi"]["ok"] or "/chat_v1" not in (openapi.get("paths") or {}):
        errors.append("POST /chat_v1 is not registered on this API entrypoint")
    checks["actual_documents"] = actual_docs
    checks["errors"] = errors
    checks["passed"] = not errors
    return checks


def _market_integration_check(client: APIClient, market_status: dict, live: bool) -> dict:
    body = market_status.get("body") or {}
    if not market_status.get("ok"):
        return {"status": "status_endpoint_failed", "detail": market_status}
    if not body.get("configured"):
        return {"status": "not_configured", "provider": body.get("provider", "Twelve Data"),
                "note": "The API reports no server-side provider key. This is a deployment configuration result, not a financial-RAG score."}
    if not live:
        return {"status": "configured_not_live_checked", "provider": body.get("provider", "Twelve Data"),
                "note": "Pass --market-live to test one provider search and, if a NASDAQ AAPL candidate is returned, one quote."}

    search = client.request("GET", "/market/search?" + urllib.parse.urlencode({"query": "AAPL"}))
    if not search["ok"]:
        return {"status": "provider_search_failed", "provider": body.get("provider", "Twelve Data"),
                "search": search}
    candidates = (search.get("body") or {}).get("candidates", [])
    candidate = next((x for x in candidates if str(x.get("symbol", "")).upper() == "AAPL"
                      and "NASDAQ" in str(x.get("exchange", "")).upper()), None)
    if candidate is None:
        return {"status": "no_verified_nasdaq_candidate", "provider": body.get("provider", "Twelve Data"),
                "candidate_count": len(candidates), "search": search}
    quote_query = urllib.parse.urlencode({"symbol": candidate["symbol"], "exchange": candidate["exchange"]})
    quote = client.request("GET", "/market/quote?" + quote_query)
    return {"status": "quote_passed" if quote["ok"] else "quote_failed",
            "provider": body.get("provider", "Twelve Data"), "candidate": candidate,
            "search": search, "quote": quote}


def _chat_turn(client: APIClient, question: str, context_question: str | None = None,
               context_prompt: str | None = None) -> dict:
    payload: dict[str, Any] = {
        "question": question,
        "document_selection": {"mode": "auto", "document_ids": []},
    }
    if context_question:
        payload["context_question"] = context_question
    if context_prompt:
        payload["context_prompt"] = context_prompt
    return client.request("POST", "/chat_v1", payload)


def run_case(client: APIClient, case: dict) -> dict:
    started = time.perf_counter()
    first = _chat_turn(client, case["question"])
    steps = [{"request_question": case["question"], **first}]
    if not first.get("ok"):
        return {"case_id": case["id"], "category": case["category"], "question": case["question"],
                "transport_ok": False, "case_latency_ms": round((time.perf_counter()-started)*1000, 2),
                "transport_error": first.get("error") or (first.get("body") or {}).get("detail"),
                "steps": steps, "grade": {"passed": False, "reason": "HTTP/API transport failure"}}

    first_body = first.get("body") or {}
    if case["mode"] == "conversation":
        expected_type = case.get("clarification_type")
        first_grade = bool(first_body.get("needs_clarification")) and not bool(first_body.get("supported"))
        if expected_type:
            first_grade = first_grade and first_body.get("clarification_type") == expected_type
        if first_body.get("sources"):
            first_grade = False
        follow = _chat_turn(client, case["followup"], case["question"], str(first_body.get("answer") or ""))
        steps.append({"request_question": case["followup"], "context_question": case["question"],
                      "context_prompt": first_body.get("answer"), **follow})
        if follow.get("ok"):
            final_body = follow.get("body") or {}
            final_grade = grade_answer(case, final_body)
            final_grade["passed"] = bool(final_grade["passed"] and first_grade)
            final_grade["initial_clarification_accuracy"] = first_grade
            final_grade["initial_response"] = first_body
            final_grade["followup_response"] = final_body
        else:
            final_grade = {"passed": False, "initial_clarification_accuracy": first_grade,
                           "followup_transport_failed": True}
        return {"case_id": case["id"], "category": case["category"], "question": case["question"],
                "followup": case["followup"], "transport_ok": bool(follow.get("ok")),
                "case_latency_ms": round((time.perf_counter()-started)*1000, 2),
                "steps": steps, "grade": final_grade}

    grade = grade_answer(case, first_body)
    return {"case_id": case["id"], "category": case["category"], "question": case["question"],
            "transport_ok": True, "case_latency_ms": round((time.perf_counter()-started)*1000, 2),
            "steps": steps, "grade": grade}


def _metric_summary(records: list[dict], key: str) -> dict:
    eligible = [r for r in records if isinstance(r.get("grade", {}).get(key), bool)]
    passed = sum(bool(r["grade"][key]) for r in eligible)
    return {"passed": passed, "total": len(eligible), "rate": round(passed / len(eligible), 4) if eligible else None}


def _build_summary(dataset: dict, records: list[dict], preflight: dict,
                   client: APIClient, market_check: dict) -> dict:
    total = len(dataset["cases"])
    passed = sum(bool(r.get("grade", {}).get("passed")) for r in records)
    category = defaultdict(lambda: {"passed": 0, "total": 0, "case_ids_failed": []})
    for record in records:
        group = category[record["category"]]
        group["total"] += 1
        if record.get("grade", {}).get("passed"):
            group["passed"] += 1
        else:
            group["case_ids_failed"].append(record["case_id"])
    categories = {}
    for key, value in sorted(category.items()):
        categories[key] = {**value, "rate": round(value["passed"] / value["total"], 4) if value["total"] else None}

    latencies = [float(r.get("case_latency_ms", 0)) for r in records if r.get("case_latency_ms") is not None]
    recorded_request_latencies = [float(step["latency_ms"]) for record in records
                                  for step in record.get("steps", [])
                                  if step.get("latency_ms") is not None]
    transport_ok = sum(bool(r.get("transport_ok")) for r in records)
    all_requests = sum(len(r.get("steps", [])) for r in records)
    failed_requests = sum(1 for r in records for s in r.get("steps", []) if not s.get("ok"))
    retrieval_error_markers = {"document_backend_unavailable", "overview_index_unavailable",
                               "document_registry_unavailable", "overview_build_error"}
    retrieval_error_cases = [r["case_id"] for r in records if any(
        (step.get("body") or {}).get("error_code") in retrieval_error_markers
        for step in r.get("steps", []))]
    metric_keys = ["status_correct", "numeric_accuracy", "period_accuracy", "citation_accuracy",
                   "semantic_accuracy", "scope_accuracy", "clarification_accuracy",
                   "abstention_accuracy", "out_of_scope_accuracy",
                   "initial_clarification_accuracy"]
    metrics = {key: _metric_summary(records, key) for key in metric_keys}
    return {
        "benchmark_id": dataset["benchmark_id"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": total,
        "cases_recorded": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "pass_rate": round(passed / len(records), 4) if records else None,
        "by_category": categories,
        "metrics": metrics,
        "api": {
            "base_url": client.base_url,
            "preflight_passed": preflight.get("passed", False),
            "preflight_errors": preflight.get("errors", []),
            "question_cases_transport_ok": transport_ok,
            "question_cases_transport_total": len(records),
            "request_count": all_requests,
            "request_failures": failed_requests,
            "request_reliability": round((all_requests - failed_requests) / all_requests, 4) if all_requests else None,
            "retrieval_error_cases": retrieval_error_cases,
            "retrieval_error_case_count": len(retrieval_error_cases),
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "request_latency_p50_ms": percentile(recorded_request_latencies, 0.50),
            "request_latency_p95_ms": percentile(recorded_request_latencies, 0.95),
            "preflight_market_api_latency_ms": preflight.get("market_status", {}).get("latency_ms"),
        },
        "market_integration": market_check,
        "interpretation": (
            "A first-run score is a baseline for this exact frozen model, code, dependencies, and index. "
            "Do not tune against this benchmark and then present the same first-run score as independent."),
    }


def _write_report(out_dir: Path, dataset: dict, records: list[dict], summary: dict) -> None:
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_dir / "failures.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "category", "question", "failed_components", "answer", "sources"])
        writer.writeheader()
        for record in records:
            grade = record.get("grade", {})
            if grade.get("passed"):
                continue
            failed = [key for key, value in grade.items()
                      if (key.endswith("accuracy") or key == "status_correct") and value is False]
            if record.get("transport_ok") is False:
                failed.append("transport_ok")
            if grade.get("reason"):
                failed.append(str(grade["reason"]))
            answer = ""
            sources = []
            steps = record.get("steps", [])
            if steps:
                last_body = steps[-1].get("body") or {}
                answer = last_body.get("answer", "")
                sources = last_body.get("sources", [])
            writer.writerow({"case_id": record.get("case_id"), "category": record.get("category"),
                             "question": record.get("question"), "failed_components": "; ".join(failed),
                             "answer": answer, "sources": json.dumps(sources, ensure_ascii=False)})

    api = summary["api"]
    lines = [
        "# FinanceAI Day 14 — Independent Evaluation Report",
        "",
        f"**Benchmark:** `{dataset['benchmark_id']}`  ",
        f"**Run time (UTC):** {summary['created_utc']}  ",
        f"**Cases:** {summary['cases_recorded']} / {summary['case_count']}  ",
        f"**Overall:** {summary['passed']} passed, {summary['failed']} failed ({summary['pass_rate']:.1%} pass rate)." if summary.get("pass_rate") is not None else "**Overall:** no cases completed.",
        "",
        "## Scope and method",
        "",
        "This is a closed-document holdout against four exact indexed PDFs: Nordea Q2 2026, Nordea Q3 2026, SEB Q3 2026, and Handelsbanken Q3 2026. Every gold citation was audited against the SHA-256-matched source PDF, physical page number, and page-text anchor before the benchmark could run.",
        "The benchmark was submitted sequentially to the frozen local `POST /chat_v1` endpoint. Two conversation cases contain an initial clarification and a short follow-up; the runner passes the actual clarification prompt and original question into the second request.",
        "",
        "## Category results",
        "",
        "| Category | Passed | Total | Pass rate | Failed IDs |",
        "|---|---:|---:|---:|---|",
    ]
    for key, value in summary["by_category"].items():
        failed_ids = ", ".join(value["case_ids_failed"])
        rate = "n/a" if value["rate"] is None else f"{value['rate']:.1%}"
        lines.append(f"| {key} | {value['passed']} | {value['total']} | {rate} | {failed_ids} |")
    lines.extend(["", "## Reliability dimensions", "", "| Dimension | Passed | Eligible | Rate |", "|---|---:|---:|---:|"])
    labels = {
        "status_correct": "Supported/clarification state",
        "numeric_accuracy": "Numeric values",
        "period_accuracy": "Report-period / source edition",
        "citation_accuracy": "Source document and page citations",
        "semantic_accuracy": "Required semantic content / forbidden content",
        "scope_accuracy": "Segment-versus-Group scope",
        "clarification_accuracy": "Clarification behavior",
        "initial_clarification_accuracy": "Conversation-follow-up initial clarification",
        "abstention_accuracy": "Unsupported-answer abstention",
        "out_of_scope_accuracy": "Out-of-scope boundary",
    }
    for key, value in summary["metrics"].items():
        rate = "n/a" if value["rate"] is None else f"{value['rate']:.1%}"
        lines.append(f"| {labels.get(key, key)} | {value['passed']} | {value['total']} | {rate} |")
    lines.extend([
        "", "## Application performance", "",
        f"- API preflight: {'passed' if api['preflight_passed'] else 'failed'}.",
        f"- Chat requests: {api['request_count']}; HTTP/request failures: {api['request_failures']}; reliability: {api['request_reliability']:.1%}" if api.get("request_reliability") is not None else "- Chat request reliability: unavailable.",
        f"- Document retrieval/runtime failures reported by the API: {api['retrieval_error_case_count']} cases ({', '.join(api['retrieval_error_cases']) or 'none'}).",
        f"- End-to-end case latency: p50 {api['latency_p50_ms']} ms; p95 {api['latency_p95_ms']} ms.",
        f"- Individual API request latency: p50 {api['request_latency_p50_ms']} ms; p95 {api['request_latency_p95_ms']} ms.",
        f"- Market integration: `{summary['market_integration'].get('status')}`.",
        "",
        "## Interpretation and limitations",
        "",
        "- This score measures this frozen local build, this four-report corpus, and this 80-case benchmark. It does not establish reliability for arbitrary banks, reports, languages, markets, or production traffic.",
        "- Numerical grading checks that every gold numeric value appears in the answer; source grading checks the required document-ID/page pairs. Human review remains appropriate for nuanced accounting interpretation and narrative quality.",
        "- Market status and an optional live quote smoke are reported separately from financial-document accuracy. Provider entitlement failures are not counted as RAG answer failures.",
        "- The benchmark is a first-run baseline. Once results are inspected, it becomes development data; do not reuse it as an independent post-fix score. Create a fresh holdout for later versions.",
        "- Full response text, citations, individual latencies and grading details are preserved in `results.jsonl`; failed cases are also summarized in `failures.csv`.",
        "",
        "## Overall conclusion",
        "",
        (f"The frozen build passed {summary['passed']} of {summary['cases_recorded']} attempted cases. "
         "Treat failures by dimension and category rather than relying on one aggregate percentage."),
        "",
    ])
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    with (out_dir / "latency.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "category", "case_latency_ms", "transport_ok"])
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k) for k in ["case_id", "category", "case_latency_ms", "transport_ok"]})


def _check_freeze() -> None:
    proc = subprocess.run([sys.executable, "-m", "scripts.day14_freeze", "--check"],
                          cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError("Day 14 freeze check failed; benchmark not sent.\n" + (proc.stdout or "") + (proc.stderr or ""))
    print(proc.stdout.strip())


def validate_only(extra_pdf_dirs: list[Path] | None = None) -> None:
    data = load_dataset()
    result = audit_benchmark([ROOT / "data" / "raw", ROOT, Path.home() / "FinanceAI_Local", *(extra_pdf_dirs or [])])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["passed"]:
        raise RuntimeError("Day 14 benchmark dataset failed PDF/source validation")
    print(f"VALIDATE ONLY PASSED: {len(data['cases'])} held-out cases; no API requests were sent.")


def evaluate(*, resume: bool, base_url: str, timeout: float, market_live: bool) -> None:
    dataset = load_dataset()
    out_dir = FIRST_RUN_DIR
    results_path = out_dir / "results.jsonl"
    manifest_path = out_dir / "run_manifest.json"
    market_path = out_dir / "market_integration.json"
    preflight_path = out_dir / "api_preflight.json"
    if resume:
        if not out_dir.is_dir() or not results_path.is_file():
            raise RuntimeError("No interrupted Day 14 first-run exists to resume")
    elif out_dir.exists():
        raise RuntimeError(f"{out_dir} already exists. Use --resume or preserve this first-run; do not overwrite it.")

    # The freeze is checked before any benchmark request is submitted.
    _check_freeze()
    client = APIClient(base_url, timeout)
    preflight = _api_preflight(client, dataset)
    if not preflight["passed"]:
        raise RuntimeError("API preflight failed; no benchmark questions were submitted:\n" + "\n".join(preflight["errors"]))

    freeze_manifest_path = ROOT / "reports" / "evaluation" / "day14_freeze" / "manifest.json"
    current_run_identity = {
        "benchmark_id": dataset["benchmark_id"],
        "dataset_sha256": sha256_file(DATASET),
        "api_base_url": base_url,
        "freeze_manifest_sha256": sha256_file(freeze_manifest_path),
        "market_live_smoke_requested": market_live,
    }
    existing_manifest = None
    if resume and manifest_path.is_file():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key, expected_value in current_run_identity.items():
            if existing_manifest.get(key) != expected_value:
                raise RuntimeError(f"Existing Day 14 run manifest does not match current {key}; refusing resume")

    # Do not repeat the optional provider search/quote when resuming an
    # interrupted benchmark. It can consume API credits and its first-run
    # outcome should remain preserved as part of the run record.
    if resume and market_path.is_file():
        market_check = json.loads(market_path.read_text(encoding="utf-8"))
    else:
        market_check = _market_integration_check(client, preflight["market_status"], market_live)

    out_dir.mkdir(parents=True, exist_ok=True)
    if not preflight_path.exists():
        preflight_path.write_text(json.dumps(preflight, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    if not market_path.exists():
        market_path.write_text(json.dumps(market_check, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    if not manifest_path.exists():
        manifest = {
            **current_run_identity,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "note": "Do not tune on this benchmark and continue to call the same result independent.",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")

    existing = _load_existing_results(results_path) if resume else {}
    completed = set(existing)
    print(f"Day 14 first-run: {len(completed)}/{len(dataset['cases'])} already recorded; API preflight passed.", flush=True)

    with results_path.open("a" if resume else "w", encoding="utf-8") as handle:
        for index, case in enumerate(dataset["cases"], 1):
            if case["id"] in completed:
                continue
            record = run_case(client, case)
            record["sequence"] = index
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"[{index:02d}/{len(dataset['cases'])}] {case['id']} {case['category']}: "
                  f"{'PASS' if record.get('grade', {}).get('passed') else 'FAIL'} "
                  f"({record.get('case_latency_ms')} ms)", flush=True)

    records = list(_load_existing_results(results_path).values())
    # Keep canonical benchmark order even when the run was resumed.
    by_id = {r["case_id"]: r for r in records}
    ordered = [by_id[c["id"]] for c in dataset["cases"] if c["id"] in by_id]
    summary = _build_summary(dataset, ordered, preflight, client, market_check)
    _write_report(out_dir, dataset, ordered, summary)
    print("\nDAY 14 FIRST-RUN SUMMARY")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nPortfolio report: {out_dir / 'report.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate-only", action="store_true", help="audit benchmark; do not contact API")
    group.add_argument("--first-run", action="store_true", help="submit once to the frozen API")
    group.add_argument("--resume", action="store_true", help="continue an interrupted Day 14 run")
    parser.add_argument("--api-url", default=os.getenv("FINANCE_AI_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--market-live", action="store_true", help="make one AAPL provider search and up to one quote request")
    parser.add_argument("--pdf-dir", action="append", type=Path, default=[],
                        help="Additional PDF source directory for --validate-only; repeat as needed")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.validate_only:
        validate_only(args.pdf_dir)
    else:
        evaluate(resume=args.resume, base_url=args.api_url, timeout=args.timeout, market_live=args.market_live)


if __name__ == "__main__":
    main()
