"""Day 10.5: new adversarial tests, independent of the Day 10.4 gold file."""
from decimal import Decimal
from unittest.mock import patch
import pytest
from app.financial_plan import plan_question
from app.document_tables import generic_cell, segment_volume_cell
from app.document_registry import DocumentRecord
from app.document_resolver import resolve_documents, DocumentSelection
from app.document_answering import answer_with_selection
from app.evidence_verifier import verify_cell
from app.table_lookup import FinancialCell
from app.narrative_answering import narrative_answer

SEB=DocumentRecord('seb-id','seb.pdf','SEB',2026,3,'interim_report','SEK')
NQ2=DocumentRecord('nq2-id','nq2.pdf','Nordea',2026,2,'factbook','EUR')
NQ3=DocumentRecord('nq3-id','nq3.pdf','Nordea',2026,3,'factbook','EUR')
DOCS=[SEB,NQ2,NQ3]
SEB_P5='''Income statement on a quarterly basis, condensed
                    Q3        Q2        Q1        Q4        Q3
SEK m               2026      2026      2026      2025      2025
Net financial income¹⁾        1 124     2 421     1 699     1 709     1 767
Net other income             -81       -247        56       22        70
Net expected credit losses    179       345        546      387       203'''
SEB_P6='''Key figures
                            Q3          Q2         Q3
                            2026        2026       2025
Cost/income ratio             0.44        0.40      0.42
Net Stable Funding Ratio (NSFR), %     113      110      116
Liquidity Coverage Ratio (LCR), %     136      125      136'''
SEB_P25='''Balance sheet, condensed
                               30 Sep     30 Jun     31 Dec
SEK m                           2026        2026       2025
TOTAL ASSETS                 4 439 488   4 354 686   3 670 681
Deposits and borrowings from the public¹⁾   1 978 530   2 029 659   1 701 902'''
SEB_P48=SEB_P25.replace('4 439 488','3 558 084').replace('1 978 530','1 713 739')
NQ2_P13='''BUSINESS AREAS
Personal Banking
Financial highlights
EURm                             Q226  Q126  Q425  Q325  Q225  Q125
Total income                     1,146  1,127  1,149  1,164  1,173  1,184
Volumes
EURbn                             Q226  Q126  Q425  Q325  Q225  Q125
Total deposits                   99.9   97.2   96.2   95.6   95.1   92.9'''


def pages(*pairs):
    return [{'document':filename,'page':page,'text':text} for filename,page,text in pairs]

@pytest.mark.parametrize('q,metric,target,op',[
    ("According to SEB's Q3 2026 report, what was net other income in Q4 2025?",'net_other_income',('Q425',),'lookup'),
    ("What was SEB's net financial income in Q2 2026, not its net interest income?",'net_financial_income',('Q226',),'lookup'),
    ("What was SEB's liquidity coverage ratio in Q2 2026 as a percentage?",'lcr',('Q226',),'lookup'),
    ("By how much did Nordea total assets fall between Q2 and Q3 2026?",'total_assets',('Q226','Q326'),'decline'),
    ("What was SEB's net stable funding ratio (NSFR) for Q3 2026?",'nsfr',('Q326',),'lookup'),
    ("What was Nordea's ordinary loan loss ratio in Q2 2026?",'loan_loss_ratio',('Q226',),'lookup'),
    ("How large were Personal Banking's total deposits at Nordea in Q2 2026?",'segment_total_deposits',('Q226',),'lookup'),
])
def test_plan_metric_period_operation(q,metric,target,op):
    plan=plan_question(q)
    assert plan.metric==metric and plan.target_periods==target and plan.operation==op


def test_source_edition_not_treated_as_target():
    p=plan_question("According to SEB's Q3 2026 report, what was net other income in Q4 2025?")
    assert p.source_edition=='Q326' and p.target_periods==('Q425',)
    d=resolve_documents("According to SEB's Q3 2026 report, what was net other income in Q4 2025?",DocumentSelection(),DOCS)
    assert len(d.documents)==1 and d.documents[0].document_id=='seb-id'


def test_forecast_not_reinterpreted_as_historic_fact():
    with patch('app.document_tables._get_pages',return_value=pages(('seb.pdf',5,SEB_P5))):
        r=answer_with_selection("According to SEB's Q3 2026 report, what is net financial income forecast for Q1 2032?",documents=DOCS)
    assert not r['supported'] and not r.get('needs_clarification')


@pytest.mark.parametrize('metric,period,expected,unit,page',[
    ('net_financial_income','Q226','2421','SEKm',5),
    ('net_other_income','Q425','22','SEKm',5),
    ('net_expected_credit_losses','Q326','179','SEKm',5),
    ('nsfr','Q326','113','percent',6),
    ('lcr','Q226','125','percent',6),
    ('cost_income_ratio','Q326','0.44','ratio',6),
    ('total_assets','Q326','4439488','SEKm',25),
    ('deposits_public','Q326','1978530','SEKm',25),
])
def test_seb_tables_no_parent_company_substitution(metric,period,expected,unit,page):
    ps=pages(('seb.pdf',5,SEB_P5),('seb.pdf',6,SEB_P6),('seb.pdf',25,SEB_P25),
             ('seb.pdf',47,'Skandinaviska Enskilda Banken AB (publ) – parent company'),
             ('seb.pdf',48,SEB_P48))
    with patch('app.document_tables._get_pages',return_value=ps):
        c=generic_cell(metric,period,'seb-id')
        assert c and str(c.value)==expected and c.unit==unit and c.page==page
        verdict=verify_cell(c,document_id='seb-id',metric=metric,period=period)
        assert verdict.accepted,verdict.reason


def test_segment_volume_isolated():
    with patch('app.document_tables._get_pages',return_value=pages(('nq2.pdf',13,NQ2_P13))):
        c=segment_volume_cell('segment_total_deposits','Q226','nq2-id','personal banking')
        assert c and str(c.value)=='99.9' and c.scope=='personal banking'
        assert segment_volume_cell('segment_total_deposits','Q226','nq2-id','business banking') is None


def test_forged_cell_row_rejected():
    f=FinancialCell('total_assets','Q326','group',Decimal('987654'),'SEKm','seb.pdf',25,
                    'Total assets','987 654')
    with patch('app.document_tables._get_pages',return_value=pages(('seb.pdf',25,SEB_P25))):
        verdict=verify_cell(f,document_id='seb-id',metric='total_assets',period='Q326')
    assert not verdict.accepted


def test_source_leakage_rejected():
    c=FinancialCell('total_assets','Q326','group',Decimal('4439488'),'SEKm','wrong.pdf',25,
                    'Total assets','4 439 488')
    with patch('app.document_tables._get_pages',return_value=pages(('seb.pdf',25,SEB_P25))):
        verdict=verify_cell(c,document_id='seb-id',metric='total_assets',period='Q326')
    assert not verdict.accepted


def test_cross_currency_fails_without_data_fetch():
    r=answer_with_selection('How many more million total assets did SEB report than Nordea in Q3 2026?',documents=DOCS)
    assert not r['supported'] and 'exchange rate' in r['answer'] and r['sources']==[]


def test_narrative_rejects_fabricated_quote():
    evidence=pages(('seb.pdf',7,'Net interest income increased mainly due to higher lending volumes and rates.'))
    with patch('app.narrative_answering._get_pages',return_value=evidence),\
         patch('app.narrative_answering.hybrid_search',return_value=[{'document':'seb.pdf','page':7}]),\
         patch('app.narrative_answering._call_narrative_model',return_value={
             'supported':True,'answer':'Improved consumer sentiment','citation_ids':[1],
             'evidence_quotes':['A very different unsupported quotation']}):
        r=narrative_answer('Why did net interest income increase?','seb-id')
    assert r['supported'] is False


def test_narrative_accepts_grounded_exact_quote():
    text='Net interest income increased mainly due to higher lending volumes and rates.'
    evidence=pages(('seb.pdf',7,text))
    with patch('app.narrative_answering._get_pages',return_value=evidence),\
         patch('app.narrative_answering.hybrid_search',return_value=[{'document':'seb.pdf','page':7}]),\
         patch('app.narrative_answering._call_narrative_model',return_value={
             'supported':True,'answer':'Higher lending volumes and rates drove the increase.',
             'citation_ids':[1], 'evidence_quotes':[text]}):
        r=narrative_answer('What drove net interest income?','seb-id')
    assert r['supported'] and r['sources'][0]['page']==7
