"""Append-only Day 11.2 freeze, preserving every earlier independent baseline.

Run after installation, before any Day 11.2 development regression. The new
snapshot is separate from Day 11.1; never overwrite an older frozen version.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import zipfile

from scripts.day11_12_common import (ROOT, SEALED, SOURCE, AUDIT, API_URL,
                                     digest, load, doc_fingerprint,
                                     validate_dataset, write_once)
from scripts.freeze_day11_12 import source_files, packages, runtime
from scripts.freeze_day11_1 import _baseline_hashes

OUT = ROOT / 'reports' / 'evaluation' / 'day11_2_freeze'
MANIFEST = OUT / 'manifest.json'
OLD_FREEZE = ROOT / 'reports' / 'evaluation' / 'day11_12_freeze' / 'manifest.json'
PREVIOUS = ROOT / 'reports' / 'evaluation' / 'day11_1_freeze' / 'manifest.json'
OLD_REGRESSIONS = ROOT / 'reports' / 'evaluation' / 'day11_1_regressions'


def _previous_hashes() -> dict[str,str]:
    if not PREVIOUS.is_file():
        raise RuntimeError('Missing preserved Day 11.1 freeze manifest')
    old=load(PREVIOUS)
    snapshot=PREVIOUS.parent/'code_snapshot.zip'
    dependencies=PREVIOUS.parent/'pip_freeze.txt'
    if not snapshot.is_file() or not dependencies.is_file():
        raise RuntimeError('Day 11.1 frozen code/dependency snapshots missing')
    if digest(snapshot)!=old['snapshot_sha256'] or digest(dependencies)!=old['pip_freeze_sha256']:
        raise RuntimeError('Preserved Day 11.1 freeze snapshot has changed')
    if digest(SEALED)!=old['gold_sha256']:
        raise RuntimeError('The sealed gold changed after Day 11.1 freeze')
    return {str(p.relative_to(ROOT)):digest(p)
            for p in (PREVIOUS,snapshot,dependencies)}


def _development_reference() -> dict[str,str]:
    """Preserve a recorded 45 PASS / 4 FAIL / 1 REVIEW Day 11.1 run."""
    if not OLD_REGRESSIONS.is_dir():
        raise RuntimeError('Missing archived Day 11.1 regression runs')
    for run in sorted(OLD_REGRESSIONS.iterdir()):
        required=[run/f for f in ('summary.json','results.jsonl','run_metadata.json')]
        if not all(p.is_file() for p in required):continue
        try:
            summary=load(required[0]); meta=load(required[2])
            if summary.get('counts') != {'PASS':45,'FAIL':4,'REVIEW':1}:continue
            if meta.get('gold_sha256')!=digest(SEALED):continue
            if meta.get('freeze_sha256')!=digest(PREVIOUS):continue
        except (OSError,ValueError,KeyError,TypeError):continue
        return {str(p.relative_to(ROOT)):digest(p) for p in required}
    raise RuntimeError('Cannot find preserved 45/4/1 Day 11.1 reference regression')


def state(api_url=API_URL):
    validate_dataset()
    original=load(OLD_FREEZE)
    prior=load(PREVIOUS)
    docs=doc_fingerprint(api_url)
    if docs!=original['documents'] or docs!=prior['documents']:
        raise RuntimeError('Four-report registry/index fingerprint changed')
    for key,path in [('gold_sha256',SEALED),('audit_sha256',AUDIT),('source_manifest_sha256',SOURCE)]:
        if digest(path)!=original[key] or digest(path)!=prior[key]:
            raise RuntimeError('Preserved evaluation source changed: '+key)
    return {'files':source_files(), 'documents':docs,
            'gold_sha256':digest(SEALED),'audit_sha256':digest(AUDIT),
            'source_manifest_sha256':digest(SOURCE),'runtime':runtime(),
            'independent_baseline':_baseline_hashes(),
            'previous_freeze':_previous_hashes(),
            'previous_regression':_development_reference()}


def freeze(api_url=API_URL):
    if OUT.exists():raise RuntimeError('Day 11.2 freeze already exists; refusing to overwrite')
    data=state(api_url)
    OUT.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(OUT/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for rel in data['files']:archive.write(ROOT/rel,rel)
    (OUT/'pip_freeze.txt').write_text(packages(),encoding='utf-8')
    data.update({'version':'day11.2-development-after-independent',
                 'created_utc':datetime.now(timezone.utc).isoformat(),
                 'snapshot_sha256':digest(OUT/'code_snapshot.zip'),
                 'pip_freeze_sha256':digest(OUT/'pip_freeze.txt')})
    write_once(MANIFEST,data)
    print('DAY 11.2 FROZEN:',len(data['files']),'files;',len(data['documents']),'reports')


def check(api_url=API_URL):
    if not MANIFEST.is_file():raise RuntimeError('Missing Day 11.2 freeze; run --freeze first')
    prior=load(MANIFEST); now=state(api_url)
    for key in now:
        if now[key]!=prior[key]:raise RuntimeError('Day 11.2 freeze violation: '+key)
    if digest(OUT/'code_snapshot.zip')!=prior['snapshot_sha256']:
        raise RuntimeError('Day 11.2 source snapshot altered')
    if digest(OUT/'pip_freeze.txt')!=prior['pip_freeze_sha256']:
        raise RuntimeError('Day 11.2 dependency snapshot altered')
    if packages().splitlines()!=(OUT/'pip_freeze.txt').read_text(encoding='utf-8').splitlines():
        raise RuntimeError('Installed dependencies changed')
    print('DAY 11.2 FREEZE CHECK PASSED:',len(now['files']),'files;',len(now['documents']),'reports')
    return prior


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--check',action='store_true')
    parser.add_argument('--api-url',default=API_URL)
    args=parser.parse_args()
    try:
        freeze(args.api_url) if args.freeze else check(args.api_url)
    except Exception as exc:
        parser.exit(2,'ERROR: '+str(exc)+'\n')

if __name__=='__main__':main()
