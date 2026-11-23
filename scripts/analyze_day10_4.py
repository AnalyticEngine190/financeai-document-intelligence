"""Post-run failure taxonomy, without altering the saved first-run score."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from scripts.evaluate_day10_4 import OUT, RESULTS, validate_dataset

GROUPS={
 'wrong_source_document':'Document isolation failure',
 'missing_required_report_citation':'Citation / evidence completeness',
 'unexpected_source_page':'Citation / evidence integrity',
 'invalid_citation_format':'Citation / evidence integrity',
 'wrong_primary_number':'Metric / period / calculation error (manual triage required)',
 'missing_primary_number':'Answer formatting / numerical extraction',
 'answer_not_supported':'Abstention on answerable question (inspect retrieval/parser/table logs)',
 'expected_clarification':'Ambiguity / document resolver behavior',
 'expected_clean_abstention':'Unsupported-data / source-safety behavior',
 'missing_currency_incompatibility_explanation':'Cross-currency safety explanation',
 'missing_percentage_unit':'Unit formatting',
 'missing_percentage_points_unit':'Unit formatting',
 'missing_basis_points_unit':'Unit formatting',
 'missing_million_unit':'Unit formatting',
 'missing_billion_unit':'Unit formatting',
 'missing_currency_code':'Unit formatting',
 'ratio_requested_as_decimal_but_percent_supplied':'Metric unit semantics',
 'transport_or_runtime_error':'Infrastructure / API exception',
 'explanation_too_short':'Narrative answer quality',
}


def run(review_path:Path|None=None) -> Path:
    if not RESULTS.exists():raise RuntimeError('Run the 40-question first benchmark before analysis')
    results=[json.loads(line) for line in RESULTS.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(results)!=40:raise RuntimeError(f'First-run incomplete: {len(results)}/40. Use --resume first.')
    dataset=validate_dataset()
    gold={c['id']:c for c in dataset['cases']}
    review={}
    if review_path:
        review=json.loads(review_path.read_text(encoding='utf-8'))
        if not isinstance(review,dict):raise ValueError('Human review must be a JSON object')
        for cid,decision in review.items():
            if cid not in gold or gold[cid]['expected_outcome']!='explanation' or decision.get('status') not in ('PASS','FAIL') or not str(decision.get('notes','')).strip():
                raise ValueError(f'Invalid human review: {cid}. Require PASS/FAIL and notes.')
    lines=['# Day 10.4 — First-run postmortem','',
           '**Original saved first-run responses are immutable.** This report is a derived view.','',
           '| Category | Pass | Fail | Narrative review pending |',
           '|---|---:|---:|---:|']
    categories=defaultdict(Counter)
    passed=failed=pending=0
    for r in results:
        status=r['status']
        if status=='REVIEW' and r['id'] in review: status=review[r['id']]['status']
        categories[r['category']][status]+=1
        if status=='PASS':passed+=1
        elif status=='FAIL':failed+=1
        else:pending+=1
    for name in ('nordea_q2','new_bank','isolation','cross_document','unsupported_ambiguous'):
        c=categories[name]
        lines.append(f'| {name} | {c["PASS"]} | {c["FAIL"]} | {c["REVIEW"]} |')
    lines += ['',f'**Reviewed pass:** {passed}/40. **Failed:** {failed}. **Narrative review pending:** {pending}.',
              '', '## Failed or pending cases','']
    for r in results:
        status=r['status']
        if status=='REVIEW' and r['id'] in review:status=review[r['id']]['status']
        if status=='PASS':continue
        issue=r['failure_code']
        lines += [f'### {r["id"]} — {status}',
                  f'**Question:** {r["question"]}',
                  f'**Expected:** {r["expected_outcome"]}; value {r["expected_value"]}, unit {r["expected_unit"]}; citations {r["expected_sources"]}',
                  f'**Returned:** {r["response"].get("answer", "")}',
                  f'**Observed sources:** {r["response"].get("sources", [])}',
                  f'**Issue:** `{issue}` — {GROUPS.get(issue,"Human review / additional diagnosis needed")}',
                  '**Attribution:** Needs log/code tracing; the output alone does not establish whether retrieval, parsing, or table extraction failed.',
                  '']
    if pending:
        lines += ['## Pending manual review',
                  'Read `narrative_review.md`, then create `human_review.json` containing decisions and notes.',
                  'Example: `{"NEW_BANK_11":{"status":"PASS","notes":"Both required concepts supported by SEB page 7"}}`.','']
    lines += ['## Integrity and interpretation',
              '- This benchmark was created and its gold page/value references checked before the first live invocation.',
              '- Do not change the frozen gold, questions, index, model, or code to make this first-run score better.',
              '- If a reference answer is genuinely wrong, publish a labeled erratum and a corrected follow-up score, not a silent rewrite.',
              '- Previous 98/98 and 15/15 results are development regressions, not independent evaluations.',
              '- Financial interpretation/citation failures should be investigated before any new feature work.','']
    target=OUT/'analysis.md'
    target.write_text('\n'.join(lines),encoding='utf-8')
    print('Wrote',target,'pass',passed,'fail',failed,'pending',pending)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-json',type=Path)
    args=parser.parse_args()
    run(args.review_json)
if __name__=='__main__':main()
