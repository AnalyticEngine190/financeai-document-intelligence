"""Post-fix regression of Day10.4 gold. NEVER modifies first-run records.

Uses same frozen Day10.4 questions/grade function with post-fix code. Outputs a
new separate directory, including any human-review cases. This is NOT an
independent performance measurement.
"""
from __future__ import annotations
import argparse,json,hashlib,sys,time
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
from scripts.evaluate_day10_4 import DEFAULT_DATASET, validate_dataset, grade, post_json, digest, ROOT
from scripts.day10_5_1_freeze import check as check_day10_5_1_freeze

FIRST_RUN=ROOT/'reports'/'evaluation'/'day10_4_first_run'


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--api-url',default='http://127.0.0.1:8000')
    ap.add_argument('--out',type=Path)
    a=ap.parse_args()
    check_day10_5_1_freeze(a.api_url)
    original=json.loads((FIRST_RUN/'run_metadata.json').read_text(encoding='utf-8'))
    if original['benchmark_sha256']!=digest(DEFAULT_DATASET):
        raise SystemExit('Original benchmark hash changed. Refusing to grade modified gold.')
    if not (FIRST_RUN/'results.jsonl').exists():
        raise SystemExit('Sealed first-run results are missing. Archive them before running this.')
    dataset=validate_dataset(DEFAULT_DATASET)
    time_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=(a.out or ROOT/'reports'/'evaluation'/'day10_5_1_regressions'/time_id).resolve()
    if out==FIRST_RUN.resolve() or FIRST_RUN.resolve() in out.parents or out==(
            ROOT/'reports'/'evaluation'/'day10_4_freeze').resolve():
        raise SystemExit('Refusing to overwrite Day10.4 first run or freeze.')
    out.mkdir(parents=True,exist_ok=False)
    # Capture the exact current application source state and original gold digest.
    paths=['app/document_answering.py','app/document_tables.py',
           'app/document_resolver.py','app/question_parser.py',
           'app/financial_plan.py','app/financial_vocabulary.py',
           'app/evidence_verifier.py','app/narrative_answering.py',
           'app/narrative_evidence.py']
    meta={'run_type':'day10_5_1_post_fix_development_regression_not_independent',
          'started_utc':datetime.now(timezone.utc).isoformat(),
          'day10_4_first_run_score':'19/40 (unchanged)',
          'gold_sha256':digest(DEFAULT_DATASET),
          'api_url':a.api_url,
          'source_sha256':{x:digest(ROOT/x) for x in paths}}
    (out/'run_metadata.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
    results=[]
    with (out/'results.jsonl').open('x',encoding='utf-8') as f:
        for i,c in enumerate(dataset['cases'],1):
            start=time.monotonic()
            try:
                r=post_json(a.api_url,{'question':c['question'],
                      'document_selection':c['document_selection']})
                state,issue=grade(c,r)
            except Exception as exc:
                r={'supported':False,'sources':[],
                   'transport_exception':type(exc).__name__, 'detail':str(exc)}
                state,issue='FAIL','transport_or_runtime_error'
            entry={'id':c['id'],'question':c['question'],
                   'category':c['category'],'expected_outcome':c['expected_outcome'],
                   'status':state,'issue':issue,'response':r,
                   'elapsed_s':round(time.monotonic()-start,3)}
            f.write(json.dumps(entry,ensure_ascii=False)+'\n');f.flush()
            results.append(entry)
            print(f'[{i:02d}/40] {c["id"]:<23} {state:<7} {issue}',flush=True)
    counts=Counter(r['status'] for r in results)
    groups=defaultdict(Counter)
    for r in results:groups[r['category']][r['status']]+=1
    summary={'note':'POST-FIX REGRESSION ONLY; the independent Day10.4 first-run result remains 19/40.',
             'counts':dict(counts),'categories':{x:dict(y) for x,y in groups.items()},
             'failures':[{'id':r['id'],'issue':r['issue']} for r in results if r['status']=='FAIL'],
             'review_required':[r['id'] for r in results if r['status']=='REVIEW']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print('Wrote',out,'\n',counts,'\nTwo narrative items may require manual review.')

if __name__=='__main__':main()
