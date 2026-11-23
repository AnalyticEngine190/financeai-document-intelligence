"""Read-only diagnostic breakdown of the preserved Day 11 first run."""
import json,statistics
from collections import Counter,defaultdict
from scripts.day11_12_common import FIRST,safe_reasons,load

def analyze(folder=FIRST):
    rows=[json.loads(x) for x in (folder/'results.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    if len(rows)!=50:raise ValueError('Incomplete first run; do not claim a 50-question score')
    by=defaultdict(Counter)
    reasons=Counter()
    for r in rows:
        by[r['category']][r['status']]+=1
        if r['status']=='FAIL':reasons[safe_reasons(r)]+=1
    lat=sorted(r['latency_seconds'] for r in rows)
    rank=lambda q:lat[max(0,min(len(lat)-1,round((len(lat)-1)*q)))]
    lines=['# Day 11 unseen first-run postmortem','',
           '**These are preserved first-run outputs; no development tuning was applied before this run.**','',
           f'Automatic PASS: {sum(r["status"]=="PASS" for r in rows)}/50',
           f'FAIL: {sum(r["status"]=="FAIL" for r in rows)}; narrative REVIEW: {sum(r["status"]=="REVIEW" for r in rows)}','',
           '## By category','', '| Category | PASS | FAIL | REVIEW |','|---|---:|---:|---:|']
    for cat,counts in sorted(by.items()):lines.append(f'| {cat} | {counts["PASS"]} | {counts["FAIL"]} | {counts["REVIEW"]} |')
    lines+=['','## Observed failure classes (not claimed internal root causes)','']
    lines += [f'- {key}: {num}' for key,num in sorted(reasons.items())]
    lines += ['','## Latency',f'- p50: {rank(.5):.3f}s; p95: {rank(.95):.3f}s; maximum {max(lat):.3f}s','',
              '## Failed and pending cases','']
    for r in rows:
        if r['status']=='PASS':continue
        lines += [f'### {r["id"]} — {r["status"]}',r['question'],
                  'Reason: '+str(r['failure_code']),
                  'Answer: '+str(r['response'].get('answer',''))[:800],
                  'Citations: '+str(r['response'].get('sources',[]))[:800],'']
    out=folder/'analysis.md'
    if out.exists():raise RuntimeError('Analysis already exists: preserve previous report, do not overwrite it')
    out.write_text('\n'.join(lines),encoding='utf-8')
    print('Wrote:',out)
if __name__=='__main__':analyze()
