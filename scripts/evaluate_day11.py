"""Sealed, no-gold-leak, crash-resumable first run of 50 never-used questions.

    python -m scripts.evaluate_day11 --validate-only
    python -m scripts.evaluate_day11 --first-run
    python -m scripts.evaluate_day11 --resume

Responses are written and fsync'd one by one. First run never overwrites files.
"""
from __future__ import annotations
import argparse,json,os,time
from collections import Counter,defaultdict
from datetime import datetime,timezone
from scripts.day11_12_common import ROOT,API_URL,SEALED,FREEZE,FIRST,digest,load,write_once,validate_dataset,post_json,grade
from scripts.freeze_day11_12 import check


def now():return datetime.now(timezone.utc).isoformat()


def run(api_url,resume=False):
    frozen=check(api_url);ds=validate_dataset();cases=ds['cases']
    meta_file=FIRST/'run_metadata.json';file=FIRST/'results.jsonl'
    if resume:
        if not meta_file.exists() or not file.exists():raise RuntimeError('No first run to resume')
        meta=load(meta_file)
        if meta['gold_sha256']!=digest(SEALED) or meta['freeze_sha256']!=digest(FREEZE/'manifest.json') or meta['api_url']!=api_url:
            raise RuntimeError('Resume metadata mismatch; cannot mix benchmark versions')
    else:
        if FIRST.exists():raise RuntimeError('The sealed first run already exists. Use --resume only for interruptions.')
        FIRST.mkdir(parents=True,exist_ok=False)
        write_once(meta_file,{'started_utc':now(),'mode':'first_run','total':50,'gold_sha256':digest(SEALED),
                              'freeze_sha256':digest(FREEZE/'manifest.json'),'api_url':api_url})
        file.open('x',encoding='utf-8').close()
    prev=[json.loads(line) for line in file.read_text(encoding='utf-8').splitlines() if line.strip()]
    expected_ids=[c['id'] for c in cases]
    if [r['id'] for r in prev]!=expected_ids[:len(prev)]:raise RuntimeError('Tampered or reordered first-run records')
    print(f'SEALED FIRST RUN: {len(prev)} responses preserved, {50-len(prev)} remaining')
    with file.open('a',encoding='utf-8') as out:
        for i,c in enumerate(cases[len(prev):],len(prev)+1):
            t=time.monotonic()
            try:
                answer=post_json(api_url,{'question':c['question'],'document_selection':c['document_selection']})
                status,reason=grade(c,answer)
            except Exception as exc:
                answer={'supported':False,'sources':[], 'transport_exception':type(exc).__name__,
                        'detail':str(exc)[:300]}
                status,reason='FAIL','transport_or_runtime_error'
            r={'id':c['id'],'category':c['category'],'question':c['question'],'selection':c['document_selection'],
               'expected_outcome':c['expected_outcome'],'expected_value':c.get('expected_value'),
               'expected_unit':c.get('expected_unit'),'expected_sources':c['expected_sources'],
               'response':answer,'status':status,'failure_code':reason,
               'latency_seconds':round(time.monotonic()-t,3),'observed_utc':now()}
            out.write(json.dumps(r,ensure_ascii=False)+'\n');out.flush();os.fsync(out.fileno())
            prev.append(r)
            print(f'[{i:02d}/50] {c["id"]} {status:<6} {reason}',flush=True)
    counts=Counter(r['status'] for r in prev);by=defaultdict(Counter)
    for r in prev:by[r['category']][r['status']]+=1
    summary={'total':len(prev),'auto_passed':counts['PASS'],'failed':counts['FAIL'],
             'human_review_pending':counts['REVIEW'],
             'categories':{k:dict(v) for k,v in by.items()},
             'failure_codes':dict(Counter(r['failure_code'] for r in prev if r['status']=='FAIL')),
             'note':'This is the FIRST UNSEEN evaluation for Day 11; preserve all outputs, even if score is low.'}
    sf=FIRST/'summary.json'
    if sf.exists():
        # An interrupted run may already have a summary. Never overwrite sealed completed evaluation.
        if len(prev)==50:raise RuntimeError('Completed first run already summarized')
        raise RuntimeError('Unexpected partial summary; inspect before resuming')
    write_once(sf,summary)
    lines=['# Day 11 narrative human review','','Do not change the gold or original responses.',
           'Evaluate semantics and cited PDF evidence; short correct answers may pass.','']
    gold={c['id']:c for c in cases}
    for r in prev:
        if r['status']!='REVIEW':continue
        c=gold[r['id']]
        lines += [f'## {r["id"]}',f'Question: {r["question"]}',
                  f'Response: {r["response"].get("answer","")}',
                  f'Citation: {r["response"].get("sources",[])}',
                  'Checklist: '+' | '.join(c['human_review_criteria']),
                  'Human verdict: PENDING','Human reason: ','']
    (FIRST/'narrative_review.md').write_text('\n'.join(lines),encoding='utf-8')
    print('SEALED FIRST RUN SAVED:',FIRST,'|',dict(counts))


def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--validate-only',action='store_true');g.add_argument('--first-run',action='store_true');g.add_argument('--resume',action='store_true')
    p.add_argument('--api-url',default=API_URL);a=p.parse_args()
    try:
        if a.validate_only:
            d=validate_dataset();print('VALID: 50 PDF-audited, sealed questions; no API calls made.')
        else:run(a.api_url,resume=a.resume)
    except Exception as e:p.exit(2,f'ERROR: {e}\n')
if __name__=='__main__':main()
