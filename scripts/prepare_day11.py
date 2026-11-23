"""Prepare and audit the new institution PDF; do not run assistant answers.

    python -m scripts.prepare_day11 --pdf "data/raw/Handelsbanken Q3 2026.pdf" --prepare
    python -m scripts.prepare_day11 --pdf "data/raw/Handelsbanken Q3 2026.pdf" --audit
    python -m scripts.prepare_day11 --pdf "data/raw/Handelsbanken Q3 2026.pdf" --seal --reviewer-attests

IMPORTANT: auto audit checks presence only, not row-to-column alignment or semantic correctness.
Review the PDF and audit output yourself before using --reviewer-attests.
"""
from __future__ import annotations
import argparse, json, re
from datetime import datetime,timezone
from pathlib import Path
from scripts.day11_12_common import CANDIDATE,SEALED,SOURCE,AUDIT,digest,load,write_once,validate_dataset


def inspect_pdf(pdf):
    try:import pymupdf as fitz
    except ImportError:import fitz
    with fitz.open(str(pdf)) as doc:
        if doc.is_encrypted:raise ValueError('Encrypted PDF')
        pages=[p.get_text('text',sort=True) or '' for p in doc]
    if len(pages)<25:raise ValueError('Unexpectedly short report (<25 physical pages)')
    header=' '.join(pages[:3]).casefold().replace('–','-').replace('—','-')
    if ('handelsbanken' not in header or '2026' not in header
        or not any(label in header for label in ('january-september','january - september','january september','third quarter','q3 2026'))):
        raise ValueError('PDF does not appear to be Handelsbanken January-September / Q3 2026 interim report')
    if sum(len(x.strip()) for x in pages)<10000:
        raise ValueError('PDF appears to lack enough searchable text; check for scanned pages')
    return pages


def searchable(s):
    s=s.casefold().replace('\u00a0',' ').replace('\u2013','-').replace('\u2212','-').replace('\u2011','-')
    # normalize thousand separators in digit groups, while preserving decimal dots
    s=re.sub(r'(?<=\d)[, ](?=\d{3}(?:\D|$))','',s)
    return re.sub(r'\s+',' ',s)


def audit_gold(candidate, pages, doc_id):
    checked=[]
    for c in candidate['cases']:
        for ev in c.get('evidence',[]):
            if ev['document_id'] not in (doc_id,candidate['documents']['SEB']['document_id']):
                raise ValueError(f'{c["id"]}: unexpected evidence document')
            if ev['document_id']!=doc_id:continue # already audited SEB from Day 10
            page=ev['page']
            ok_page=1<=page<=len(pages)
            text=searchable(pages[page-1]) if ok_page else ''
            row=searchable(ev['row_anchor']);num=searchable(ev['raw_value'])
            result={'case_id':c['id'],'page':page,'row':ev['row_anchor'],'raw_value':ev['raw_value'],
                    'page_exists':ok_page,'row_present':bool(row and row in text),'value_present':bool(num and num in text)}
            result['pass_presence']=result['page_exists'] and result['row_present'] and result['value_present']
            checked.append(result)
    return checked


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdf',type=Path,required=True)
    m=p.add_mutually_exclusive_group(required=True)
    m.add_argument('--prepare',action='store_true');m.add_argument('--audit',action='store_true');m.add_argument('--seal',action='store_true')
    p.add_argument('--reviewer-attests',action='store_true',help='Only after independent visual source and answer review')
    a=p.parse_args();pdf=a.pdf.resolve()
    if not pdf.is_file():p.error('Download the official Handelsbanken report PDF first')
    pages=inspect_pdf(pdf);h=digest(pdf)
    if a.prepare:
        if SOURCE.exists():
            info=load(SOURCE)
            if info['sha256']!=h:raise RuntimeError('Prepared report SHA differs. Do not change document edition.')
            print('ALREADY PREPARED:',h);return
        write_once(SOURCE,{'sha256':h,'pdf_path':str(pdf),'page_count':len(pages),
                           'issuer':'Handelsbanken','report_year':2026,'report_quarter':3,'currency':'SEK',
                           'prepared_utc':datetime.now(timezone.utc).isoformat()})
        print(f'PREPARED: {len(pages)} physical pages, SHA256 {h}');return
    if not SOURCE.exists() or load(SOURCE)['sha256']!=h:
        raise RuntimeError('Run --prepare with this same PDF first')
    candidate=validate_dataset(CANDIDATE,require_sealed=False)
    if candidate['documents']['HB']['document_id']!='NEW_PDF_SHA256_SET_BY_PREPARE':
        raise RuntimeError('Expected original unmodified Day 11 candidate template')
    checks=audit_gold(candidate,pages,'NEW_PDF_SHA256_SET_BY_PREPARE')
    failed=[r for r in checks if not r['pass_presence']]
    print(f'PDF reference presence: {len(checks)-len(failed)}/{len(checks)} matched expected physical page, row anchor, and value.')
    for x in failed:
        print(f'MISMATCH: {x["case_id"]} p{x["page"]} row={x["row"]!r} value={x["raw_value"]!r} row_found={x["row_present"]} value_found={x["value_present"]}')
    print('Presence checks do NOT prove correct row/period alignment. Inspect pages and the draft gold before approving.')
    if a.audit:return
    if not a.reviewer_attests:raise RuntimeError('Use --reviewer-attests only after independent page/column and narrative review')
    if failed:raise RuntimeError('Unresolved physical-PDF mismatches. Do NOT seal inaccurate gold. Correct the candidate before freezing.')
    if SEALED.exists() or AUDIT.exists():raise RuntimeError('Sealed gold already exists; never overwrite it')
    d=json.loads(json.dumps(candidate).replace('NEW_PDF_SHA256_SET_BY_PREPARE',h))
    d['dataset_version']='day11-sealed-v1';d['status']='PDF_AUDITED_AND_SEALED'
    d['pdf_sha256']=h;d['source_candidate_sha256']=digest(CANDIDATE)
    d['reference_status']='Independently reviewed against the exact PDF by local reviewer; anchors auto-checked only for presence.'
    write_once(SEALED,d)
    write_once(AUDIT,{'reviewer_attestation':True,'reviewed_utc':datetime.now(timezone.utc).isoformat(),
                      'pdf_sha256':h,'candidate_sha256':digest(CANDIDATE),'sealed_sha256':digest(SEALED),
                      'checks':checks,'review_note':'Reviewer must confirm each cited physical page, row, target column, unit and arithmetic by inspection. Presence check alone is not sufficient.'})
    print('SEALED:',SEALED,'| SHA256',digest(SEALED))

if __name__=='__main__':main()
