"""Freeze/check local Day 10.4 code, selected configuration and indexed-report metadata.

Usage from the Finance AI Building project root:
    python -m scripts.day10_4_freeze --freeze
    python -m scripts.day10_4_freeze --check

Does not change your application, its ChromaDB collection, or Ollama settings.
Never includes .env files, API keys, uploaded PDFs, or private customer data in the snapshot.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'reports' / 'evaluation' / 'day10_4_freeze'
MANIFEST = DIR / 'manifest.json'
DATASET = ROOT / 'data' / 'evaluation' / 'day10_4_40.json'
URL = os.environ.get('FINANCE_AI_API_URL', 'http://127.0.0.1:8000').rstrip('/')


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def tracked_files() -> dict[str, str]:
    """Snapshot source/config without copying credentials, private PDFs, or indexes."""
    paths = []
    for folder in ('app', 'scripts', 'tests'):
        root = ROOT / folder
        if root.exists():
            paths.extend(root.rglob('*.py'))
    paths.extend(ROOT.glob('*.py'))
    paths.extend(ROOT.glob('requirements*.txt'))
    for filename in ('pyproject.toml', 'poetry.lock', 'Pipfile.lock', 'uv.lock'):
        if (ROOT / filename).is_file():
            paths.append(ROOT / filename)
    paths.extend((ROOT / 'data' / 'evaluation').glob('*.json'))
    # Avoid symlink escape and arbitrary large/non-source files.
    result = {}
    for file in sorted(set(paths)):
        if file.is_file() and not file.is_symlink():
            result[file.relative_to(ROOT).as_posix()] = digest(file)
    return result


def _request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=12) as res:
        return json.load(res)


def get_docs(api_url: str = URL) -> list[dict]:
    records = _request_json(api_url.rstrip('/') + '/documents').get('documents')
    if not isinstance(records, list):
        raise RuntimeError('The Day 10 /documents endpoint did not return a document list.')
    return records


def document_fingerprint(api_url: str = URL) -> dict:
    """Track identity, reporting edition and indexed-chunk counts, not file paths."""
    docs = get_docs(api_url)
    return {str(d['document_id']): {
        'issuer': d.get('issuer'), 'report_year': d.get('report_year'),
        'report_quarter': d.get('report_quarter'), 'currency': d.get('currency'),
        'chunk_count': d.get('chunk_count'), 'report_type': d.get('report_type')
    } for d in docs}


def _requirements() -> str:
    out = subprocess.run([sys.executable, '-m', 'pip', 'freeze'],
                         capture_output=True, text=True, timeout=45, check=True)
    return out.stdout


def _git_revision() -> str | None:
    try:
        p = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                           text=True, timeout=5)
        return p.stdout.strip() or None if p.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _safe_runtime() -> dict:
    return {
        'python': sys.version, 'platform': platform.platform(),
        'ollama_model': os.getenv('OLLAMA_MODEL', 'qwen3:4b'),
        'ollama_host': 'local' if 'localhost' in os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')
                    or '127.0.0.1' in os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') else 'nonlocal',
        'finance_ai_debug': os.getenv('FINANCE_AI_DEBUG', 'unset'),
    }


def _check_documents(documents: dict, dataset: dict) -> None:
    required = {d['document_id']: (d['issuer'], d['edition']) for d in dataset['documents'].values()}
    missing = set(required) - set(documents)
    unexpected = set(documents) - set(required)
    if missing or unexpected:
        raise RuntimeError(f'Expected exactly the 3 benchmark PDFs. Missing: {sorted(missing)}; extra: {sorted(unexpected)}. Do not change the indexed corpus during the first run.')
    for key, d in dataset['documents'].items():
        idx = documents[d['document_id']]
        if str(idx['issuer']).casefold() != d['issuer'].casefold() or idx['report_year'] != int(d['edition'][-4:]) or idx['report_quarter'] != int(d['edition'][1]):
            raise RuntimeError(f'Indexed document metadata does not match {key}: {idx}')
        if not isinstance(idx['chunk_count'], int) or idx['chunk_count'] <= 0:
            raise RuntimeError(f'Indexed document has no chunks: {key}')


def _previous_evaluation_artifacts() -> list[dict]:
    folder=ROOT/'reports'/'evaluation'
    found=sorted(folder.glob('nordea_eval_*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:8]
    return [{'name':p.name,'sha256':digest(p)} for p in found if p.is_file()]

def make_freeze(api_url: str = URL) -> None:
    if DIR.exists():
        raise RuntimeError(f'Freeze directory already exists: {DIR}. Preserve the first frozen version.')
    dataset = json.loads(DATASET.read_text(encoding='utf-8'))
    documents = document_fingerprint(api_url)
    _check_documents(documents, dataset)
    files = tracked_files()
    if 'data/evaluation/day10_4_40.json' not in files:
        raise RuntimeError('The independent benchmark is missing from the source freeze')
    deps = _requirements()
    DIR.mkdir(parents=True, exist_ok=False)
    snapshot = DIR / 'code_snapshot.zip'
    with zipfile.ZipFile(snapshot, 'w', zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(files):
            archive.write(ROOT / rel, rel)
    (DIR / 'pip_freeze.txt').write_text(deps, encoding='utf-8')
    manifest = {
        'freeze_version': 1,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'files': files, 'documents': documents,
        'pip_freeze_sha256': digest(DIR / 'pip_freeze.txt'),
        'code_snapshot_sha256': digest(snapshot),
        'git_revision': _git_revision(), 'runtime': _safe_runtime(),
        'previous_evaluation_artifacts': _previous_evaluation_artifacts(),
        'limits': 'Source snapshot and Chroma document identity/chunk counts captured. Full vector DB state, embedding weights and model tensor weights are NOT snapshotted. Preserve Ollama model and index during the evaluation.'
    }
    with MANIFEST.open('x', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    print('FROZEN:',len(files),'source/config files; 3 indexed reports')
    print('Manifest:', MANIFEST)
    print('Snapshot:', snapshot)


def verify_freeze(api_url: str = URL) -> dict:
    if not MANIFEST.exists():
        raise RuntimeError('No Day 10.4 freeze found. Run python -m scripts.day10_4_freeze --freeze first.')
    saved = json.loads(MANIFEST.read_text(encoding='utf-8'))
    current = tracked_files()
    if saved['files'] != current:
        old,new=saved['files'],current
        missing=sorted(set(old)-set(new));added=sorted(set(new)-set(old))
        changed=sorted(k for k in set(old)&set(new) if old[k]!=new[k])
        raise RuntimeError(f'FREEZE VIOLATION. Added: {added}; missing: {missing}; modified: {changed}. Restore the frozen version before the first run.')
    if document_fingerprint(api_url) != saved['documents']:
        raise RuntimeError('FREEZE VIOLATION: Indexed reports/metadata/chunk counts changed.')
    if _safe_runtime() != saved['runtime']:
        raise RuntimeError('FREEZE VIOLATION: Runtime configuration changed.')
    if _requirements().splitlines()!= (DIR/'pip_freeze.txt').read_text(encoding='utf-8').splitlines():
        raise RuntimeError('FREEZE VIOLATION: Installed Python dependency versions changed.')
    if digest(DIR/'code_snapshot.zip') != saved['code_snapshot_sha256']:
        raise RuntimeError('Source snapshot is not intact.')
    if digest(DIR/'pip_freeze.txt') != saved['pip_freeze_sha256']:
        raise RuntimeError('Dependency manifest is not intact.')
    print('FREEZE CHECK PASSED:', len(current), 'files unchanged; report registry unchanged.')
    return saved


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--freeze',action='store_true')
    mode.add_argument('--check',action='store_true')
    p.add_argument('--api-url',default=URL)
    args=p.parse_args()
    try:
        if args.freeze: make_freeze(args.api_url)
        else: verify_freeze(args.api_url)
    except Exception as exc:
        p.exit(2,f'ERROR: {exc}\n')
if __name__ == '__main__': main()
