"""Issuer-neutral layout/interpretation/regression tests; no live Chroma required."""
from decimal import Decimal
from unittest.mock import patch
import pytest
from app.layout_table_reader import aligned_group_cell
from app.financial_plan import plan_question
from app.document_resolver import DocumentSelection,resolve_documents
from app.document_registry import DocumentRecord
from app.narrative_answering import narrative_answer

D=DocumentRecord('hb','handelsbanken.pdf','Handelsbanken',2026,3,'interim_report','SEK')
Q=DocumentRecord('nq3','nordea.pdf','Nordea',2026,3,'factbook',None)
S=DocumentRecord('seb','seb.pdf','SEB',2026,3,'interim_report','SEK')
Q2=DocumentRecord('nq2','nordea-q2.pdf','Nordea',2026,2,'factbook','EUR')

INCOME='''Quarterly Performance – Group
                  Q3          Q2          Q1          Q4          Q3
SEK m             2026        2026        2026        2025        2025
Net interest income                 10,051     10,008       10,016      10,139       10,578
Net gains/losses on financial transactions              515       116       547       676      573
Other income                       23       23      1,149      256       52
Staff costs                        -3,818       -3,743        -3,679       -3,653       -3,746
Depreciation, amortisation and impairment of tangible and intangible
                                    -508      -464      -482     -481     -515
assets
Total expenses                       -5,910       -5,963        -5,838       -5,956       -5,855
Earnings per share, SEK               2.87        2.60        3.21        3.01        3.00
'''
BALANCE='''Balance Sheet – Group
                    30 Sep     30 Jun     31 Mar     31 Dec     30 Sep
SEK m               2026       2026       2026       2025       2025
Assets
Cash and balances with central banks                    517,108   578,352   535,676   408,154   598,245
Loans to the public                    Note 9           2,393,051   2,346,528   2,333,447   2,263,765   2,288,016
Derivatives                            Note 11,12       33,817      34,031     26,264       21,752     22,253
Total assets                          Note 18           3,740,642  3,872,440  3,725,382  3,387,566  3,802,267
Liabilities and equity
Derivatives                            Note 11,12       23,129      16,522      21,997      26,211      23,496
Total liabilities                      Note 18           3,546,421  3,688,045  3,553,154  3,188,211  3,607,577
Total liabilities and equity                             3,740,642  3,872,440  3,725,382  3,387,566  3,802,267
'''
KEY='''Key metrics – Group
                 Q3     Q2     Q3    Jan-Sep   Jan-Sep   Full year
                 2026   2026   2025  2026      2025      2025
C/I ratio             42.3%    44.3%     41.1%    42.0%    42.3%    42.1%
Earnings per share, SEK              2.87     2.60      3.00     8.68      8.97      11.98
Common equity tier 1 ratio, CRR      17.0%    17.2%     18.2%    17.0%     18.2%    17.6%
'''

def page(text,n=1,document='handelsbanken.pdf'):
    return {'document':document,'page':n,'text':text}

@pytest.mark.parametrize('metric,period,value,unit',[
    ('net_gains_financial','Q326','515','SEKm'),
    ('other_income_group','Q226','23','SEKm'),
    ('staff_costs','Q326','-3818','SEKm'),
    ('depreciation_impairment','Q326','-508','SEKm'),
    ('total_expenses','Q226','-5963','SEKm'),
    ('cash_central_banks','Q326','517108','SEKm'),
    ('loans_public','Q226','2346528','SEKm'),
    ('total_assets','Q326','3740642','SEKm'),
    ('total_liabilities','Q326','3546421','SEKm'),
    ('derivative_assets','Q326','33817','SEKm'),
    ('earnings_per_share','Q326','2.87','SEK'),
    ('cost_income_ratio','Q326','42.3','percent'),
    ('cet1_ratio','Q326','17.0','percent'),
])
def test_aligned_group_table(metric,period,value,unit):
    source=[page(INCOME,24),page(BALANCE,25),page(KEY,21)]
    cell=aligned_group_cell(metric,period,source)
    assert cell is not None,(metric,period)
    assert cell.value==Decimal(value) and cell.unit==unit


def test_conflicting_duplicate_group_values_fail_closed():
    changed=INCOME.replace('10,051','10,999')
    assert aligned_group_cell('net_interest_income','Q326',
                   [page(INCOME,24),page(changed,26)]) is None


def test_parent_statement_is_not_a_group_balance_sheet():
    assert aligned_group_cell('total_assets','Q326',
           [page(BALANCE.replace('Balance Sheet – Group','Balance Sheet – Parent company'),48)]) is None


def test_missing_unit_cannot_be_inferred_from_issuer():
    without=INCOME.replace('SEK m','')
    assert aligned_group_cell('net_interest_income','Q326',[page(without)]) is None


@pytest.mark.parametrize('question,metric,periods',[
    ("Give Handelsbanken's signed staff-cost amount in Q3 2026",'staff_costs',('Q326',)),
    ("What was Handelsbanken's total asset balance on 30 June 2026?",'total_assets',('Q226',)),
    ("How much did total assets decline from 30 June to 30 September 2026?",'total_assets',('Q226','Q326')),
    ("How much did loans to the public grow from June to September 2026?",'loans_public',('Q226','Q326')),
    ("What was the Q2-to-Q3 2026 change in other income?",'other_income_group',('Q226','Q326')),
    ("What was the difference in net gains/losses on financial transactions Q3 than Q2 2026?",'net_gains_financial',('Q326','Q226')),
])
def test_interpretation(question,metric,periods):
    plan=plan_question(question)
    assert (plan.metric,plan.target_periods)==(metric,periods)


def test_manual_selected_source_edition_does_not_reject_historical_column():
    r=resolve_documents("What was Handelsbanken's net interest income for Q2 2026 according to its Q3 interim report?",
             DocumentSelection('manual',('hb',)),[D])
    assert len(r.documents)==1 and r.documents[0].document_id=='hb'


def test_manual_wrong_issuer_still_refused():
    r=resolve_documents("What was SEB's operating profit in Q3 2026?",
             DocumentSelection('manual',('hb',)),[D,S])
    assert not r.documents and r.clarification


def test_explicit_date_auto_chooses_unique_two_banks():
    r=resolve_documents('How many SEK million more total assets did SEB have than Handelsbanken at 30 September 2026?',
             DocumentSelection(),[D,S,Q])
    assert {d.document_id for d in r.documents}=={'hb','seb'}


def test_unknown_issuer_does_not_use_unrelated_bank():
    r=resolve_documents('How much total assets did BankThatIsNotIndexed have in Q3 2026?',
             DocumentSelection(),[D,S,Q])
    assert not r.documents


def test_narrative_scoped_quote_and_citation():
    snippet='Lending volumes rose, primarily driven by the UK and the Netherlands.'
    p=page('Q3 2026. '+snippet,2)
    model={'answer':'The UK and the Netherlands drove the rise.',
           'supported':True,'citation_ids':[1],'evidence_quotes':[snippet]}
    with patch('app.narrative_answering._get_pages',return_value=[p]), \
         patch('app.narrative_answering.hybrid_search',return_value=[]), \
         patch('app.narrative_answering._call_narrative_model',return_value=model):
        result=narrative_answer('Which home markets drove lending volumes?', 'hb')
    assert result['supported'] is True and result['sources'][0]['page']==2


def test_narrative_rejects_model_quote_from_unselected_pdf():
    p=page('Lending grew in several markets.',2)
    model={'answer':'An unsupported particular country','supported':True,'citation_ids':[1],
           'evidence_quotes':['The UK and Netherlands were the drivers.']}
    with patch('app.narrative_answering._get_pages',return_value=[p]), \
         patch('app.narrative_answering.hybrid_search',return_value=[{'document':'handelsbanken.pdf','page':2}]), \
         patch('app.narrative_answering._call_narrative_model',return_value=model):
        result=narrative_answer('Which home markets drove lending volumes?', 'hb')
    assert not result['supported']
