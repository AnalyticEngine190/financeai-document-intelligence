from unittest.mock import patch
from app.document_tables import generic_cell


def test_sek_quarterly_cell_from_explicit_row_and_unit():
    pages = [{"document": "seb.pdf", "page": 6, "text":
              "SEB interim report\nIncome statement\nSEKm Q3 2026 Q2 2026 Q1 2026\n"
              "Net interest income 12,345 12,000 11,000\nOperating profit 8,500 8,200 8,100"}]
    with patch("app.document_tables._get_pages", return_value=pages):
        c = generic_cell("net_interest_income", "Q326", "seb")
    assert c and str(c.value) == "12345" and c.unit == "SEKm" and c.page == 6


def test_no_unit_is_not_implicitly_eur():
    pages = [{"document": "other.pdf", "page": 1, "text":
              "Income statement\nQ3 2026 Q2 2026\nNet interest income 100 200"}]
    with patch("app.document_tables._get_pages", return_value=pages):
        assert generic_cell("net_interest_income", "Q326", "doc") is None


def test_ambiguous_columns_or_rows_fail_closed():
    pages = [{"document": "seb.pdf", "page": 1, "text":
              "Income statement SEKm\nQ3 2026 Q2 2026\nNet interest income 100 200 300"}]
    with patch("app.document_tables._get_pages", return_value=pages):
        assert generic_cell("net_interest_income", "Q326", "doc") is None
