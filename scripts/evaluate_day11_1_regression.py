"""Versioned Day11 post-fix regression. NEVER overwrites the independent 14/50 run."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter,defaultdict
from scripts.day11_12_common import (ROOT, API_URL, SEALED, digest,
                                       validate_dataset, grade, post_json)
from scripts.freeze_day11_1 import check

PROTECTED=[ROOT/'reports/evaluation/day11_first_run',
           ROOT/'reports/evaluation/day11_first_valid_run',
           ROOT/'reports/evaluation/day11_12_freeze',
           ROOT/'reports/evaluation/day11_1_freeze']


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--api-url',default=API_URL)
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    freeze=check(args.api_url)
    cases=validate_dataset()['cases']
    timestamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest=(args.output or ROOT/'reports/evaluation/day11_1_regressions'/timestamp).resolve()
    if any(dest==p.resolve() or p.resolve() in dest.parents for p in PROTECTED):
        raise SystemExit('Refusing protected result/freeze destination')
    dest.mkdir(parents=True,exist_ok=False)
    metadata={'kind':'POST_FIX_DEVELOPMENT_REGRESSION_NOT_INDEPENDENT',
              'original_first_valid_score':'14/50 (unchanged)',
              'api_endpoint':args.api_url.rstrip('/')+'/ask_v2',
              'gold_sha256':digest(SEALED),
              'freeze_sha256':digest(ROOT/'reports/evaluation/day11_1_freeze/manifest.json'),
              'started_utc':datetime.now(timezone.utc).isoformat()}
    (dest/'run_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf8')
    rows=[]
    with (dest/'results.jsonl').open('x',encoding='utf8') as out:
        for index,case in enumerate(cases,1):
            start=time.monotonic()
            try:
                response=post_json(args.api_url.rstrip('/')+'/ask_v2',
                   {'question':case['question'],'document_selection':case['document_selection']})
                status,reason=grade(case,response)
            except Exception as exc:
                response={'supported':False,'sources':[],
                          'transport_exception':type(exc).__name__,'detail':str(exc)}
                status,reason='FAIL','transport_or_runtime_error'
            result={'id':case['id'],'category':case['category'],'question':case['question'],
                    'status':status,'failure_code':reason,'response':response,
                    'elapsed_s':round(time.monotonic()-start,3)}
            out.write(json.dumps(result,ensure_ascii=False)+'\n');out.flush()
            rows.append(result)
            print(f'[{index:02d}/{len(cases)}] {case["id"]:<8} {status:<7} {reason}',flush=True)
    summary={'note':'Post-fix DEVELOPMENT regression, original independent baseline 14/50 unchanged',
             'total':len(cases),'counts':dict(Counter(x['status'] for x in rows)),
             'by_category':{cat:dict(Counter(x['status'] for x in rows if x['category']==cat))
                           for cat in sorted({x['category'] for x in rows})},
             'failures':[{'id':x['id'],'reason':x['failure_code']} for x in rows if x['status']=='FAIL'],
             'human_review':[x['id'] for x in rows if x['status']=='REVIEW']}
    (dest/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf8')
    lines=['# Day 11.1 post-fix development regression','',
           'Original valid unseen 14/50 preserved; this run is not independent.','',
           '## Counts',str(summary['counts']),'','## Failures']
    for x in summary['failures']:lines.append(f'- {x["id"]}: {x["reason"]}')
    lines.extend(['','## Narrative human review',*('- '+x for x in summary['human_review'])])
    (dest/'analysis.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print('SAVED TO:',dest,'COUNTS:',summary['counts'])
if __name__=='__main__':main()
