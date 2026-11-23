from __future__ import annotations

import json
from pathlib import Path

from app.company_overview import PageEvidence, build_company_overview


def test_seb_actual_q3_pdf_layout_does_not_select_table_of_contents_as_segment_table():
    fixture = Path(__file__).parent / "fixtures" / "seb_q3_2026_overview_pages.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))
    pages = [PageEvidence(page=row["page"], text=row["text"]) for row in rows]

    result = build_company_overview(
        "SEB", "SEB — Q3 2026 (quarterly_report)", "seb-q3", 2026, 3, pages
    )

    assert result["supported"] is True
    assert result["business_segments"] == [
        "Corporate & Investment Banking", "Business & Retail Banking",
        "Wealth & Asset Management", "Baltic",
    ]
    assert result["group_functions_reported"] is True
    assert "Group Functions are reported separately" in result["answer"]
    assert "Eliminations are shown as a reporting column" in result["answer"]
    assert set(result["evidence_pages"]) == {14, 15, 17, 19, 21}
    # Page 4 is the table of contents and must not be used as segment evidence.
    assert 4 not in result["evidence_pages"]
    assert all(source["document_id"] == "seb-q3" for source in result["sources"])


def test_seb_detail_pages_are_enough_when_segment_table_page_is_not_indexed():
    fixture = Path(__file__).parent / "fixtures" / "seb_q3_2026_overview_pages.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))
    pages = [PageEvidence(page=row["page"], text=row["text"])
             for row in rows if row["page"] in {15, 17, 19, 21}]

    result = build_company_overview(
        "SEB", "SEB — Q3 2026 (quarterly_report)", "seb-q3", 2026, 3, pages
    )

    assert result["supported"] is True
    assert result["business_segments"] == [
        "Corporate & Investment Banking", "Business & Retail Banking",
        "Wealth & Asset Management", "Baltic",
    ]
    assert set(result["evidence_pages"]) == {15, 17, 19, 21}
    assert "Group Functions are reported separately" not in result["answer"]
