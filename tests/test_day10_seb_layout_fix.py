"""Realistic layouts transcribed from SEB's official Q3 2026 Fact Book p.5.

Tests use fixture text; they are NOT a live test against the user's PDF.
"""
from unittest.mock import patch
from app.document_tables import generic_cell, _quarter_columns

NINE_Q = 'Q3 Q4 Q1 Q2 Q3 Q4 Q1 Q2 Q3\n2024 2024 2025 2025 2025 2025 2026 2026 2026'
SEB_P5 = f'''Income statement - nine quarters
SEK m
{NINE_Q}
Net interest income 11,266 11,112 10,844 10,622 10,540 10,348 10,242 10,687 10,881
Net fee and commission income 6,034 6,508 6,705 6,685 6,287 6,814 6,410 7,194 6,702
Operating profit before items affecting comparability 11,818 10,069 9,954 10,400 9,719 9,241 9,432 10,774 9,412
Operating profit 11,818 10,069 9,954 10,400 9,719 8,826 9,432 10,774 9,412
Net profit 9,454 7,493 7,824 8,253 7,677 7,308 7,497 8,664 7,498
Key figures - nine quarters
{NINE_Q}
Return on equity, % 17,0 13.2 13.4 15.0 14.0 12.9 13.1 15.7 13.0
Common Equity Tier 1 capital ratio, % 19.4 17.6 17.5 17.7 18.2 17.7 17.5 17.2 17.1
Assets under management, SEK bn 2,709 2,664 2,669 2,744 2,820 2,904 2,863 3,069 3,157'''


def _cell(metric, period='Q326', pdf_text=SEB_P5):
    pages = [{"document": "seb_factbook.pdf", "page": 5, "text": pdf_text}]
    with patch('app.document_tables._get_pages', return_value=pages) as get_pages:
        value = generic_cell(metric, period, 'seb_document_id')
        get_pages.assert_called_once_with('seb_document_id')
        return value


def test_quarter_header_separate_rows():
    assert _quarter_columns(NINE_Q) == ['Q324','Q424','Q125','Q225','Q325','Q425','Q126','Q226','Q326']


def test_quarter_header_alternating():
    assert _quarter_columns('SEK m Q3 2025 Q2 2026 Q3 2026') == ['Q325','Q226','Q326']


def test_exact_operating_profit_does_not_pick_adjusted_row():
    cell = _cell('operating_profit')
    assert cell and str(cell.value) == '9412' and cell.unit == 'SEKm' and cell.page == 5


def test_other_quarter_different_value():
    c = _cell('operating_profit', 'Q425')
    assert c and str(c.value) == '8826' and c.period == 'Q425'


def test_net_interest_income():
    c = _cell('net_interest_income')
    assert c and str(c.value) == '10881' and c.unit == 'SEKm'


def test_net_fee_income():
    c = _cell('net_fee_income')
    assert c and str(c.value) == '6702'


def test_net_profit_seb_label():
    c = _cell('net_profit')
    assert c and str(c.value) == '7498'


def test_seb_roe_decimal_commas():
    c = _cell('roe', 'Q324')
    assert c and c.unit == 'percent' and str(c.value) == '17.0'


def test_seb_cet1_ratio():
    c = _cell('cet1_ratio')
    assert c and str(c.value) == '17.1' and c.unit == 'percent'


def test_aum_scale_from_row_not_income_header():
    c = _cell('aum')
    assert c and str(c.value) == '3157' and c.unit == 'SEKbn'


def test_no_units_no_monetary_guessing():
    text = 'Income statement - nine quarters\n' + NINE_Q + '\nOperating profit 1 2 3 4 5 6 7 8 9'
    assert _cell('operating_profit', pdf_text=text) is None


def test_extra_undocumented_columns_are_rejected():
    text = 'SEK m Q3 2026 Q2 2026\nOperating profit 9,412 10,774 5%'
    assert _cell('operating_profit', pdf_text=text) is None


def test_ambiguous_multiple_tables_abstain():
    text = SEB_P5 + '\nIncome statement - nine quarters\nSEK m\nQ3 2026 Q2 2026\nOperating profit 9,413 10,774'
    assert _cell('operating_profit', pdf_text=text) is None


def test_unknown_period_abstains():
    assert _cell('operating_profit', 'Q327') is None


def test_seb_full_question_manual_and_auto_route():
    from app.document_answering import answer_with_selection
    from app.document_resolver import DocumentSelection
    from app.document_registry import DocumentRecord
    doc = DocumentRecord('seb_id', 'SEB Q3 2026.pdf', 'SEB', 2026, 3, 'factbook', 'SEK')
    page = [{"document": doc.filename, "page": 5, "text": SEB_P5}]
    with patch('app.document_tables._get_pages', return_value=page):
        for sel in [DocumentSelection('auto'), DocumentSelection('manual', ('seb_id',))]:
            ans = answer_with_selection("What was SEB's operating profit in Q3 2026?", sel, [doc])
            assert ans['supported'] is True
            assert ans['answer'] == 'SEK 9,412 million'
            assert ans['sources'][0]['page'] == 5
            assert ans['sources'][0]['document_id'] == 'seb_id'


def test_pymupdf_sort_true_quarters_before_sek_m_year_row():
    # A second ordering produced by fitz.get_text('text', sort=True)
    # on a simulated nine-quarter financial PDF.
    text = '''Income statement - nine quarters

Q3 Q4 Q1 Q2 Q3 Q4 Q1 Q2 Q3
SEK m 2024 2024 2025 2025 2025 2025 2026 2026 2026

Net interest income 11,266 11,112 10,844 10,622 10,540 10,348 10,242 10,687 10,881
Operating profit 11,818 10,069 9,954 10,400 9,719 8,826 9,432 10,774 9,412'''
    c = _cell('operating_profit', pdf_text=text)
    assert c is not None and c.value == 9412 and c.unit == 'SEKm'
