"""Day10.5.2: model-evidence focus and fail-closed citation checks."""
from unittest.mock import patch
from app.narrative_evidence import select_narrative_evidence, render_narrative_evidence
from app.narrative_answering import _model_evidence, _verified_model_result, narrative_answer

Q_CAUSAL = 'What factors did the company cite for the quarter-on-quarter rise in net interest income?'
Q_CHANGE = 'What accounting presentation change relating to interest accruals on derivatives did the bank introduce?'
DRIVERS = ('Net interest income increased by 2 percent compared with the previous quarter, '
           'explained mainly by higher lending volumes and higher interest rates.')
DIVISION = ('Operating profit grew, driven by higher net interest income in the business division. '
            'Net interest income increased primarily due to segment-specific lending.')
CHANGE = ('Changed presentation of interest accruals on derivatives. From 2026, '
          'the accruals are reported in net interest income instead of net financial income.')


def page(n, content, doc='selected.pdf'):
    return {'document': doc, 'page': n, 'text': content}


def hits(*numbers, doc='selected.pdf'):
    return [{'document':doc, 'page':n} for n in numbers]


def test_hybrid_top_group_outranks_lexical_segment_distractors():
    pages = [page(7, DRIVERS + ' Group third quarter financial commentary.'),
             page(18, DIVISION + ' ' + ('net interest income ' * 12)),
             page(22, DIVISION + ' ' + ('net interest income ' * 12)),
             page(16, DIVISION + ' ' + ('net interest income ' * 12))]
    # No company name, specific value, or benchmark page is used by the selector.
    ranked = hits(7, 18, 22, 16)
    result = select_narrative_evidence(Q_CAUSAL, pages, ranked)
    assert result[0].page['page'] == 7
    assert _model_evidence(Q_CAUSAL, result) == result[:1]
    assert DRIVERS in render_narrative_evidence(_model_evidence(Q_CAUSAL, result))


def test_narrative_model_sees_precise_causal_page_and_cites_it():
    pages = [page(10, DRIVERS), page(20, DIVISION)]
    with patch('app.narrative_answering._get_pages', return_value=pages), \
         patch('app.narrative_answering.hybrid_search', return_value=hits(10,20)) as search, \
         patch('app.narrative_answering._call_narrative_model') as call:
        def respond(q, evidence):
            assert 'page 10' in evidence
            assert 'page 20' not in evidence
            return {'answer':'It was mainly driven by higher lending volumes and higher interest rates.',
                    'supported':True, 'citation_ids':[1],
                    'evidence_quotes':['explained mainly by higher lending volumes and higher interest rates.']}
        call.side_effect = respond
        response = narrative_answer(Q_CAUSAL, 'my-doc-id')
        assert response['supported']
        assert response['sources'] == [{'document':'selected.pdf','page':10}]
        assert search.call_args.kwargs['document_id'] == 'my-doc-id'


def test_presentation_change_keeps_multi_page_selection():
    pages = [page(3, 'Accounting presentation section. ' + CHANGE),
             page(4, 'The consolidated balance sheet is disclosed here.')]
    selected = select_narrative_evidence(Q_CHANGE, pages, hits(3,4))
    assert len(_model_evidence(Q_CHANGE, selected)) == 2


def test_model_abstention_reason_does_not_bypass_safety():
    selected = select_narrative_evidence(Q_CAUSAL, [page(10, DRIVERS)], hits(10))
    result, reason = _verified_model_result({'supported': False}, selected)
    assert result is None and reason == 'model_did_not_assert_support'


def test_fake_quote_and_fake_number_still_rejected():
    selected = select_narrative_evidence(Q_CAUSAL, [page(10, DRIVERS)], hits(10))
    a={'supported':True, 'answer':'A purported factor was a capital injection.',
       'citation_ids':[1], 'evidence_quotes':['They acquired another bank in 2026.']}
    result, reason = _verified_model_result(a, selected)
    assert result is None and reason == 'quote_not_in_cited_excerpt'
    a={'supported':True, 'answer':'The factor added 999 million.',
       'citation_ids':[1], 'evidence_quotes':['explained mainly by higher lending volumes']}
    result, reason = _verified_model_result(a, selected)
    assert result is None and reason == 'answer_has_unverified_numbers'


def test_invalid_source_ignored():
    selected = select_narrative_evidence(Q_CAUSAL, [page(10, DRIVERS)],
                                          hits(20,doc='another-bank.pdf')+hits(10))
    assert [v.page['page'] for v in selected] == [10]
