"""Offline tests for our evaluation harness (do not call Ollama)."""

import json

import pytest

from scripts.evaluate_answers import (
    DEFAULT_DATASET,
    grade,
    numeric_values,
    unit_status,
    validate_dataset,
    value_present,
)


@pytest.mark.skipif(
    not DEFAULT_DATASET.is_file(),
    reason="Private Nordea gold benchmark is not included in the public repository",
)
def test_gold_set_has_50_cases():
    dataset = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    validate_dataset(dataset)
    kinds = [case["expected"]["outcome"] for case in dataset["questions"]]
    assert len(kinds) == 50
    assert kinds.count("answer") == 35
    assert kinds.count("abstain") == 10
    assert kinds.count("clarify") == 5


def test_number_boundaries():
    assert value_present("EUR 2,968 million", "2,968", "EURm")
    assert value_present("2968", "2,968", "EURm")
    assert value_present("EUR 2.968 billion", "2,968", "EURm")
    assert not value_present("EUR 2,969 million", "2,968", "EURm")
    assert not value_present("EUR 2,968 billion", "2,968", "EURm")
    assert not value_present("EUR 509.4 million", "509.4", "EURbn")
    assert not value_present("total 12,968", "2,968", "EURm")
    assert value_present("15.8%", "15.8", "percent")
    assert not value_present("115.8%", "15.8", "percent")


def test_correct_factual_answer_requires_citation():
    case = {"expected": {"outcome": "answer", "value": "2,968", "unit": "EURm", "pages": [7]}}
    correct = {"answer": "€2,968 million", "supported": True,
               "sources": [{"page": 7, "document": "annual_report.pdf"}]}
    assert grade(case, correct)["passed"] is True
    wrong_page = {**correct, "sources": [{"page": 5, "document": "annual_report.pdf"}]}
    assert grade(case, wrong_page)["passed"] is False
    wrong_number = {**correct, "answer": "€3,032 million"}
    assert grade(case, wrong_number)["passed"] is False


def test_missing_unit_flagged_but_number_still_correct():
    case = {"expected": {"outcome": "answer", "value": "2,968", "unit": "EURm", "pages": [7]}}
    result = grade(case, {"answer": "2,968", "supported": True, "sources": [{"page": 7, "document": "annual_report.pdf"}]})
    assert result["passed"] is True
    assert result["unit_status"] == "missing"
    assert "unit_missing" in result["issue"]


def test_abstain_not_same_as_clarify():
    unsupported = {"expected": {"outcome": "abstain"}}
    clarification = {"answer": "Which metric?", "supported": False,
                     "needs_clarification": True, "sources": []}
    assert grade(unsupported, clarification)["passed"] is False
    ambiguous = {"expected": {"outcome": "clarify"}}
    assert grade(ambiguous, clarification)["passed"] is True


def test_unnecessary_citation_fails():
    case = {"expected": {"outcome": "answer", "value": "2,968", "unit": "EURm", "pages": [7]}}
    result = grade(case, {"answer": "€2,968 million", "supported": True,
                          "sources": [{"page": 7, "document": "annual_report.pdf"},
                                      {"page": 5, "document": "annual_report.pdf"}]})
    assert result["passed"] is False


def test_unsupported_number_not_counted_as_abstention():
    unsupported = {"expected": {"outcome": "abstain"}}
    incorrect = {"answer": "2035 revenue was EUR 999 million", "supported": False, "sources": []}
    assert grade(unsupported, incorrect)["passed"] is False
    proper = {"answer": "I cannot verify the answer from the retrieved document passages.",
              "supported": False, "sources": []}
    assert grade(unsupported, proper)["passed"] is True
