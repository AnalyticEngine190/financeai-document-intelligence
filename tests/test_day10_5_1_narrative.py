"""Day 10.5.1: retrieval/excerpt/citation regressions (no live model needed)."""
from unittest.mock import patch
import pytest

from app.narrative_evidence import (
    MAX_EVIDENCE_PAGES, MAX_SEARCH_HITS,
    render_narrative_evidence, select_narrative_evidence,
)
from app.narrative_answering import narrative_answer

FILENAME = 'example.pdf'
Q_CHANGE = 'What accounting presentation change relating to interest accruals on derivatives did the bank introduce in January 2026?'
Q_DRIVERS = 'What factors did the bank cite for the quarter-on-quarter rise in net interest income?'
DISCLOSURE = ('The following reporting changes were implemented 1 January 2026. '
              'Changed presentation of interest accruals on derivatives that economically hedge banking book items. '
              'From 2026, these accruals are reported in net interest income instead of net financial income.')
DRIVERS = ('Net interest income increased compared with the previous quarter, '
           'explained mainly by higher volumes and higher interest rates.')


def page(number, text, doc=FILENAME):
    return {'document': doc, 'page': number, 'text': text}


def hits(*numbers, doc=FILENAME):
    return [{'document': doc, 'page': number} for number in numbers]


def test_rank_six_evidence_is_promoted_without_hardcoded_page_or_issuer():
    # The relevant excerpt is intentionally on page 43 at retrieval rank 6.
    # This must generalize to arbitrary issuer and page numbers.
    ps = [page(10, 'Accounting policies and comparative historical figures.'),
          page(11, 'Income statement and derivative cost breakdowns.'),
          page(12, 'Net interest income and earnings.'),
          page(13, 'Total assets and consolidated group information.'),
          page(14, 'Financial income and net commission expenses.'),
          page(43, 'Boilerplate. ' * 200 + '\n' + DISCLOSURE + '\nOther details. ' * 200)]
    selected = select_narrative_evidence(Q_CHANGE, ps, hits(10, 11, 12, 13, 14, 43))
    assert 43 in [v.page['page'] for v in selected]
    excerpt = next(v.excerpt for v in selected if v.page['page'] == 43)
    assert 'accruals are reported in net interest income instead' in excerpt
    assert len(excerpt) <= 1750
    assert excerpt in ps[-1]['text']  # exact substring, not synthesized text


def test_multiple_hybrid_hits_same_page_use_one_evidence_slot():
    ps = [page(1, 'The financial discussion and relevant policies.'), page(2, DISCLOSURE)]
    selected = select_narrative_evidence(Q_CHANGE, ps, hits(1, 1, 2, 2))
    assert len(selected) == 2
    assert {v.page['page'] for v in selected} == {1, 2}


def test_unselected_or_spoofed_search_result_is_never_used():
    ps = [page(5, 'Normal financial filing passages.')]
    ranked = hits(8, doc='another-bank.pdf') + hits(5)
    selected = select_narrative_evidence(Q_CHANGE, ps, ranked)
    assert len(selected) == 1
    assert selected[0].page['document'] == FILENAME
    assert selected[0].page['page'] == 5


def test_long_page_uses_relevant_late_passage_not_first_6500_chars():
    long_page = page(23, 'Generic balance sheet rows. ' * 450 + DISCLOSURE)
    selected = select_narrative_evidence(Q_CHANGE, [long_page], hits(23))
    assert len(long_page['text']) > 6500
    assert 'interest accruals on derivatives' in selected[0].excerpt
    assert DISCLOSURE[:45] in selected[0].excerpt


def test_causal_question_preserves_drivers_in_excerpt():
    text = ('Other finance disclosures. ' * 100 + '\n' + DRIVERS +
            '\nAdditional interest income disclosures. ' * 100 + '\n' + DISCLOSURE)
    selected = select_narrative_evidence(Q_DRIVERS, [page(7, text)], hits(7))
    assert 'higher volumes and higher interest rates' in selected[0].excerpt


def test_number_of_pages_and_context_size_are_bounded():
    ps = [page(i, ('Corporate financial narrative. ' * 140) + DISCLOSURE)
          for i in range(1, 13)]
    result = select_narrative_evidence(Q_CHANGE, ps, hits(*range(1, 13)))
    assert len(result) == MAX_EVIDENCE_PAGES
    assert all(len(v.excerpt) <= 1750 for v in result)
    assert len(render_narrative_evidence(result)) < 10500


def test_narrative_valid_quote_and_citation_pass():
    ps = [page(11, 'Historical accounts.'), page(43, DISCLOSURE)]
    with patch('app.narrative_answering._get_pages', return_value=ps), \
         patch('app.narrative_answering.hybrid_search', return_value=hits(11, 43)) as search, \
         patch('app.narrative_answering._call_narrative_model') as model:
        def respond(question, evidence):
            assert 'instead of net financial income' in evidence
            cite = next(i for i, part in enumerate(evidence.split('\n\n['), 1)
                        if 'instead of net financial income' in part)
            return {'answer': 'Interest accruals are now reported in net interest income instead of net financial income.',
                    'supported': True, 'citation_ids': [cite],
                    'evidence_quotes': ['From 2026, these accruals are reported in net interest income instead of net financial income.']}
        model.side_effect = respond
        r = narrative_answer(Q_CHANGE, 'selected-doc')
    assert search.call_args.kwargs == {'top_k': MAX_SEARCH_HITS, 'document_id': 'selected-doc'}
    assert r['supported'] and r['sources'] == [{'document': FILENAME, 'page': 43}]


def test_narrative_rejects_false_quote_even_if_page_contains_it_outside_excerpt():
    p = page(4, 'Suspicious claim is NOT evidence. ' * 2 + '\n' + 'Unrelated historical text. ' * 330 + '\n' + DISCLOSURE)
    with patch('app.narrative_answering._get_pages', return_value=[p]), \
         patch('app.narrative_answering.hybrid_search', return_value=hits(4)), \
         patch('app.narrative_answering._call_narrative_model', return_value={
              'answer': 'The suspicious claim was confirmed.',
              'supported': True, 'citation_ids': [1],
              'evidence_quotes': ['Suspicious claim is NOT evidence. ' * 2]}):
        r = narrative_answer(Q_CHANGE, 'selected-doc')
    assert not r['supported']


def test_narrative_fabricated_numeric_value_rejected():
    with patch('app.narrative_answering._get_pages', return_value=[page(43, DISCLOSURE)]), \
         patch('app.narrative_answering.hybrid_search', return_value=hits(43)), \
         patch('app.narrative_answering._call_narrative_model', return_value={
            'answer': 'This affected SEK 999999 million in 2026.',
            'supported': True, 'citation_ids': [1],
            'evidence_quotes': ['From 2026, these accruals are reported in net interest income instead of net financial income.']}):
        r = narrative_answer(Q_CHANGE, 'selected-doc')
    assert not r['supported']


def test_empty_or_malformed_search_remains_abstention():
    with patch('app.narrative_answering._get_pages', return_value=[page(43, DISCLOSURE)]), \
         patch('app.narrative_answering.hybrid_search', return_value=[{'document': 'unknown.pdf', 'page': 999}]):
        r = narrative_answer(Q_CHANGE, 'selected-doc')
    assert r['supported'] is False and r['sources'] == []


def test_bad_max_pages_rejected():
    with pytest.raises(ValueError):
        select_narrative_evidence(Q_CHANGE, [page(1, 'text')], hits(1), max_pages=0)
