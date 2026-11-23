"""Synthetic but layout-faithful tests from SEB Q3 2026 quarterly report pages 5-6.

Financial values are fixture assertions, NEVER part of application parsing.
"""
from unittest.mock import patch

from app.document_tables import generic_cell

P5 = '''SEB Group
Income statement on a quarterly basis, condensed
                                                  Q3        Q2        Q1        Q4        Q3
SEK m                                                2026       2026       2026       2025       2025
Net interest income¹⁾                                    10 881      10 687      10 242      10 348      10 540
Net fee and commission income                             6 702       7 194       6 410       6 814       6 287
Operating profit before
 items affecting comparability                              9 412      10 774       9 432       9 241       9 719
 Operating profit                                        9 412      10 774       9 432       8 826       9 719
NET PROFIT                                           7 498       8 664       7 497       7 308       7 677'''
P6 = '''Key figures
                                             Q3       Q2        Q3         Jan-Sep             Full year
                                                 2026      2026       2025       2026     2025       2025
 Return on equity, %                                        13.0       15.7         14.0         13.9      14.1         13.8
 Return on equity excluding items affecting
 comparability, %                                                 1)       13.0       15.7         14.0         13.9      14.1         14.0
 Liquidity Coverage Ratio (LCR), %                           4)      136       125        136        136      136        150
Common Equity Tier 1 capital ratio, %                        17.1       17.2         18.2         17.1      18.2         17.7
Number of full-time equivalents (FTEs)                     6)    18 413    18 491      18 804      18 431    19 008      18 929
 Assets under management, SEK bn                     3 157     3 069       2 820       3 157     2 820      2 904'''


def _read(metric, period='Q326', p5=P5, p6=P6):
    pages = [{'document':'SEB Q3 2026.pdf','page':5,'text':p5},
             {'document':'SEB Q3 2026.pdf','page':6,'text':p6}]
    with patch('app.document_tables._get_pages',return_value=pages) as source:
        result=generic_cell(metric,period,'seb-abc')
        source.assert_called_once_with('seb-abc')
        return result


def test_real_layout_values_and_citations():
    expected = {
        'operating_profit': (9412,'SEKm',5),
        'net_interest_income': (10881,'SEKm',5),
        'net_fee_income': (6702,'SEKm',5),
        'net_profit': (7498,'SEKm',5),
        'cet1_ratio': ('17.1','percent',6),
        'roe': ('13.0','percent',6),
        'aum': (3157,'SEKbn',6),
        'lcr': (136,'percent',6),
        'fte': (18413,'count',6),
    }
    for metric,(value,unit,page) in expected.items():
        c=_read(metric)
        assert c is not None, metric
        assert str(c.value) == str(value),metric
        assert c.unit == unit and c.page == page and c.document == 'SEB Q3 2026.pdf'


def test_alternate_quarter_and_adjusted_profit_not_confused():
    assert str(_read('operating_profit','Q425').value) == '8826'
    assert str(_read('operating_profit','Q226').value) == '10774'


def test_row_without_explicit_unit_does_not_gain_currency_from_issuer():
    p5 = P5.replace('SEK m', ' ',1)
    assert _read('operating_profit',p5=p5) is None


def test_mixed_currency_headers_abstain():
    p5 = P5.replace('SEK m', 'EUR m and SEK m',1)
    assert _read('operating_profit',p5=p5) is None


def test_conflicting_valid_tables_abstain():
    p5 = P5 + '''\n\nIncome statement on a quarterly basis, condensed
Q3 2026 Q2 2026
SEK m
Operating profit 9,413 10,774'''
    # The second table's SEK m is after the quarter labels and before
    # the accounting row; the parser may abstain by unaligned header.
    # A fully aligned contradictory table must definitely abstain.
    p5 = P5 + '''\nIncome statement on a quarterly basis, condensed
SEK m Q3 2026 Q2 2026
Operating profit 9,413 10,774'''
    assert _read('operating_profit',p5=p5) is None


def test_no_claim_for_nonexistent_period():
    assert _read('net_profit','Q327') is None
