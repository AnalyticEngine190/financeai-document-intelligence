"""Recheck Day 10.4 references independently against the 3 PDF page texts.

This utility reads PDF files only. It does not import or query the financial AI
answering pipeline and never sends gold labels to FastAPI or Ollama.

Usage: python -m scripts.verify_day10_4_gold
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from scripts.day10_4_freeze import ROOT, DATASET, digest


def locate_by_digest(sha: str) -> Path:
    # Prefer the user's original names; a re-downloaded report can have a
    # different filename but must have exactly the same SHA256 bytes.
    candidates=list(ROOT.glob('*.pdf')) + list((ROOT/'data'/'raw').glob('*.pdf'))
    candidates += list((ROOT/'data').glob('*.pdf'))
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file() and digest(candidate)==sha:
            return candidate
    raise FileNotFoundError(f'No local PDF matches the indexed document SHA256 {sha}. Keep the actual indexed source PDF available for independent gold verification.')


def verify() -> dict:
    try:import fitz
    except ImportError as e:raise RuntimeError('PyMuPDF is needed: pip install pymupdf') from e
    data=json.loads(DATASET.read_text(encoding='utf-8'))
    pdfs={key:locate_by_digest(doc['document_id']) for key,doc in data['documents'].items()}
    text={}
    for key,path in pdfs.items():
        with fitz.open(path) as pdf:
            if len(pdf)!=data['documents'][key]['pages']:
                raise ValueError(f'Unexpected page count: {key}')
            text[key]=[re.sub(r'\s+',' ',p.get_text('text')).casefold() for p in pdf]
    checked=0
    for case in data['cases']:
        for e in case.get('evidence',[]):
            content=text[e['doc_key']][e['page']-1]
            anchor=re.sub(r'\s+',' ',e['row_anchor']).casefold()
            value=re.sub(r'\s+',' ',e['raw_value']).casefold()
            offset=content.find(anchor)
            if offset<0:
                raise AssertionError(f'{case["id"]}: row missing from physical page {e["page"]}: {anchor}')
            if value not in content[offset:offset+600]:
                raise AssertionError(f'{case["id"]}: printed value {value} not found near {anchor}')
            checked+=1
    # Check known comparisons numerically, using independently transcribed operands.
    from decimal import Decimal
    math_cases={
        'CROSS_DOCUMENT_01':(3032,2968,64),
        'CROSS_DOCUMENT_02':(880,880,0),
        'CROSS_DOCUMENT_03':('15.7','15.8','0.1'),
        'CROSS_DOCUMENT_04':(712394,704631,7763),
        'CROSS_DOCUMENT_05':(7194,6702,492),
    }
    index={c['id']:c for c in data['cases']}
    for cid,(a,b,expected) in math_cases.items():
        if abs(Decimal(str(b))-Decimal(str(a)))!=Decimal(str(expected)):
            raise AssertionError(f'{cid}: incorrect reference arithmetic')
        if Decimal(index[cid]['expected_value'])!=Decimal(str(expected)):
            raise AssertionError(f'{cid}: incorrect gold label')
    print('GOLD REFERENCE CHECK PASSED:')
    print(' ',len(data['cases']),'cases,',checked,'printed metric/operand anchors, 5 independently checked calculations')
    for key,p in pdfs.items():print(' ',key, p.name, digest(p))
    print('This verifies cited text/value co-location, not the full table-column interpretation; factual gold was checked separately when authored.')
    return {'cases':len(data['cases']),'anchors':checked,'documents':{k:digest(p) for k,p in pdfs.items()}}

if __name__=='__main__':verify()
