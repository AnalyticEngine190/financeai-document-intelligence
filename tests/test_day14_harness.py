"""Offline tests for the Day 14 evaluation harness (do not call Ollama)."""

import pytest

from scripts.day14_common import DATASET, load_dataset, percentile
from scripts.evaluate_day14 import grade_answer, numeric_tokens, _fragments

requires_private_dataset = pytest.mark.skipif(
    not DATASET.is_file(),
    reason="Private Day 14 holdout benchmark is not included in the public repository",
)


@requires_private_dataset
def test_holdout_has_80_unique_cases_and_expected_category_counts():
    dataset = load_dataset()
    assert dataset["benchmark_id"] == "financeai_day14_holdout_80_v1"
    assert len(dataset["cases"]) == 80
    assert len({case["id"] for case in dataset["cases"]}) == 80
    assert len({case["question"].casefold() for case in dataset["cases"]}) == 80
    counts = {}
    for case in dataset["cases"]:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    assert counts == dataset["category_counts"]


def test_numeric_parser_handles_thousands_decimals_and_negative_values():
    assert numeric_tokens("SEK 1,941 million, down 13.5%; -27 SEK m; EPS 0.35") == [
        1941.0, 13.5, -27.0, 0.35
    ]


def test_answer_grade_requires_all_values_and_exact_document_page_citations():
    case = {
        "id": "TEST-1", "category": "calculation", "mode": "answer",
        "expected": {
            "supported": True, "needs_clarification": False,
            "numeric_values": [1166, 13.5], "numeric_tolerance": 0.11,
            "sources": [{"document_id": "seb-q3", "page": 5}],
            "required_fragments": ["decrease"], "required_any_fragments": [],
            "forbidden_fragments": [], "period": "Q2 to Q3 2026",
        },
    }
    response = {
        "supported": True, "needs_clarification": False,
        "answer": "SEK 1,166 million decrease (13.5% decline) from Q2 to Q3 2026.",
        "sources": [{"document_id": "seb-q3", "page": 5, "document": "SEB Q3"}],
    }
    grade = grade_answer(case, response)
    assert grade["passed"] is True
    assert grade["numeric_accuracy"] is True
    assert grade["citation_accuracy"] is True
    assert grade["period_accuracy"] is True

    wrong_page = {**response, "sources": [{"document_id": "seb-q3", "page": 6}]}
    assert grade_answer(case, wrong_page)["passed"] is False

    missing_value = {**response, "answer": "SEK 1,166 million decrease."}
    assert grade_answer(case, missing_value)["numeric_accuracy"] is False


def test_segment_grade_fails_if_group_page_is_cited_instead_of_segment_page():
    case = {
        "id": "TEST-SEG", "category": "segment_metric", "mode": "answer",
        "expected": {
            "supported": True, "needs_clarification": False,
            "numeric_values": [1941], "numeric_tolerance": 0,
            "sources": [{"document_id": "seb-q3", "page": 21}],
            "required_fragments": ["Baltic"], "required_any_fragments": [],
            "forbidden_fragments": [], "scope": "segment",
        },
    }
    correct = {
        "supported": True, "needs_clarification": False,
        "answer": "SEB Baltic operating profit was SEK 1,941 million.",
        "sources": [{"document_id": "seb-q3", "page": 21}],
    }
    assert grade_answer(case, correct)["scope_accuracy"] is True
    group_page = {**correct, "sources": [{"document_id": "seb-q3", "page": 5}]}
    assert grade_answer(case, group_page)["scope_accuracy"] is False


def test_clarification_grade_requires_expected_type_and_no_sources():
    case = {
        "id": "TEST-CLAR", "category": "clarification", "mode": "clarification",
        "expected": {"supported": False, "needs_clarification": True,
                      "clarification_type": "metric", "sources": [],
                      "required_fragments": ["which financial measure"],
                      "required_any_fragments": ["net profit", "operating profit"]},
    }
    good = {"supported": False, "needs_clarification": True,
            "clarification_type": "metric", "kind": "document",
            "answer": "Which financial measure do you mean: net profit or operating profit?", "sources": []}
    assert grade_answer(case, good)["clarification_accuracy"] is True
    wrong_type = {**good, "clarification_type": "issuer"}
    assert grade_answer(case, wrong_type)["clarification_accuracy"] is False


def test_abstention_accepts_one_of_alternative_phrases_but_never_cites_sources():
    case = {
        "id": "TEST-ABSTAIN", "category": "unsupported_period", "mode": "abstention",
        "expected": {"supported": False, "needs_clarification": False, "sources": [],
                      "required_fragments": ["Q4 2026"],
                      "required_any_fragments": ["not available", "not in the selected report"]},
    }
    good = {"supported": False, "needs_clarification": False,
            "answer": "Q4 2026 results are not available in the selected report.", "sources": []}
    assert grade_answer(case, good)["abstention_accuracy"] is True
    cited = {**good, "sources": [{"document_id": "hb-q3", "page": 24}]}
    assert grade_answer(case, cited)["passed"] is False
    assert _fragments("not available", [], [], ["not available", "not in report"])["passed"] is True


def test_out_of_scope_kind_is_accepted_even_without_extra_boolean_flag():
    case = {"id": "TEST-OOS", "category": "out_of_scope", "mode": "out_of_scope",
            "expected": {"supported": False, "needs_clarification": False,
                          "out_of_scope": True, "sources": [],
                          "required_fragments": ["outside my scope"],
                          "required_any_fragments": []}}
    response = {"kind": "out_of_scope", "supported": False,
                "needs_clarification": False,
                "answer": "That question is outside my scope.", "sources": []}
    assert grade_answer(case, response)["out_of_scope_accuracy"] is True


def test_percentile_uses_interpolated_p50_and_p95():
    assert percentile([], 0.5) is None
    assert percentile([10], 0.95) == 10
    assert percentile([10, 20, 30, 40], 0.5) == 25
    assert percentile([10, 20, 30, 40], 0.95) == 38.5


@requires_private_dataset
def test_conversation_cases_specify_clarification_slots_and_are_two_turns():
    data = load_dataset()
    conv = [case for case in data["cases"] if case["mode"] == "conversation"]
    assert len(conv) == 2
    assert {case["clarification_type"] for case in conv} == {"metric", "issuer"}
    assert all(case.get("followup") for case in conv)


def test_conversation_runner_passes_previous_question_and_clarification_prompt():
    from scripts.evaluate_day14 import run_case

    class FakeClient:
        def __init__(self):
            self.calls = []

        def request(self, method, path, payload=None):
            self.calls.append((method, path, payload))
            if len(self.calls) == 1:
                return {"ok": True, "status_code": 200, "latency_ms": 10.0,
                        "body": {"kind": "document", "supported": False,
                                 "needs_clarification": True, "clarification_type": "metric",
                                 "answer": "Which financial measure do you mean?", "sources": []}}
            return {"ok": True, "status_code": 200, "latency_ms": 12.0,
                    "body": {"kind": "document", "supported": True,
                             "needs_clarification": False,
                             "answer": "Net interest income was EUR 1,799 million in Q3 2026.",
                             "sources": [{"document_id": "nq3", "page": 7}]}}

    case = {
        "id": "D14-CONV-TEST", "category": "conversation_followup", "mode": "conversation",
        "question": "How much sales did Nordea generate during Q3 2026?",
        "followup": "net interest income", "clarification_type": "metric",
        "expected": {"supported": True, "needs_clarification": False,
                     "numeric_values": [1799], "numeric_tolerance": 0,
                     "sources": [{"document_id": "nq3", "page": 7}],
                     "required_fragments": ["Q3 2026"], "required_any_fragments": []},
    }
    fake = FakeClient()
    result = run_case(fake, case)
    assert result["grade"]["passed"] is True
    assert fake.calls[1][2]["context_question"] == case["question"]
    assert fake.calls[1][2]["context_prompt"] == "Which financial measure do you mean?"


def test_numeric_parser_accepts_spaced_thousands_without_crossing_lines():
    assert numeric_tokens("SEK 1 941 million; EUR 1,799 million; tax -1,601") == [1941.0, 1799.0, -1601.0]


@requires_private_dataset
def test_api_preflight_checks_exact_four_docs_and_chat_route():
    from scripts.evaluate_day14 import _api_preflight

    dataset = load_dataset()
    expected_ids = {meta["sha256"] for meta in dataset["source_documents"].values()}

    class FakeClient:
        def __init__(self, include_chat=True):
            self.include_chat = include_chat

        def request(self, method, path, payload=None):
            if path == "/health":
                return {"ok": True, "body": {"status": "ok"}}
            if path == "/documents":
                return {"ok": True, "body": {"documents": [{"document_id": x} for x in expected_ids]}}
            if path == "/market/status":
                return {"ok": True, "body": {"configured": False}}
            if path == "/openapi.json":
                paths = {"/chat_v1": {}} if self.include_chat else {}
                return {"ok": True, "body": {"paths": paths}}
            raise AssertionError(path)

    assert _api_preflight(FakeClient(), dataset)["passed"] is True
    failed = _api_preflight(FakeClient(include_chat=False), dataset)
    assert failed["passed"] is False
    assert any("/chat_v1" in message for message in failed["errors"])


def test_market_not_configured_is_reported_separately_without_provider_calls():
    from scripts.evaluate_day14 import _market_integration_check

    class FakeClient:
        def request(self, *args, **kwargs):
            raise AssertionError("market search must not run when the server key is absent")

    result = _market_integration_check(FakeClient(), {"ok": True, "body": {"configured": False}}, live=True)
    assert result["status"] == "not_configured"


@requires_private_dataset
def test_calculation_gold_operands_and_percentage_changes_are_consistent():
    data = load_dataset()
    calculations = [c for c in data["cases"] if c.get("expected", {}).get("calculation_check")]
    assert len(calculations) == 8
    for case in calculations:
        check = case["expected"]["calculation_check"]
        change = float(check["final"]) - float(check["initial"])
        assert change == float(check["expected_signed_change"])
        if "expected_signed_percentage_change" in check:
            pct = round(change / float(check["initial"]) * 100, int(check.get("round_digits", 1)))
            assert pct == float(check["expected_signed_percentage_change"])


@requires_private_dataset
def test_day14_source_inventory_pins_pdf_lengths_and_known_hashes():
    data = load_dataset()
    assert len(data["source_documents"]) == 4
    assert {k: v["page_count"] for k, v in data["source_documents"].items()} == {
        "nq2": 59, "nq3": 59, "seb": 57, "hb": 67,
    }
    assert all(len(v["sha256"]) == 64 for v in data["source_documents"].values())


@requires_private_dataset
def test_scope_comparisons_use_same_currency_and_expected_percentage_points():
    data = load_dataset()
    by_id = {c["id"]: c for c in data["cases"]}
    cet1 = by_id["D14-067"]["expected"]
    assert cet1["numeric_values"] == [17.1, 17.0, 0.1]
    assert [s["issuer"] for s in cet1["sources"]] == ["SEB", "Handelsbanken"]
    nii = by_id["D14-068"]["expected"]
    assert nii["numeric_values"] == [10881, 10051, 830]
    assert {s["issuer"] for s in nii["sources"]} == {"SEB", "Handelsbanken"}
