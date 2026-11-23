"""Unit tests for the independent evaluator; no calls to Ollama/Chroma/AI pipeline."""

import copy
from pathlib import Path

import pytest

from scripts.evaluate_day10_4 import validate_dataset, first_number, grade

DATASET = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "day10_4_40.json"
requires_private_dataset = pytest.mark.skipif(
    not DATASET.is_file(),
    reason="Private Day 10.4 benchmark is not included in the public repository",
)


@requires_private_dataset
def test_40_case_dataset_valid():
    ds = validate_dataset()
    assert len(ds["cases"]) == 40
    assert len([c for c in ds["cases"] if c["expected_outcome"] == "explanation"]) == 2


def test_number_extraction_and_sign():
    assert first_number("EUR -1,335 million") == -1335
    assert first_number("SEK 4,439,488 million") == 4439488
    assert first_number("0 percentage points") == 0
    assert first_number("") is None


@requires_private_dataset
def test_source_isolation_and_value():
    c = next(c for c in validate_dataset()["cases"] if c["id"] == "NEW_BANK_01")
    doc = c["expected_sources"][0]["document_id"]
    valid = {
        "supported": True,
        "needs_clarification": False,
        "answer": "SEK 18,406 million",
        "sources": [{"document_id": doc, "page": 5}],
    }
    assert grade(c, valid) == ("PASS", "")
    other = copy.deepcopy(valid)
    other["sources"][0]["document_id"] = "wrong"
    assert grade(c, other) == ("FAIL", "wrong_source_document")
    wrong = copy.deepcopy(valid)
    wrong["answer"] = "SEK 20,055 million"
    assert grade(c, wrong) == ("FAIL", "wrong_primary_number")


@requires_private_dataset
def test_review_never_auto_passes():
    c = next(c for c in validate_dataset()["cases"] if c["id"] == "NEW_BANK_11")
    doc = c["expected_sources"][0]["document_id"]
    resp = {
        "supported": True,
        "answer": "Higher lending volumes and higher interest rates contributed to net interest income.",
        "sources": [{"document_id": doc, "page": 7}],
    }
    assert grade(c, resp)[0] == "REVIEW"


@requires_private_dataset
def test_clarification_not_confused_with_abstention():
    c = next(c for c in validate_dataset()["cases"] if c["id"] == "ISOLATION_01")
    assert grade(c, {"supported": False, "needs_clarification": True, "sources": []})[0] == "PASS"
    assert grade(c, {"supported": False, "needs_clarification": False, "sources": []})[0] == "FAIL"


@requires_private_dataset
def test_mixed_currency_requires_specific_reason():
    c = next(c for c in validate_dataset()["cases"] if c["id"] == "CROSS_DOCUMENT_06")
    assert grade(
        c,
        {
            "supported": False,
            "needs_clarification": False,
            "sources": [],
            "answer": "Cannot subtract EUR and SEK without an exchange rate.",
        },
    )[0] == "PASS"
    assert grade(
        c,
        {
            "supported": False,
            "needs_clarification": False,
            "sources": [],
            "answer": "Cannot verify the answer.",
        },
    )[0] == "FAIL"


@requires_private_dataset
def test_exact_multi_document_citations_required():
    c = next(c for c in validate_dataset()["cases"] if c["id"] == "CROSS_DOCUMENT_01")
    sources = c["expected_sources"]
    resp = {
        "supported": True,
        "needs_clarification": False,
        "answer": "EUR 64 million decline.",
        "sources": sources,
    }
    assert grade(c, resp)[0] == "PASS"
    resp["sources"] = [sources[0]]
    assert grade(c, resp) == ("FAIL", "missing_required_report_citation")
