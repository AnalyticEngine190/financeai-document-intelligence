"""Separate Day10.5 reproducibility snapshot, without touching Day10.4 freeze.

Run after installing the patch and before the post-fix regression:
  python -m scripts.day10_5_freeze --freeze
  python -m scripts.day10_5_freeze --check
"""
from __future__ import annotations
import argparse, json, zipfile
from datetime import datetime,timezone
from scripts.day10_4_freeze import (
    ROOT, URL, digest, tracked_files, document_fingerprint,
    _requirements, _git_revision, _safe_runtime,
)
from scripts.evaluate_day10_4 import DEFAULT_DATASET,validate_dataset

DIR=ROOT/'reports'/'evaluation'/'day10_5_freeze'
MANIFEST=DIR/'manifest.json'
FIRST=ROOT/'reports'/'evaluation'/'day10_4_first_run'


def _first_run_hashes():
    return {name:digest(FIRST/name) for name in ('run_metadata.json','results.jsonl','summary.json')
            if (FIRST/name).is_file()}


def freeze(url=URL):
    if DIR.exists():raise RuntimeError('Day10.5 freeze already exists; do not overwrite it')
    data=validate_dataset(DEFAULT_DATASET)
    hashes=_first_run_hashes()
    if not {'run_metadata.json','results.jsonl'}.issubset(hashes):
        raise RuntimeError('Day10.4 sealed first-run files are missing')
    meta=json.loads((FIRST/'run_metadata.json').read_text(encoding='utf-8'))
    if meta.get('benchmark_sha256')!=digest(DEFAULT_DATASET):
        raise RuntimeError('Original Day10.4 gold differs from sealed run')
    required={doc['document_id'] for doc in data['documents'].values()}
    documents=document_fingerprint(url)
    if set(documents)!=required:
        raise RuntimeError('Indexed document IDs differ from the 3 benchmark reports')
    files=tracked_files()
    deps=_requirements()
    DIR.mkdir(parents=True,exist_ok=False)
    archive=DIR/'code_snapshot.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):z.write(ROOT/rel,rel)
    (DIR/'pip_freeze.txt').write_text(deps,encoding='utf-8')
    result={'freeze_version':'day10.5-postfix-v1',
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'files':files,'documents':documents,'runtime':_safe_runtime(),
            'git_revision':_git_revision(),
            'source_zip_sha256':digest(archive),
            'pip_freeze_sha256':digest(DIR/'pip_freeze.txt'),
            'sealed_day10_4_first_run_hashes':hashes,
            'limitations':'Chroma vectors and Ollama weights not copied; keep local index and model unchanged.'}
    with MANIFEST.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
    print('DAY 10.5 FROZEN:',len(files),'source/config files,',len(documents),'reports')
    print('Manifest:',MANIFEST)


def check(url=URL):
    if not MANIFEST.is_file():raise RuntimeError('Day10.5 freeze missing')
    saved=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if saved['files']!=tracked_files():raise RuntimeError('Source files changed after Day10.5 freeze')
    if saved['documents']!=document_fingerprint(url):raise RuntimeError('Indexed report metadata changed')
    if saved['runtime']!=_safe_runtime():raise RuntimeError('Runtime/model configuration changed')
    if saved['sealed_day10_4_first_run_hashes']!=_first_run_hashes():
        raise RuntimeError('Sealed Day10.4 first-run files changed')
    if saved['source_zip_sha256']!=digest(DIR/'code_snapshot.zip'):
        raise RuntimeError('Code snapshot changed')
    if saved['pip_freeze_sha256']!=digest(DIR/'pip_freeze.txt') or _requirements().splitlines()!=(DIR/'pip_freeze.txt').read_text(encoding='utf-8').splitlines():
        raise RuntimeError('Python dependencies changed')
    print('DAY 10.5 FREEZE CHECK PASSED:',len(saved['files']),'files unchanged')
    return saved

if __name__=='__main__':
    p=argparse.ArgumentParser()
    m=p.add_mutually_exclusive_group(required=True)
    m.add_argument('--freeze',action='store_true');m.add_argument('--check',action='store_true')
    p.add_argument('--api-url',default=URL)
    a=p.parse_args()
    try:freeze(a.api_url) if a.freeze else check(a.api_url)
    except Exception as exc:p.exit(2,f'ERROR: {exc}\n')
