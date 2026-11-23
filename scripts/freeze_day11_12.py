"""Freeze Day 11/12 code, audited gold, dependency versions and four indexed reports.

The preserved Day 10.4 and Day 10.5.2 evaluation artifacts are NOT rewritten.
The raw Chroma vector data and local Ollama weights are not copied.
"""
from __future__ import annotations
import argparse,json,os,platform,subprocess,sys,zipfile
from datetime import datetime,timezone
from scripts.day11_12_common import ROOT,API_URL,SEALED,SOURCE,AUDIT,FREEZE,digest,load,write_once,doc_fingerprint,validate_dataset
MANIFEST=FREEZE/'manifest.json'
OLD_MANIFEST=ROOT/'reports/evaluation/day10_5_2_freeze/manifest.json'
OLD_FIRST=ROOT/'reports/evaluation/day10_4_first_run'


def source_files():
    paths=[]
    for folder in ('app','scripts','tests'):
        if (ROOT/folder).is_dir():paths.extend((ROOT/folder).rglob('*.py'))
    paths+=list(ROOT.glob('*.py'))+list(ROOT.glob('requirements*.txt'))
    for name in ('pyproject.toml','poetry.lock','uv.lock','Pipfile.lock'):
        if (ROOT/name).is_file():paths.append(ROOT/name)
    paths+=list((ROOT/'data/evaluation').glob('*.json'))
    return {p.relative_to(ROOT).as_posix():digest(p) for p in sorted(set(paths)) if p.is_file() and not p.is_symlink()}


def packages():
    r=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,timeout=70,check=True)
    return r.stdout


def runtime():
    from scripts.day10_4_freeze import _safe_runtime
    return _safe_runtime()


def baseline():
    if not OLD_MANIFEST.exists():raise RuntimeError('Missing preserved Day10.5.2 manifest')
    originals={p.name:digest(p) for p in (OLD_FIRST/'run_metadata.json',OLD_FIRST/'results.jsonl',OLD_FIRST/'summary.json') if p.exists()}
    if not {'run_metadata.json','results.jsonl'}.issubset(originals):
        raise RuntimeError('Original Day10.4 sealed evaluation files missing')
    return {'day10_5_2_manifest_sha256':digest(OLD_MANIFEST),'day10_4_first_run_hashes':originals}


def current(api_url):
    gold=validate_dataset()
    report=load(SOURCE)
    a=load(AUDIT)
    if a['sealed_sha256']!=digest(SEALED) or a['pdf_sha256']!=report['sha256']:
        raise RuntimeError('PDF/gold audit attestation mismatch')
    pdf=__import__('pathlib').Path(report['pdf_path'])
    if not pdf.exists() or digest(pdf)!=report['sha256']:
        raise RuntimeError('The independently reviewed PDF was moved or modified')
    docs=doc_fingerprint(api_url)
    if len(docs)!=4:raise RuntimeError(f'Expected exactly 4 indexed reports, got {len(docs)}')
    # New report and the 3 existing indexed report hashes are all required.
    expected={d['document_id'] for d in gold['documents'].values()}
    old=load(OLD_MANIFEST)['documents']
    if set(docs)!=set(old)|{report['sha256']}:
        raise RuntimeError('Document index must consist of the original three reports plus the new report')
    if not expected.issubset(docs):raise RuntimeError('Required benchmark source missing')
    hb=docs[report['sha256']]
    if hb['issuer'].casefold()!='handelsbanken' or hb['report_year']!=2026 or hb['report_quarter']!=3 or hb['currency']!='SEK' or hb['chunk_count']<=0:
        raise RuntimeError('New report registry identity/edition/currency/chunks do not match')
    return {'files':source_files(),'documents':docs,'gold_sha256':digest(SEALED),
            'audit_sha256':digest(AUDIT),'source_manifest_sha256':digest(SOURCE),
            'source_pdf_sha256':report['sha256'],'runtime':runtime(),'baseline':baseline()}


def freeze(api_url):
    if FREEZE.exists():raise RuntimeError('Day11/12 freeze already exists; no overwrite allowed')
    state=current(api_url);deps=packages()
    FREEZE.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(FREEZE/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for rel in state['files']:z.write(ROOT/rel,rel)
    (FREEZE/'pip_freeze.txt').write_text(deps,encoding='utf-8')
    state.update({'created_utc':datetime.now(timezone.utc).isoformat(),
                  'freeze_version':'day11-12-v1',
                  'pip_freeze_sha256':digest(FREEZE/'pip_freeze.txt'),
                  'snapshot_sha256':digest(FREEZE/'code_snapshot.zip'),
                  'limitations':'Vectors, physical PDF and model tensors are not snapshotted; preserve the source PDF and Chroma index unchanged.'})
    write_once(MANIFEST,state)
    print(f'DAY 11/12 FROZEN: {len(state["files"])} source/config files, {len(state["documents"])} indexed reports')


def check(api_url):
    if not MANIFEST.is_file():raise RuntimeError('Freeze missing')
    saved=load(MANIFEST)
    now=current(api_url)
    for k in ('files','documents','gold_sha256','audit_sha256','source_manifest_sha256','source_pdf_sha256','runtime','baseline'):
        if saved[k]!=now[k]:raise RuntimeError(f'FREEZE VIOLATION: {k} has changed')
    if saved['pip_freeze_sha256']!=digest(FREEZE/'pip_freeze.txt') or packages().splitlines()!=(FREEZE/'pip_freeze.txt').read_text(encoding='utf8').splitlines():
        raise RuntimeError('Installed dependencies changed')
    if saved['snapshot_sha256']!=digest(FREEZE/'code_snapshot.zip'):
        raise RuntimeError('Code snapshot changed')
    print(f'DAY 11/12 FREEZE CHECK PASSED: {len(now["files"])} source/config files unchanged, {len(now["documents"])} reports unchanged')
    return saved


def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--freeze',action='store_true');g.add_argument('--check',action='store_true')
    p.add_argument('--api-url',default=API_URL);a=p.parse_args()
    try:freeze(a.api_url) if a.freeze else check(a.api_url)
    except Exception as e:p.exit(2,f'ERROR: {e}\n')
if __name__=='__main__':main()
