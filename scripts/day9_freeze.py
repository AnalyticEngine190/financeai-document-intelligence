"""Create/check a simple SHA-256 manifest of the Financial AI evaluation code.

Usage (from project root):
    python -m scripts.day9_freeze --freeze
    python -m scripts.day9_freeze --check

Pure standard library; no changes to app.py or evaluator.
This DOES NOT snapshot your external ChromaDB or Ollama model.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports' / 'evaluation' / 'day9_freeze_manifest.json'


def targets():
    candidates = []
    for folder in ('app', 'scripts', 'tests'):
        p = ROOT / folder
        if p.is_dir():
            candidates += list(p.rglob('*.py'))
    candidates += list(ROOT.glob('*.py'))
    candidates += list(ROOT.glob('requirements*.txt'))
    for name in ('pyproject.toml', 'poetry.lock', 'Pipfile.lock', 'uv.lock'):
        if (ROOT / name).is_file():
            candidates.append(ROOT / name)
    candidates += list((ROOT/'data'/'processed').rglob('*.json')) if (ROOT/'data'/'processed').exists() else []
    # Freeze evaluation gold labels too: changing the key after seeing model outputs invalidates the baseline.
    candidates += list((ROOT/'data'/'evaluation').glob('*.json')) if (ROOT/'data'/'evaluation').exists() else []
    for path in (ROOT/'annual_report.pdf', ROOT/'data'/'raw'/'annual_report.pdf'):
        if path.is_file():
            candidates.append(path)
    # Only track code/data source, not the freshly produced evaluation reports.
    return sorted(set(p for p in candidates if p.is_file()), key=lambda p: p.as_posix())


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def hashes():
    return {p.relative_to(ROOT).as_posix():digest(p) for p in targets()}


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--freeze',action='store_true')
    a.add_argument('--check',action='store_true')
    args=a.parse_args()
    if args.freeze==args.check:
        a.error('Choose exactly one: --freeze or --check')
    if args.freeze:
        if OUT.exists():
            a.error(f'Manifest already exists: {OUT}. Do not overwrite a completed freeze.')
        data={'created_utc':datetime.now(timezone.utc).isoformat(),
              'python':platform.python_version(),
              'files':hashes(),
              'limitations':'Manifest covers local source and selected processed data. Does not hash external ChromaDB, Ollama weights, or non-JSON raw files. Do not change ingestion, DB, model or prompts while testing.'}
        OUT.parent.mkdir(parents=True,exist_ok=True)
        OUT.write_text(json.dumps(data,indent=2)+'\n',encoding='utf8')
        print('FROZEN:',len(data['files']),'files; manifest:',OUT)
        print('Python:',data['python'])
        return
    if not OUT.exists():
        a.error(f'No manifest: {OUT}; run --freeze first')
    stored=json.loads(OUT.read_text(encoding='utf8'))['files']
    now=hashes()
    added=sorted(now.keys()-stored.keys())
    missing=sorted(stored.keys()-now.keys())
    changed=sorted(k for k in stored.keys()&now.keys() if stored[k]!=now[k])
    if added or missing or changed:
        for kind,items in [('ADDED',added),('MISSING',missing),('CHANGED',changed)]:
            for path in items:
                print(kind,path)
        print('FREEZE CHECK FAILED: filesystem changed after baseline')
        sys.exit(1)
    print('FREEZE CHECK PASSED:',len(stored),'files unchanged')


if __name__=='__main__':
    main()
