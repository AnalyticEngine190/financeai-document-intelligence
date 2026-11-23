"""Day 11.3: direct regulatory relation takes verified selected-PDF text.

All tests use synthetic excerpts and never access actual benchmark answers,
PDF files, Chroma, or Ollama. This checks general safety properties.
"""
from unittest.mock import patch

from app.narrative_answering import narrative_answer, _source_first_regulatory_relation


def page(content, number=2, document='unseen-bank.pdf'):
    return {'text': content, 'document': document, 'page': number}


REPORT = '''Interim results Q3 2026
The common equity tier 1 ratio, after expected dividends, was 18.1%. This was 3.4 percentage points above the regulatory requirement, which amounted to 14.7%.
'''
QUESTION = 'What was the bank CET1 ratio relative to the regulatory capital requirement in Q3 2026?'


def test_direct_relation_returns_exact_page_and_values():
    got = _source_first_regulatory_relation(QUESTION, [page(REPORT)])
    assert got and got['supported']
    assert got['sources'] == [{'document':'unseen-bank.pdf', 'page':2}]
    assert '18.1%' in got['answer'] and '3.4 percentage points' in got['answer']
    assert '14.7%' in got['answer']


def test_no_ollama_or_retrieval_when_direct_supported():
    with patch('app.narrative_answering._get_pages', return_value=[page(REPORT)]), \
         patch('app.narrative_answering.hybrid_search',side_effect=AssertionError('hybrid path should not run')), \
         patch('app.narrative_answering._call_narrative_model',side_effect=AssertionError('LLM should not run')):
        got=narrative_answer(QUESTION,'selected_document_id')
    assert got['supported']


def test_extractive_does_not_cross_selected_document_boundary():
    result = _source_first_regulatory_relation(QUESTION, [page(REPORT),page(REPORT,3,'other-bank.pdf')])
    assert result is None


def test_wrong_quarter_abstains_from_preemption():
    q='What was the CET1 ratio relative to the regulatory capital requirement in Q2 2026?'
    assert _source_first_regulatory_relation(q,[page(REPORT)]) is None


def test_hypothetical_does_not_preempt():
    q='What would the CET1 ratio be relative to the regulatory requirement in Q3 2026?'
    assert _source_first_regulatory_relation(q,[page(REPORT)]) is None


def test_weak_metric_relationship_does_not_preempt():
    q='What was the CET1 ratio relative to the regulatory capital requirement in Q3 2026?'
    assert _source_first_regulatory_relation(q,[page('Interim results Q3 2026. CET1 ratio was 18.1%.')]) is None


def test_unrelated_causal_question_still_uses_model_and_quotes():
    narrative='Net interest income increased due to higher lending volumes.'
    model={'answer':'Higher lending volumes', 'supported':True,
           'citation_ids':[1], 'evidence_quotes':[narrative]}
    with patch('app.narrative_answering._get_pages', return_value=[page('Q3 2026. '+narrative)]), \
         patch('app.narrative_answering.hybrid_search',return_value=[]), \
         patch('app.narrative_answering._call_narrative_model',return_value=model) as call:
        got=narrative_answer('Why did net interest income increase in Q3 2026?','doc1')
    assert call.called and got['supported']
    assert got['answer']=='Higher lending volumes'


def test_invalid_model_quote_still_fails_closed_when_no_direct_extraction():
    text='Q3 2026. The regulatory capital ratio was 18.1%.'
    model={'answer':'It was 14.7%.','supported':True,'citation_ids':[1],
           'evidence_quotes':['The requirement was 14.7% and the bank exceeded it.']}
    with patch('app.narrative_answering._get_pages',return_value=[page(text)]), \
         patch('app.narrative_answering.hybrid_search',return_value=[]), \
         patch('app.narrative_answering._call_narrative_model',return_value=model):
        got=narrative_answer('What was the regulatory capital ratio relative to requirements in Q3 2026?','doc1')
    assert not got['supported'] and got['sources']==[]


def test_source_synthetic_values_not_benchmark_constants():
    text='''Q3 2026. The regulatory capital ratio was 21.6%. It was 4.5 percentage points above the regulatory requirement of 17.1%.'''
    q='How did the regulatory capital ratio compare to the regulatory requirement in Q3 2026?'
    got=_source_first_regulatory_relation(q,[page(text)])
    assert got and '21.6%' in got['answer'] and '4.5 percentage points' in got['answer']
