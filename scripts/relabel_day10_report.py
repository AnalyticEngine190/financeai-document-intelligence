"""Safely correct one report's display metadata without changing indexed data.

Run with --apply to save. Without --apply, only prints the proposed change.
Does not re-embed, re-ingest, delete Chroma chunks, or change document IDs.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
from app.document_registry import get_document, save_document


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--document-id',required=True)
    parser.add_argument('--report-type',required=True,choices=('factbook','interim_report','financial_report','prospectus'))
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    doc=get_document(args.document_id)
    if doc is None:
        parser.error('Document ID is not in the current Chroma index')
    updated=replace(doc,report_type=args.report_type)
    print(f'Document: {doc.filename} (issuer={doc.issuer}, id={doc.document_id})')
    print(f'Report type: {doc.report_type} -> {updated.report_type}')
    if args.apply:
        save_document(updated)
        print('Updated display metadata. Indexed document chunks were not modified.')
    else:
        print('Preview only. Add --apply to save the change.')


if __name__=='__main__':
    main()
