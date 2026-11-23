"""Separate, append-only Day 11.1 post-fix freeze; never rewrites Day 11 baseline."""
from __future__ import annotations
import argparse, json, sys, zipfile
from pathlib import Path
from datetime import datetime, timezone
from scripts.day11_12_common import (ROOT, SEALED, SOURCE, AUDIT, API_URL,
                                      digest, load, doc_fingerprint, validate_dataset, write_once)
from scripts.freeze_day11_12 import source_files, packages, runtime

OUT = ROOT / 'reports' / 'evaluation' / 'day11_1_freeze'
MANIFEST = OUT / 'manifest.json'
BASELINE = ROOT / 'reports' / 'evaluation' / 'day11_first_valid_run'
ORIGINAL = ROOT / 'reports' / 'evaluation' / 'day11_first_run'
OLD_FREEZE = ROOT / 'reports' / 'evaluation' / 'day11_12_freeze' / 'manifest.json'


def _baseline_hashes():
    required=[OLD_FREEZE, BASELINE/'results.jsonl', BASELINE/'summary.json',
              BASELINE/'run_metadata.json', ORIGINAL/'results.jsonl']
    missing=[str(p) for p in required if not p.is_file()]
    if missing:raise RuntimeError('Missing preserved baseline artifacts: '+str(missing))
    result={str(p.relative_to(ROOT)):digest(p) for p in required}
    summary=load(BASELINE/'summary.json')
    if (summary.get('auto_passed'),summary.get('failed'))!=(14,36):
        raise RuntimeError('Preserved first valid result is not the recorded 14/50 baseline')
    return result


def state(api_url=API_URL):
    validate_dataset()
    old=load(OLD_FREEZE)
    current_docs=doc_fingerprint(api_url)
    if current_docs!=old['documents']:
        raise RuntimeError('Document registry/index differs from Day 11 initial freeze')
    if digest(SEALED)!=old['gold_sha256']:
        raise RuntimeError('Sealed benchmark changed')
    if digest(AUDIT)!=old['audit_sha256']:
        raise RuntimeError('Gold audit changed')
    if digest(SOURCE)!=old['source_manifest_sha256']:
        raise RuntimeError('Source metadata changed')
    return {'files':source_files(), 'documents':current_docs,
            'gold_sha256':digest(SEALED), 'audit_sha256':digest(AUDIT),
            'source_manifest_sha256':digest(SOURCE), 'runtime':runtime(),
            'baseline_hashes':_baseline_hashes()}


def freeze(api_url=API_URL):
    if OUT.exists():raise RuntimeError('Day 11.1 freeze already exists; never overwrite')
    s=state(api_url)
    OUT.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(OUT/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name in s['files']:z.write(ROOT/name,name)
    (OUT/'pip_freeze.txt').write_text(packages(),encoding='utf-8')
    s.update({'version':'day11.1-post-fix', 'created_utc':datetime.now(timezone.utc).isoformat(),
              'snapshot_sha256':digest(OUT/'code_snapshot.zip'),
              'pip_freeze_sha256':digest(OUT/'pip_freeze.txt'),
              'note':'Post-fix development regression; never claim new independent accuracy.'})
    write_once(MANIFEST,s)
    print('DAY 11.1 FROZEN:',len(s['files']),'files;',len(s['documents']),'reports')


def check(api_url=API_URL):
    if not MANIFEST.is_file():raise RuntimeError('Missing Day 11.1 freeze')
    old=load(MANIFEST);current=state(api_url)
    for name in current:
        if current[name]!=old[name]:raise RuntimeError('Day 11.1 freeze violation: '+name)
    if digest(OUT/'code_snapshot.zip')!=old['snapshot_sha256']:
        raise RuntimeError('Code snapshot was altered')
    if digest(OUT/'pip_freeze.txt')!=old['pip_freeze_sha256']:
        raise RuntimeError('Dependency snapshot was altered')
    if packages().splitlines()!=(OUT/'pip_freeze.txt').read_text(encoding='utf-8').splitlines():
        raise RuntimeError('Installed dependencies changed')
    print('DAY 11.1 FREEZE CHECK PASSED:',len(current['files']),'files;',len(current['documents']),'reports')
    return old


def main():
    ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--freeze',action='store_true');g.add_argument('--check',action='store_true')
    ap.add_argument('--api-url',default=API_URL);args=ap.parse_args()
    try:freeze(args.api_url) if args.freeze else check(args.api_url)
    except Exception as exc:ap.exit(2,'ERROR: '+str(exc)+'\n')
if __name__=='__main__':main()
