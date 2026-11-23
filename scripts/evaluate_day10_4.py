"""Sealed Day 10.4 evaluation of the EXISTING /ask_v2 API.

- Never sends gold values, document answers, or references to the server.
- Requires a matching code/report freeze before a first run.
- Preserves every result immediately; --resume continues interrupted first run.
- Provides strict numerical/source/unit grading and explicit human review of narratives.
- Does NOT patch or tune the application based on the evaluation set.

Run from project root, after freezing: python -m scripts.evaluate_day10_4 --first-run
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

from scripts.day10_4_freeze import ROOT, URL, digest, verify_freeze

DEFAULT_DATASET = ROOT/'data'/'evaluation'/'day10_4_40.json'
OUT = ROOT/'reports'/'evaluation'/'day10_4_first_run'
RESULTS = OUT/'results.jsonl'
CATEGORIES = {'nordea_q2': 10, 'new_bank': 12, 'isolation': 8,
              'cross_document': 6, 'unsupported_ambiguous': 4}


def validate_dataset(path: Path = DEFAULT_DATASET) -> dict:
    obj=json.loads(path.read_text(encoding='utf-8'))
    if obj.get('dataset_version')!='day10.4-independent-v1':
        raise ValueError('Unknown benchmark version')
    docs=obj.get('documents',{})
    if len(docs)!=3:
        raise ValueError('Exactly three document records required')
    cases=obj.get('cases')
    if not isinstance(cases,list) or len(cases)!=40:
        raise ValueError('Exactly 40 questions required')
    if Counter(x.get('category') for x in cases)!=CATEGORIES:
        raise ValueError('Category count changed since gold preparation')
    docids={d['document_id'] for d in docs.values()}
    seen=set()
    for c in cases:
        cid=c.get('id')
        if not isinstance(cid,str) or not cid or cid in seen:
            raise ValueError(f'Duplicate/blank case id: {cid}')
        seen.add(cid)
        if not isinstance(c.get('question'),str) or not c['question'].strip():
            raise ValueError(f'{cid}: blank question')
        o=c.get('expected_outcome')
        if o not in ('answer','abstain','clarify','explanation'):
            raise ValueError(f'{cid}: invalid expected outcome')
        choice=c.get('document_selection') or {}
        mode=choice.get('mode'); ids=choice.get('document_ids')
        if mode not in ('auto','manual','compare') or not isinstance(ids,list):
            raise ValueError(f'{cid}: invalid document selection')
        if (mode=='auto' and ids) or (mode=='manual' and len(ids)!=1) or (mode=='compare' and len(ids)!=2):
            raise ValueError(f'{cid}: invalid document selection IDs')
        if any(d not in docids for d in ids):
            raise ValueError(f'{cid}: selected unknown document')
        if o=='answer':
            try:Decimal(str(c['expected_value']))
            except (InvalidOperation,KeyError):raise ValueError(f'{cid}: invalid expected number')
            if not c.get('expected_sources') or not c.get('expected_unit') or not c.get('evidence'):
                raise ValueError(f'{cid}: incomplete source/value reference')
        if o=='explanation':
            if not c.get('human_review_criteria') or not c.get('expected_sources'):
                raise ValueError(f'{cid}: narrative requires human gold rubric and source')
        if o in ('clarify','abstain') and c.get('expected_sources'):
            raise ValueError(f'{cid}: unsupported case must not expect a citation')
        for s in c.get('expected_sources',[]):
            if s.get('document_id') not in docids or not isinstance(s.get('page'),int) or s['page']<1:
                raise ValueError(f'{cid}: invalid reference document/page')
    return obj


def first_number(text: str) -> Decimal | None:
    """Strict primary output number. Do not accidentally grade a later operand."""
    match=re.search(r'(?<![\w])[-+]?(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.\d+)?(?![\w])',str(text)[:140])
    if not match:return None
    try:return Decimal(match.group().replace(',','').replace(' ',''))
    except InvalidOperation:return None


def citation_grade(case:dict, response:dict) -> tuple[bool,str]:
    sources=response.get('sources') or []
    if not isinstance(sources,list):return False,'invalid_citation_format'
    seen=set()
    for s in sources:
        if not isinstance(s,dict) or not s.get('document_id') or not isinstance(s.get('page'),int):
            return False,'invalid_citation_format'
        seen.add((s['document_id'],s['page']))
    allowed={}
    for s in case['expected_sources']:
        allowed.setdefault(s['document_id'],set()).add(s['page'])
    for doc,pgs in (case.get('acceptable_source_pages') or {}).items():
        allowed.setdefault(doc,set()).update(int(p) for p in pgs)
    for doc,p in seen:
        if doc not in allowed:
            return False,'wrong_source_document'
        if p not in allowed[doc]:
            return False,'unexpected_source_page'
    if any(not any((d,p) in seen for p in pages) for d,pages in allowed.items()):
        return False,'missing_required_report_citation'
    return True,''


def unit_grade(case:dict, answer:str) -> tuple[bool,str]:
    unit=case.get('expected_unit')
    head=str(answer)[:140].casefold()
    if unit=='percent' and '%' not in head and 'per cent' not in head:
        return False,'missing_percentage_unit'
    if unit=='percentage_points' and 'percentage point' not in head:
        return False,'missing_percentage_points_unit'
    if unit=='basis_points' and not (re.search(r'\b(basis points?|bp|bps)\b',head)):
        return False,'missing_basis_points_unit'
    if unit=='ratio' and '%' in head:
        return False,'ratio_requested_as_decimal_but_percent_supplied'
    m=re.fullmatch(r'([A-Z]{3})(m|bn)',str(unit))
    if m:
        if m[1].casefold() not in head:
            return False,'missing_currency_code'
        if m[2]=='m' and not re.search(r'\bmillion\b|\b'+m[1].casefold()+r'm\b',head):
            return False,'missing_million_unit'
        if m[2]=='bn' and not re.search(r'\bbillion\b|\b'+m[1].casefold()+r'bn\b',head):
            return False,'missing_billion_unit'
    return True,''


def grade(case:dict,response:dict) -> tuple[str,str]:
    """PASS/FAIL/REVIEW. Explanation content requires independent human judgment."""
    status=case['expected_outcome']
    supported=response.get('supported')
    needs=bool(response.get('needs_clarification'))
    cited=response.get('sources') or []
    answer=str(response.get('answer',''))
    if status=='clarify':
        if supported is False and needs and not cited:
            return 'PASS',''
        return 'FAIL','expected_clarification'
    if status=='abstain':
        if supported is not False or needs or cited:
            return 'FAIL','expected_clean_abstention'
        if case.get('expected_reason')=='currency_incompatible':
            low=answer.casefold()
            if not (('sek' in low and 'eur' in low) or 'exchange rate' in low or 'currency' in low):
                return 'FAIL','missing_currency_incompatibility_explanation'
        return 'PASS',''
    if supported is not True or needs:
        return 'FAIL','answer_not_supported'
    ok,why=citation_grade(case,response)
    if not ok:return 'FAIL',why
    if status=='explanation':
        if len(answer.split())<8:
            return 'FAIL','explanation_too_short'
        return 'REVIEW','human_explanation_review_required'
    number=first_number(answer)
    expected=Decimal(str(case['expected_value']))
    if number!=expected:
        return 'FAIL', 'wrong_primary_number' if number is not None else 'missing_primary_number'
    unit_ok,unit_issue=unit_grade(case,answer)
    if not unit_ok:
        return 'FAIL',unit_issue
    return 'PASS',''


def post_json(url:str,payload:dict) -> dict:
    request=urllib.request.Request(url.rstrip('/')+'/ask_v2',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=180) as resp:
        return json.load(resp)


def _load_existing() -> list[dict]:
    if not RESULTS.is_file():return []
    out=[]
    for row in RESULTS.read_text(encoding='utf-8').splitlines():
        if row.strip():out.append(json.loads(row))
    return out


def run_first(api_url:str, resume:bool=False):
    dataset=validate_dataset(DEFAULT_DATASET)
    freeze=verify_freeze(api_url)
    if freeze['files'].get('data/evaluation/day10_4_40.json')!=digest(DEFAULT_DATASET):
        raise RuntimeError('Benchmark gold JSON differs from frozen data')
    if resume:
        if not OUT.exists() or not (OUT/'run_metadata.json').is_file():
            raise RuntimeError('No interrupted first run found to resume')
    else:
        OUT.mkdir(parents=True,exist_ok=False)
        metadata={'started_utc':datetime.now(timezone.utc).isoformat(),
                  'benchmark_sha256':digest(DEFAULT_DATASET),
                  'freeze_manifest_sha256':digest(ROOT/'reports'/'evaluation'/'day10_4_freeze'/'manifest.json'),
                  'api_url':api_url,'mode':'first_run','question_count':40}
        with (OUT/'run_metadata.json').open('x',encoding='utf-8') as f:
            json.dump(metadata,f,indent=2);f.write('\n')
    meta=json.loads((OUT/'run_metadata.json').read_text(encoding='utf-8'))
    if meta['benchmark_sha256']!=digest(DEFAULT_DATASET):
        raise RuntimeError('Dataset hash changed after first-run initialization')
    if meta['freeze_manifest_sha256']!=digest(ROOT/'reports'/'evaluation'/'day10_4_freeze'/'manifest.json'):
        raise RuntimeError('Freeze manifest changed after first-run initialization')
    existing=_load_existing()
    ids=[r['id'] for r in existing]
    if ids != [c['id'] for c in dataset['cases'][:len(ids)]]:
        raise RuntimeError('Saved first-run results are out of sequence. Do not overwrite them.')
    if len(existing)==40:
        raise RuntimeError('First run already completed. Do not overwrite its records.')
    print('SEALED FIRST RUN:', len(existing),'previous responses preserved,',40-len(existing),'remaining')
    for i,c in enumerate(dataset['cases'][len(existing):], start=len(existing)+1):
        payload={'question':c['question'],'document_selection':c['document_selection']}
        started=time.monotonic()
        try:
            result=post_json(api_url,payload)
            status,issue=grade(c,result)
        except Exception as exc:
            result={'supported':False,'answer':'','sources':[],
                    'transport_exception':type(exc).__name__,'detail':str(exc)}
            status,issue='FAIL','transport_or_runtime_error'
        record={'id':c['id'],'category':c['category'],'question':c['question'],
                'selection':c['document_selection'],'expected_outcome':c['expected_outcome'],
                'expected_value':c.get('expected_value'),'expected_unit':c.get('expected_unit'),
                'expected_sources':c['expected_sources'], 'response':result,
                'status':status,'failure_code':issue,
                'latency_seconds':round(time.monotonic()-started,3),
                'observed_utc':datetime.now(timezone.utc).isoformat()}
        with RESULTS.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
            f.flush()
        existing.append(record)
        print(f'[{i:02d}/40] {c["id"]:<24} {status:<6} {issue}',flush=True)
    counts=Counter(r['status'] for r in existing)
    failures=Counter(r['failure_code'] for r in existing if r['status']=='FAIL')
    by_category=defaultdict(Counter)
    for r in existing:by_category[r['category']][r['status']]+=1
    summary={'summary':{'total':len(existing),'auto_passed':counts['PASS'],
                        'failed':counts['FAIL'],'human_review_pending':counts['REVIEW'],
                        'score_note':'Human-review cases are not counted as passes until independently assessed',
                        'failure_codes':dict(failures),'categories':{k:dict(v) for k,v in by_category.items()}},
             'results':existing}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    review=[r for r in existing if r['status']=='REVIEW']
    if review:
        lines=['# Day 10.4 human review (two narrative cases)',
               '', 'Do not modify the frozen benchmark or initial results. Judge the actual answer against',
               'the PDF source and listed factual requirements, then record PASS/FAIL in a separate review file.','']
        references={c['id']:c for c in dataset['cases']}
        for r in review:
            gold=references[r['id']]
            lines.extend([f'## {r["id"]}', f'Question: {r["question"]}',
                'Answer returned: '+str(r['response'].get('answer','')),
                'Required concepts: '+ ' | '.join(gold['human_review_criteria']),
                f'Expected PDF citation: {gold["expected_sources"]}',
                'Human decision: PENDING', 'Reason: ', ''])
        (OUT/'narrative_review.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\nFIRST-RUN RESULTS PRESERVED:',OUT)
    print(f'Auto PASS {counts["PASS"]}, FAIL {counts["FAIL"]}, REVIEW {counts["REVIEW"]}, total {len(existing)}')
    print('Do not modify the gold, source code, model or PDF index before archiving the result.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--validate-only',action='store_true')
    mode.add_argument('--first-run',action='store_true')
    mode.add_argument('--resume',action='store_true')
    p.add_argument('--api-url',default=URL)
    args=p.parse_args()
    try:
        if args.validate_only:
            data=validate_dataset(); print('VALID:',len(data['cases']),'questions; no API calls; no score measured.')
        else:
            run_first(args.api_url,resume=args.resume)
    except Exception as exc:
        p.exit(2,f'ERROR: {exc}\n')
if __name__=='__main__':main()
