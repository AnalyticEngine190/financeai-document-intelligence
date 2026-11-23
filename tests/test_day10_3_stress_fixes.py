"""Day 10.3: general failures found by manually testing a second-bank report.

Figures are test fixtures derived from the user's uploaded SEB quarterly report,
never constants used by application logic. Retrieval is always document-scoped.
"""
from decimal import Decimal
from unittest.mock import patch

from app.document_answering import answer_with_selection
from app.document_resolver import DocumentSelection, resolve_documents
from app.document_registry import DocumentRecord
from app.document_tables import generic_cell
from app.question_parser import parse_question
from app.table_lookup import FinancialCell

NQ3=DocumentRecord('nq3','nq3.pdf','Nordea',2026,3,'factbook','EUR')
NQ2=DocumentRecord('nq2','nq2.pdf','Nordea',2026,2,'factbook','EUR')
SQ3=DocumentRecord('sq3','seb.pdf','SEB',2026,3,'interim_report','SEK')
DOCS=[NQ3,NQ2,SQ3]

P5='''Income statement on a quarterly basis, condensed
                                        Q3       Q2       Q1       Q4       Q3
SEK m                                   2026     2026     2026     2025     2025
Net interest income¹⁾                   10 881   10 687   10 242   10 348   10 540
Operating profit before
items affecting comparability            9 412   10 774    9 432    9 241    9 719
Operating profit                         9 412   10 774    9 432    8 826    9 719
NET PROFIT                               7 498    8 664    7 497    7 308    7 677'''
SEB_PAGES=[{'document':'seb.pdf','page':5,'text':P5}]


def _nordea_stub(intent, document_id):
    if (document_id,intent.periods) == ('nq2',('Q226',)):
        return [FinancialCell(intent.metric,'Q226','group',Decimal(1779),'EURm','nq2.pdf',7,'Net interest income','1,779')]
    if (document_id,intent.periods) == ('nq3',('Q326',)):
        return [FinancialCell(intent.metric,'Q326','group',Decimal(1799),'EURm','nq3.pdf',7,'Net interest income','1,799')]
    return []


def test_excluded_competing_metric_is_not_selected():
    p=parse_question("What was SEB's net profit in Q3 2026, not its operating profit?")
    assert p.metric == 'net_profit' and p.periods == ('Q326',)
    with patch('app.document_tables._get_pages', return_value=SEB_PAGES):
        r=answer_with_selection("What was SEB's net profit in Q3 2026, not its operating profit?", documents=DOCS)
    assert r['supported'] and '7,498' in r['answer'] and r['sources'][0]['page']==5


def test_shared_year_period_expansion_and_calculations():
    q="By how many SEK million did SEB's operating profit decline between Q2 and Q3 2026?"
    p=parse_question(q)
    assert p.metric=='operating_profit' and p.periods==('Q226','Q326') and p.operation=='decline'
    with patch('app.document_tables._get_pages', return_value=SEB_PAGES):
        r=answer_with_selection(q, documents=DOCS)
    assert r['supported'] and '1,362 million decline' in r['answer'] and len(r['sources'])==1


def test_seb_same_report_two_quarter_increase():
    q="How much did SEB's net interest income increase from Q2 2026 to Q3 2026?"
    with patch('app.document_tables._get_pages', return_value=SEB_PAGES):
        r=answer_with_selection(q, documents=DOCS)
    assert r['supported'] and '194 million increase' in r['answer']
    assert r['sources'][0]['document_id']=='sq3'


def test_adjusted_operating_profit_never_matches_reported():
    adj="What was SEB's Q4 2025 operating profit, excluding items affecting comparability?"
    ordinary="What was SEB's reported operating profit in Q4 2025, including items affecting comparability?"
    assert parse_question(adj).metric=='operating_profit_adjusted'
    assert parse_question(ordinary).metric=='operating_profit'
    with patch('app.document_tables._get_pages', return_value=SEB_PAGES):
        x=answer_with_selection(adj,documents=DOCS)
        y=answer_with_selection(ordinary,documents=DOCS)
    assert x['supported'] and '9,241' in x['answer']
    assert y['supported'] and '8,826' in y['answer']


def test_automatic_two_nordea_editions_two_exact_citations():
    q="How much higher was Nordea's Q3 2026 net interest income than in Q2 2026?"
    r=resolve_documents(q,DocumentSelection(),DOCS)
    assert {d.document_id for d in r.documents} == {'nq3','nq2'}
    with patch('app.document_answering.resolve_intent_cells',side_effect=_nordea_stub):
        response=answer_with_selection(q,documents=DOCS)
    assert response['supported'] and 'EUR 20 million' in response['answer']
    assert {s['document_id'] for s in response['sources']} == {'nq3','nq2'}


def test_auto_cross_bank_comparison_never_mixes_currencies():
    q="How many more million did SEB earn in operating profit than Nordea in Q3 2026?"
    r=resolve_documents(q,DocumentSelection(),DOCS)
    assert {d.document_id for d in r.documents}=={'sq3','nq3'}
    def nordea_operating_profit(intent, doc):
        return [FinancialCell('operating_profit','Q326','group',Decimal(1541),'EURm','nq3.pdf',7,'Operating profit','1,541')]
    with patch('app.document_answering.resolve_intent_cells',side_effect=nordea_operating_profit), patch('app.document_tables._get_pages', return_value=SEB_PAGES):
        answer=answer_with_selection(q,documents=DOCS)
    assert not answer['supported'] and not answer['sources']
    assert 'incompatible' in answer['selection_reason'].casefold() or 'currency' in answer['selection_reason'].casefold()


def test_automatic_ambiguous_profit_still_clarifies():
    q='Compare Nordea and SEB Q3 2026 profit'
    r=resolve_documents(q,DocumentSelection(),DOCS)
    assert r.clarification and not r.documents


def test_future_period_report_edition_does_not_become_answer():
    q="According to SEB's Q3 2026 report, what was its operating profit in Q3 2030?"
    with patch('app.document_tables._get_pages', return_value=SEB_PAGES):
        r=answer_with_selection(q,documents=DOCS)
    assert not r['supported'] and not r['sources']


def test_manual_selection_never_auto_adds_second_report():
    q="How much higher was Nordea's Q3 2026 net interest income than in Q2 2026?"
    r=answer_with_selection(q,DocumentSelection('manual',('nq3',)),DOCS)
    assert r['needs_clarification'] and not r['sources']


def test_new_issuer_missing_operand_must_abstain():
    q="How much did SEB's net interest income increase from Q2 2026 to Q3 2026?"
    only_q3=P5.replace('10 687   10 242','   ---   10 242')
    with patch('app.document_tables._get_pages', return_value=[{'document':'seb.pdf','page':5,'text':only_q3}]):
        r=answer_with_selection(q,documents=DOCS)
    assert not r['supported'] and not r['sources']
