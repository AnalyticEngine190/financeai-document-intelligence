"""Day 13.11 regressions for SEB/Handelsbanken report layouts and generic profit."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.company_overview import PageEvidence, build_company_overview
from app.question_parser import parse_question

FIXTURES = Path(__file__).parent / "fixtures"


def _pages(filename: str) -> list[PageEvidence]:
    data = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    return [PageEvidence(page=item["page"], text=item["text"]) for item in data]


def test_seb_q3_2026_overview_reads_segment_table_and_own_division_pages():
    result = build_company_overview(
        "SEB", "SEB — Q3 2026 (quarterly report)", "seb-q3", 2026, 3,
        _pages("seb_q3_2026_overview_pages.json"),
    )
    assert result["supported"] is True
    assert result["business_segments"] == [
        "Corporate & Investment Banking", "Business & Retail Banking",
        "Wealth & Asset Management", "Baltic",
    ]
    answer = result["answer"]
    assert "Group Functions" in answer
    assert "serves large corporate, mid corporate and institutional clients" in answer
    assert "private individuals and small and medium-sized corporate customers" in answer
    assert "Private Wealth Management & Family Office" in answer
    assert "in Estonia, Latvia and Lithuania" in answer
    assert set(result["evidence_pages"]) == {14, 15, 17, 19, 21}
    assert all(source["document_id"] == "seb-q3" for source in result["sources"])


def test_handelsbanken_q3_2026_overview_reads_declared_segment_table_and_pages():
    result = build_company_overview(
        "Handelsbanken", "Handelsbanken — Q3 2026 (interim report)", "hb-q3", 2026, 3,
        _pages("handelsbanken_q3_2026_overview_pages.json"),
    )
    assert result["supported"] is True
    assert result["business_segments"] == [
        "Handelsbanken Sweden", "Handelsbanken UK", "Handelsbanken Norway",
        "Handelsbanken the Netherlands", "Handelsbanken Markets",
    ]
    assert "five business segments" in result["answer"]
    assert "Other and adjustments/eliminations separately" in result["answer"]
    assert "Markets reporting note" in result["answer"]
    assert set(result["evidence_pages"]) == {10, 11, 13, 15, 17, 19}
    assert all(source["document_id"] == "hb-q3" for source in result["sources"])


def test_multibank_overview_does_not_infer_a_segment_from_a_stray_mention():
    pages = [PageEvidence(page=19, text=(
        "Wealth & Asset Management\n\nThe products are distributed by the Baltic division.\n\n"
        "Income statement\n"))]
    result = build_company_overview("SEB", "SEB Q3 2026", "seb-q3", 2026, 3, pages)
    assert result["supported"] is False
    assert result["sources"] == []


def test_unqualified_profit_defaults_to_net_profit_but_explicit_measures_stay_distinct():
    generic = parse_question("What is SEB's profit")
    operating = parse_question("What was SEB's operating profit?")
    net = parse_question("What was SEB's net profit in Q3 2026?")
    pre_tax = parse_question("What was SEB's profit before tax?")
    assert generic.metric == "net_profit"
    assert generic.periods == ()
    assert operating.metric == "operating_profit"
    assert net.metric == "net_profit" and net.periods == ("Q326",)
    assert pre_tax.metric is None  # It is not silently re-labelled as net profit.


def test_seb_undated_profit_answer_uses_latest_quarter_of_selected_report():
    # The project runtime includes these dependencies. Skip this integration
    # check in lightweight test containers which only have the parser installed.
    pytest.importorskip("chromadb")
    pytest.importorskip("sentence_transformers")
    from app.document_answering import answer_with_selection
    from app.document_registry import DocumentRecord
    from app.document_resolver import DocumentSelection

    row = json.loads((FIXTURES / "seb_q3_2026_income_statement_page.json").read_text(encoding="utf-8"))
    doc = DocumentRecord(
        "seb-q3", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK",
    )
    page = [{"document": row["document"], "page": row["page"], "text": row["text"]}]
    with patch("app.document_tables._get_pages", return_value=page):
        answer = answer_with_selection("What is SEB's profit", DocumentSelection(), [doc])
    assert answer["supported"] is True
    assert "net profit" in answer["answer"].casefold()
    assert "SEK 7,498 million" in answer["answer"]
    assert "operating profit" in answer["answer"].casefold()
    assert "SEK 9,412 million" in answer["answer"]
    assert answer["sources"][0]["page"] == 5
    assert answer["sources"][0]["document_id"] == "seb-q3"
