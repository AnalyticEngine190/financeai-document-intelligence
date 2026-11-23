from __future__ import annotations

import json
from pathlib import Path

from app.company_overview import (
    PageEvidence, build_company_overview, load_indexed_pages,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nordea_q3_overview_pages.json"


def pages():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [PageEvidence(page=x["page"], text=x["text"]) for x in rows]


def test_synthetic_factbook_produces_useful_overview_from_business_area_pages():
    result = build_company_overview(
        "Aster Financial Group", "Aster Financial Group - Q3 2026 (review)", "aster-q3", 2026, 3, pages()
    )
    assert result["supported"] is True
    answer = result["answer"]
    assert result["business_segments"] == [
        "Retail Banking", "Wealth & Asset Management", "Business Banking",
        "Corporate & Institutional",
    ]
    for label in (
        "Retail Banking", "Wealth & Asset Management", "Business Banking",
        "Corporate & Institutional", "Group Functions",
        "Denmark", "Finland", "Norway", "Sweden",
    ):
        assert label in answer
    assert "192.7 EURbn" in answer
    assert "102.4 EURbn" in answer
    assert "204.4 EURbn" in answer
    assert "108.5 EURbn" in answer
    assert "61.4 EURbn" in answer
    assert "68.1 EURbn" in answer
    assert "57.2 EURbn" in answer


def test_overview_citations_are_physical_pages_from_one_selected_document():
    result = build_company_overview(
        "Aster Financial Group", "Aster Financial Group - Q3 2026 (review)", "aster-q3", 2026, 3, pages()
    )
    by_page = {s["page"]: s for s in result["sources"]}
    assert {2, 13, 14, 16, 20, 21, 23, 24}.issubset(by_page)
    assert 59 not in by_page
    assert all(s["document_id"] == "aster-q3" for s in result["sources"])
    assert result["selected_document_id"] == "aster-q3"


def test_promotional_footer_alone_is_not_accepted_as_overview():
    page = [PageEvidence(page=59, text=pages()[-1].text)]
    result = build_company_overview("Aster Financial Group", "Aster Financial Group Q3 2026", "aster-q3", 2026, 3, page)
    assert result["supported"] is False
    assert result["sources"] == []
    assert "do not contain enough" in result["answer"]


def test_no_segment_evidence_fails_closed():
    page = [PageEvidence(page=5, text="Quarterly results and key financial figures.")]
    result = build_company_overview("Example Institution", "Example Q3 2026", "x", 2026, 3, page)
    assert result["supported"] is False
    assert result["sources"] == []


def test_loader_filters_to_selected_document_and_joins_chunks_by_page():
    class FakeCollection:
        def __init__(self):
            self.where = None
        def get(self, *, where, include):
            self.where = where
            assert include == ["documents", "metadatas"]
            return {
                "documents": ["second chunk", "foreign chunk", "first chunk"],
                "metadatas": [
                    {"document_id": "chosen", "page_number": 13, "chunk_index": 1},
                    {"document_id": "other", "page_number": 2, "chunk_index": 0},
                    {"document_id": "chosen", "page_number": 13, "chunk_index": 0},
                ],
            }
    collection = FakeCollection()
    result = load_indexed_pages("chosen", collection=collection)
    assert collection.where == {"document_id": "chosen"}
    assert len(result) == 1
    assert result[0].page == 13
    assert result[0].text == "first chunk\nsecond chunk"


def test_financial_highlights_are_extracted_only_from_rows_in_same_page():
    text = """BUSINESS AREAS
Retail Banking
Volumes
EURbn
Q326 Q226
Total lending
192.7 190.8
Total deposits
102.4 101.8
"""
    result = build_company_overview(
        "Aster Financial Group", "Aster Financial Group Q3 2026", "aster-q3", 2026, 3,
        [PageEvidence(page=13, text=text)],
    )
    assert result["supported"]
    assert "192.7 EURbn" in result["answer"]
    assert "102.4 EURbn" in result["answer"]


def test_wrong_currency_context_prevents_volume_highlight():
    text = """BUSINESS AREAS
Retail Banking
Volumes
Q326 Q226
Total lending
192.7 190.8
"""
    result = build_company_overview(
        "Aster Financial Group", "Aster Financial Group Q3 2026", "aster-q3", 2026, 3,
        [PageEvidence(page=13, text=text)],
    )
    assert result["supported"]
    assert "192.7" not in result["answer"]


def test_financial_highlight_requires_selected_quarter_header():
    text = """BUSINESS AREAS
Retail Banking
Volumes
EURbn
Q226 Q126
Total lending 190.8 189.6
Total deposits 101.8 100.9
"""
    result = build_company_overview(
        "Aster Financial Group", "Aster Financial Group Q3 2026", "aster-q3", 2026, 3,
        [PageEvidence(page=13, text=text)],
    )
    assert result["supported"]
    assert "190.8" not in result["answer"]
    assert "101.8 EURbn" not in result["answer"]


def test_indexed_overview_reads_only_selected_chroma_document(monkeypatch):
    from types import SimpleNamespace
    from app.company_overview import build_indexed_company_overview

    fixture_pages = pages()

    class FakeCollection:
        def __init__(self):
            self.where = None
        def get(self, *, where, include):
            self.where = where
            docs, metas = [], []
            for page in fixture_pages:
                docs.append(page.text)
                metas.append({"document_id": "aster-q3", "page_number": page.page, "chunk_index": 0})
            docs.append("BUSINESS AREAS\nUnrelated Retail Division")
            metas.append({"document_id": "other-report", "page_number": 13, "chunk_index": 0})
            return {"documents": docs, "metadatas": metas}

    collection = FakeCollection()
    monkeypatch.setitem(__import__("sys").modules, "app.vector_store",
                        SimpleNamespace(get_collection=lambda: collection))
    fake_doc = SimpleNamespace(
        issuer="Aster Financial Group", display_name="Aster Financial Group - Q3 2026 (review)",
        document_id="aster-q3", report_year=2026, report_quarter=3,
    )
    result = build_indexed_company_overview(fake_doc)
    assert collection.where == {"document_id": "aster-q3"}
    assert result["supported"]
    assert result["business_segments"] == [
        "Retail Banking", "Wealth & Asset Management", "Business Banking",
        "Corporate & Institutional",
    ]
    assert "Unrelated Retail Division" not in result["answer"]
