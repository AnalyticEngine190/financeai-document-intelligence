"""Independent, append-only Day 11.3 development snapshot.

Preserves the original Day 11 14/50 independent run, all earlier snapshots,
Day 11.1 45/4/1, and Day 11.2 targeted 1/1/2 outcomes. Does not edit any
of their files or the sealed gold. Source changes are expected after patching.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from pathlib import Path
import zipfile

from scripts.day11_12_common import ROOT, SEALED, API_URL, load, digest, write_once
from scripts.freeze_day11_12 import packages
from scripts.freeze_day11_2 import state as previous_state

OUT=ROOT/'reports/evaluation/day11_3_freeze'
MANIFEST=OUT/'manifest.json'
PREVIOUS=ROOT/'reports/evaluation/day11_2_freeze/manifest.json'
PREVIOUS_RUNS=ROOT/'reports/evaluation/day11_2_regressions'


def _preserved_snapshot() -> dict[str,str]:
    if not PREVIOUS.is_file():
        raise RuntimeError('Missing Day 11.2 frozen manifest')
    old=load(PREVIOUS)
    snapshot=PREVIOUS.parent/'code_snapshot.zip'
    dependencies=PREVIOUS.parent/'pip_freeze.txt'
    for p,key in ((snapshot,'snapshot_sha256'),(dependencies,'pip_freeze_sha256')):
        if not p.is_file() or digest(p)!=old[key]:
            raise RuntimeError('Original Day 11.2 frozen artifact changed: '+str(p))
    if digest(SEALED)!=old['gold_sha256']:
        raise RuntimeError('Sealed Day 11 gold has changed')
    return {str(p.relative_to(ROOT)):digest(p) for p in (PREVIOUS,snapshot,dependencies)}


def _preserved_regression() -> dict[str,str]:
    """Pin a real recorded Day 11.2 targeted run, never manufacture one."""
    if not PREVIOUS_RUNS.is_dir():
        raise RuntimeError('Cannot locate Day 11.2 regression archive')
    for folder in sorted(PREVIOUS_RUNS.iterdir()):
        files=[folder/n for n in ('run_metadata.json','results.jsonl','summary.json')]
        if not all(f.is_file() for f in files):continue
        try:
            summary=load(files[2]); meta=load(files[0])
            if summary.get('counts')!={'PASS':1,'FAIL':1,'REVIEW':2}:continue
            if meta.get('gold_sha256')!=digest(SEALED):continue
            if meta.get('freeze_sha256')!=digest(PREVIOUS):continue
            if set(meta.get('question_ids',[]))!={'D11_12','D11_45','D11_46','D11_47'}:continue
            return {str(f.relative_to(ROOT)):digest(f) for f in files}
        except (OSError,ValueError,KeyError,TypeError):continue
    raise RuntimeError('The original Day 11.2 1 PASS / 1 FAIL / 2 REVIEW targeted run is missing or changed')


def state(api_url=API_URL):
    base=previous_state(api_url)
    old=load(PREVIOUS)
    if base['documents']!=old['documents']:
        raise RuntimeError('Report registry/index changed from Day 11.2')
    for key in ('gold_sha256','audit_sha256','source_manifest_sha256'):
        if base[key]!=old[key]:
            raise RuntimeError('Preserved gold/source changed: '+key)
    base.update({'previous_day11_2_freeze':_preserved_snapshot(),
                 'previous_day11_2_regression':_preserved_regression()})
    return base


def freeze(api_url=API_URL):
    if OUT.exists():raise RuntimeError('Day 11.3 freeze already exists; refusing to overwrite')
    data=state(api_url)
    OUT.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(OUT/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(data['files']):z.write(ROOT/rel,rel)
    (OUT/'pip_freeze.txt').write_text(packages(),encoding='utf8')
    data.update({'version':'day11.3-development-after-independent',
                 'created_utc':datetime.now(timezone.utc).isoformat(),
                 'snapshot_sha256':digest(OUT/'code_snapshot.zip'),
                 'pip_freeze_sha256':digest(OUT/'pip_freeze.txt')})
    write_once(MANIFEST,data)
    print('DAY 11.3 FROZEN:',len(data['files']),'files;',len(data['documents']),'reports')


def check(api_url=API_URL):
    if not MANIFEST.is_file():
        raise RuntimeError('Day 11.3 is not frozen; run --freeze before regression')
    frozen=load(MANIFEST);now=state(api_url)
    for key in now:
        if frozen.get(key)!=now[key]:raise RuntimeError('Day 11.3 freeze violation: '+key)
    if digest(OUT/'code_snapshot.zip')!=frozen['snapshot_sha256']:
        raise RuntimeError('Day 11.3 snapshot modified')
    if digest(OUT/'pip_freeze.txt')!=frozen['pip_freeze_sha256']:
        raise RuntimeError('Day 11.3 dependencies modified')
    if packages().splitlines()!=(OUT/'pip_freeze.txt').read_text(encoding='utf8').splitlines():
        raise RuntimeError('Installed package dependencies changed')
    print('DAY 11.3 FREEZE CHECK PASSED:',len(now['files']),'files;',len(now['documents']),'reports')
    return frozen


def main():
    p=argparse.ArgumentParser(description=__doc__)
    m=p.add_mutually_exclusive_group(required=True)
    m.add_argument('--freeze',action='store_true')
    m.add_argument('--check',action='store_true')
    p.add_argument('--api-url',default=API_URL)
    args=p.parse_args()
    try:
        freeze(args.api_url) if args.freeze else check(args.api_url)
    except Exception as e:p.exit(2,'ERROR: '+str(e)+'\n')

if __name__=='__main__':main()
