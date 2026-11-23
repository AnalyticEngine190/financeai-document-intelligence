"""Day 11.2: generic chunk boundaries and source-only narrative fallback.

No live index/Ollama, no benchmark file required. A separate Windows live
regression remains mandatory before reporting improved benchmark outcomes.
"""
from decimal import Decimal
from unittest.mock import patch
from app.layout_table_reader import aligned_group_cell
from app.narrative_extractive import direct_extractive_answer, _explicit_quarter
from app.narrative_answering import narrative_answer


def page(s, n=4, doc='generic-new-issuer.pdf'):
    return {'document':doc, 'page':n, 'text':s}


HEADER='''Quarterly Performance – Group
                        Q3        Q2        Q1
SEK m                  2026      2026      2026
'''


def test_chunk_boundary_removed_numeric_indentation():
    source=HEADER+'''Depreciation, amortisation and impairment of tangible and intangible
-508        -464        -482
assets
Other expenses              -1,584        -1,756        -1,677
'''
    cell=aligned_group_cell('depreciation_impairment','Q326',[page(source)])
    assert cell and cell.value == Decimal('-508') and cell.page == 4
    assert cell.raw_value=='-508'


def test_chunk_boundary_secondary_period_correct():
    source=HEADER+'''Depreciation, amortisation and impairment of tangible and intangible
-508        -464        -482
assets
'''
    cell=aligned_group_cell('depreciation_impairment','Q226',[page(source)])
    assert cell and cell.value==Decimal('-464')


def test_do_not_steal_unrelated_next_row():
    source=HEADER+'''Depreciation, amortisation and impairment of tangible and intangible
Other expenses              -1,584        -1,756        -1,677
'''
    assert aligned_group_cell('depreciation_impairment','Q326',[page(source)]) is None


def test_do_not_accept_wrong_column_count():
    source=HEADER+'''Depreciation, amortisation and impairment of tangible and intangible
-508        -464
assets
'''
    assert aligned_group_cell('depreciation_impairment','Q326',[page(source)]) is None


def test_do_not_accept_text_mixed_with_numeric_continuation():
    source=HEADER+'''Depreciation, amortisation and impairment of tangible and intangible
-508        -464        -482 extra
assets
'''
    assert aligned_group_cell('depreciation_impairment','Q326',[page(source)]) is None


DISCLOSURES='''Quarterly results, Q3 2026
Assets under management in the savings business increased, positively impacted by both rising market values and continued strong net inflows.

Capital position
The common equity tier 1 ratio was 17.0%. This was 2.5 percentage points above the regulatory requirement of 14.5%.

A new PD model for corporate exposures was approved in the preceding quarter and implemented during the current quarter.
'''


def test_extractive_factors_verbatim():
    q='What two factors drove the rise in savings-business assets under management in Q3 2026?'
    got=direct_extractive_answer(q,[page(DISCLOSURES,2)])
    assert got and got['supported'] and got['sources'][0]['page']==2
    assert 'rising market values' in got['answer'] and 'net inflows' in got['answer']


def test_extractive_ratio_and_buffer():
    q='What was the common equity tier 1 ratio relative to the regulatory capital requirement in Q3 2026?'
    got=direct_extractive_answer(q,[page(DISCLOSURES,2)])
    assert got and '17.0%' in got['answer'] and '14.5%' in got['answer'] and '2.5 percentage points' in got['answer']


def test_extractive_completed_event():
    q='What happened to the corporate PD model during the third quarter of 2026?'
    got=direct_extractive_answer(q,[page(DISCLOSURES,2)])
    assert got and 'implemented' in got['answer'] and got['sources']==[{'document':'generic-new-issuer.pdf','page':2}]


def test_extractive_missing_subject_not_guessed():
    q='What two factors drove the decline in mortgage originations in Q3 2026?'
    assert direct_extractive_answer(q,[page(DISCLOSURES,2)]) is None


def test_extractive_absent_relation_not_guessed():
    q='Why did assets under management increase during Q3 2026?'
    assert direct_extractive_answer(q,[page('Q3 2026. Assets under management were SEK 45bn.',2)]) is None


def test_extractive_rejects_multiple_documents():
    q='What two factors drove savings-business assets under management in Q3 2026?'
    assert direct_extractive_answer(q,[page(DISCLOSURES,2),page(DISCLOSURES,2,'unselected.pdf')]) is None


def test_extractive_rejects_wrong_quarter():
    q='What happened to the corporate PD model during the first quarter of 2026?'
    assert direct_extractive_answer(q,[page(DISCLOSURES,2)]) is None


def test_extractive_rejects_hypotheticals():
    q='What two factors would explain the rise in savings-business assets under management in Q3 2026?'
    assert direct_extractive_answer(q,[page(DISCLOSURES,2)]) is None


def test_extractive_rejects_weak_name_overlap():
    q="What happened to Corporate XyzAlpha's corporate PD model during Q3 2026?"
    assert direct_extractive_answer(q,[page(DISCLOSURES,2)]) is None


def test_verified_model_still_wins_and_unchanged():
    snippet='Net interest income increased because of greater customer deposits.'
    pages=[page('Q3 2026. '+snippet,5)]
    model={'answer':'Greater customer deposits','supported':True,
           'citation_ids':[1],'evidence_quotes':[snippet]}
    with patch('app.narrative_answering._get_pages',return_value=pages), \
         patch('app.narrative_answering.hybrid_search',return_value=[]), \
         patch('app.narrative_answering._call_narrative_model',return_value=model):
        got=narrative_answer('What factors caused net interest income to increase?','doc_id')
    assert got['answer']=='Greater customer deposits'
    assert got['sources'][0]['page']==5


def test_narrative_fallback_after_model_abstains():
    pages=[page(DISCLOSURES,2)]
    with patch('app.narrative_answering._get_pages',return_value=pages), \
         patch('app.narrative_answering.hybrid_search',return_value=[]), \
         patch('app.narrative_answering._call_narrative_model',return_value={'supported':False}):
        got=narrative_answer('What happened to the corporate PD model during the third quarter of 2026?','doc_id')
    assert got['supported'] and got['sources'][0]['page']==2


def test_narrative_does_not_replace_unsupported_fact_with_invention():
    pages=[page(DISCLOSURES,2)]
    with patch('app.narrative_answering._get_pages',return_value=pages), \
         patch('app.narrative_answering.hybrid_search',return_value=[]), \
         patch('app.narrative_answering._call_narrative_model',return_value={'supported':False}):
        got=narrative_answer('Why did the bank conceal its derivatives fraud in Q3 2026?','doc_id')
    assert not got['supported'] and not got['sources']
