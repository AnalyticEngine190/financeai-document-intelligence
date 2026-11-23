from app.document_registry import DocumentRecord, _infer, save_document, _read_manifest
from app.document_resolver import DocumentSelection, resolve_documents
import pytest

NQ3 = DocumentRecord("nq3", "nq3.pdf", "Nordea", 2026, 3, "factbook", "EUR")
NQ2 = DocumentRecord("nq2", "nq2.pdf", "Nordea", 2026, 2, "factbook", "EUR")
SQ3 = DocumentRecord("sq3", "sq3.pdf", "SEB", 2026, 3, "interim_report", "SEK")
DOCS = [NQ3, NQ2, SQ3]


def test_legacy_nordea_pdf_metadata_inferred():
    assert _infer("Nordea Third-Quarter Factbook 2026 Q3\n", "annual_report.pdf") == ("Nordea", 2026, 3, "factbook")


def test_manifest_atomic_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_AI_DOCUMENT_REGISTRY", str(tmp_path / "data" / "registry.json"))
    save_document(NQ3)
    assert _read_manifest()["nq3"] == NQ3


def test_automatic_picks_explicit_bank_and_quarter():
    r = resolve_documents("What was Nordea's operating profit in Q3 2026?", DocumentSelection(), DOCS)
    assert [x.document_id for x in r.documents] == ["nq3"]
    r = resolve_documents("What was SEB's operating profit in Q3 2026?", DocumentSelection(), DOCS)
    assert [x.document_id for x in r.documents] == ["sq3"]


def test_automatic_asks_without_bank_but_defaults_named_bank_to_latest_edition():
    r = resolve_documents("What was the operating profit?", DocumentSelection(), DOCS)
    assert not r.documents and r.clarification
    r = resolve_documents("What was Nordea's operating profit?", DocumentSelection(), DOCS)
    assert [doc.document_id for doc in r.documents] == ["nq3"]
    assert r.clarification is None
    assert "latest indexed edition" in r.reason.casefold()


def test_manual_selection_overrides_period_not_issuer():
    r = resolve_documents("What was Nordea's profit in Q3 2026?", DocumentSelection("manual", ("nq2",)), DOCS)
    assert [d.document_id for d in r.documents] == ["nq2"]
    r = resolve_documents("What was SEB's profit?", DocumentSelection("manual", ("nq2",)), DOCS)
    assert not r.documents and r.clarification


def test_manual_conflict_report_edition():
    r = resolve_documents("According to Nordea's Q3 2026 factbook, net interest income?",
                          DocumentSelection("manual", ("nq2",)), DOCS)
    assert not r.documents and r.clarification


def test_compare_two_explicit_pdfs():
    r = resolve_documents("Compare Nordea Q2 2026 and Q3 2026 net interest income",
                          DocumentSelection("compare", ("nq2", "nq3")), DOCS)
    assert len(r.documents) == 2

def test_auto_compare_two_quarterly_reports_for_same_bank():
    r = resolve_documents(
        "Compare What was Nordea's operating profit in Q2 2026 with Q3 2026.",
        DocumentSelection(),
        DOCS,
    )

    assert r.clarification is None
    assert [doc.document_id for doc in r.documents] == ["nq2", "nq3"]

def test_cannot_compare_without_explicit_comparison():
    r = resolve_documents("What was Nordea's operating profit in Q3 2026?",
                          DocumentSelection("compare", ("nq2", "nq3")), DOCS)
    assert r.clarification


def test_invalid_document_id_and_duplicate_rejected():
    with pytest.raises(ValueError):
        resolve_documents("Hello", DocumentSelection("manual", ("bad",)), DOCS)
    with pytest.raises(ValueError):
        resolve_documents("Compare", DocumentSelection("compare", ("nq3", "nq3")), DOCS)


def test_automatic_cannot_silently_mix_bank_and_period():
    r = resolve_documents("What was Nordea's profit in Q1 2025?", DocumentSelection(), DOCS)
    assert not r.documents and r.clarification
    r = resolve_documents("Compare Nordea and SEB Q3 2026 profit", DocumentSelection(), DOCS)
    assert not r.documents and r.clarification


def test_manual_cannot_partially_answer_multi_report_comparison():
    r = resolve_documents("Compare Nordea and SEB Q3 2026 net interest income",
                          DocumentSelection("manual", ("nq3",)), DOCS)
    assert not r.documents and r.clarification
    r = resolve_documents("Compare Nordea Q2 2026 and Q3 2026 net interest income",
                          DocumentSelection("manual", ("nq3",)), DOCS)
    assert not r.documents and r.clarification
