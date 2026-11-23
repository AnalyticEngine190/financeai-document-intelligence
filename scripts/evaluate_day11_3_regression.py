"""Day 11.3 DEVELOPMENT ONLY: 50 known questions, never the independent first run."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from scripts.day11_12_common import ROOT, API_URL, SEALED, digest, validate_dataset, grade, post_json
from scripts.freeze_day11_3 import check

PROTECTED=[ROOT/'reports/evaluation'/name for name in (
    'day11_first_run','day11_first_valid_run','day11_12_freeze',
    'day11_1_freeze','day11_1_regressions','day11_2_freeze','day11_2_regressions','day11_3_freeze')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url',default=API_URL,help='API BASE URL, e.g. http://127.0.0.1:8000')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--ids',help='Optional comma-delimited list for quick targeted regression')
    args=parser.parse_args()
    freeze=check(args.api_url)
    all_cases=validate_dataset()['cases']
    cases=all_cases
    if args.ids:
        requested=[i.strip() for i in args.ids.split(',') if i.strip()]
        known={c['id'] for c in all_cases}
        if not requested or len(requested)!=len(set(requested)) or not set(requested)<=known:
            raise SystemExit('Unknown or duplicated --ids; refusing to run')
        cases=[c for c in all_cases if c['id'] in requested]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest=(args.output or ROOT/'reports/evaluation/day11_3_regressions'/stamp).resolve()
    if any(dest==p.resolve() or p.resolve() in dest.parents for p in PROTECTED):
        raise SystemExit('Refusing to write within an archived result/freeze folder')
    dest.mkdir(parents=True,exist_ok=False)
    url=args.api_url.rstrip('/')
    url=url if url.endswith('/ask_v2') else url+'/ask_v2'
    metadata={'kind':'POST_FIX_DEVELOPMENT_REGRESSION_NOT_INDEPENDENT',
              'original_first_valid_score':'14/50 (unchanged)',
              'previous_regression':'Day 11.2 targeted 1 PASS / 1 FAIL / 2 REVIEW (archived)',
              'total_questions':len(cases),'question_ids':[c['id'] for c in cases],
              'api_endpoint':url,'gold_sha256':digest(SEALED),
              'freeze_sha256':digest(ROOT/'reports/evaluation/day11_3_freeze/manifest.json'),
              'started_utc':datetime.now(timezone.utc).isoformat()}
    (dest/'run_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf8')
    rows=[]
    with (dest/'results.jsonl').open('x',encoding='utf8') as output:
        for i,case in enumerate(cases,1):
            start=time.monotonic()
            try:
                response=post_json(url,{'question':case['question'],
                                        'document_selection':case['document_selection']})
                status,reason=grade(case,response)
            except Exception as exc:
                response={'supported':False,'sources':[],
                          'transport_exception':type(exc).__name__,'detail':str(exc)}
                status,reason='FAIL','transport_or_runtime_error'
            row={'id':case['id'],'category':case['category'],'question':case['question'],
                 'status':status,'failure_code':reason,'response':response,
                 'elapsed_s':round(time.monotonic()-start,3)}
            output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush()
            rows.append(row)
            print(f'[{i:02d}/{len(cases)}] {case["id"]:<8} {status:<7} {reason}',flush=True)
    counts=Counter(r['status'] for r in rows)
    summary={'note':'Development only; first valid unseen 14/50 score preserved',
             'total':len(rows),'counts':dict(counts),
             'by_category':{c:dict(Counter(r['status'] for r in rows if r['category']==c))
                            for c in sorted({r['category'] for r in rows})},
             'failures':[{'id':r['id'],'reason':r['failure_code']} for r in rows if r['status']=='FAIL'],
             'human_review':[r['id'] for r in rows if r['status']=='REVIEW']}
    (dest/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf8')
    lines=['# Day 11.3 development regression','',
           'First valid independent Handelsbanken score remains **14/50**, unchanged.','',
           '## Counts',str(dict(counts)),'','## Failures',
           *(f'- {r["id"]}: {r["failure_code"]}' for r in rows if r['status']=='FAIL'),
           '','## Narrative cases requiring human review',
           *(f'- {r["id"]}: {r["response"].get("answer", "")}' for r in rows if r['status']=='REVIEW')]
    (dest/'analysis.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print('SAVED TO:',dest,'COUNTS:',dict(counts))

if __name__=='__main__':main()
