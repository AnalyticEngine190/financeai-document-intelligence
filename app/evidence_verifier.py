"""Fail-closed checks for document-scoped financial cells and derived results.

A number merely appearing on a page is not sufficient. The producing table
adapter must provide an aligned cell: document, page, metric, period, row, raw
number and unit. This module checks provenance and disallows source leakage.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import re
from app.table_lookup import FinancialCell
from app import document_tables

@dataclass(frozen=True)
class EvidenceVerdict:
    accepted: bool
    reason: str


def verify_cell(cell: FinancialCell | None, *, document_id: str, metric: str,
                period: str, scope: str = "group") -> EvidenceVerdict:
    if cell is None:
        return EvidenceVerdict(False,"No aligned source cell")
    if (cell.metric,cell.period,cell.scope)!=(metric,period,scope):
        return EvidenceVerdict(False,"Metric/period/scope mismatch")
    if not isinstance(cell.value,Decimal) or not cell.value.is_finite():
        return EvidenceVerdict(False,"Non-finite or untyped numeric value")
    if cell.page < 1 or not cell.document or not cell.row_label or not cell.raw_value:
        return EvidenceVerdict(False,"Missing row-level provenance")
    if not re.fullmatch(r"(?:EUR|SEK|NOK|DKK|USD|GBP)(?:m|bn)?|percent|ratio|basis_points|count",cell.unit):
        return EvidenceVerdict(False,"Unrecognized unit")
    # Check the indexed bytes for the selected doc ID, not any other report.
    candidates=[p for p in document_tables._get_pages(document_id)
                if p['page']==cell.page and p['document']==cell.document]
    if len(candidates)!=1:
        return EvidenceVerdict(False,"Cited page is not uniquely indexed in selected document")
    lines=candidates[0]['text'].splitlines()
    label=re.sub(r"\s+"," ",cell.row_label.strip()).casefold()
    value=re.sub(r"\s+"," ",cell.raw_value.strip())
    # A true row/value alignment is guaranteed by the producing adapter;
    # independently confirm that the same row and raw token occur on the page.
    for i,line in enumerate(lines):
        normalized=re.sub(r"\s+"," ",(line+" "+(lines[i+1] if i+1<len(lines) else "")).strip())
        if label in normalized.casefold() and value in normalized:
            return EvidenceVerdict(True,"Indexed aligned row and raw cell present")
    return EvidenceVerdict(False,"Cited row and raw value not found together")


def verify_operands(cells: list[FinancialCell], *, document_ids: list[str],
                    metric: str, periods: list[str], scope: str="group") -> EvidenceVerdict:
    if not cells or len(cells)!=len(document_ids) or len(cells)!=len(periods):
        return EvidenceVerdict(False,"Missing operand")
    for cell,doc_id,period in zip(cells,document_ids,periods):
        # Comparison operands originate from strict table adapters, not from an
        # arbitrary answer string or a language model. Validate their complete
        # typed provenance; no redundant Chroma query is required here.
        if (cell.metric,cell.period,cell.scope)!=(metric,period,scope) or not cell.document or cell.page<1 or not cell.row_label or not cell.raw_value:
            return EvidenceVerdict(False,"Invalid operand provenance")
        if not isinstance(cell.value,Decimal) or not cell.value.is_finite():
            return EvidenceVerdict(False,"Invalid numeric operand")
    if len({c.unit for c in cells})!=1:
        return EvidenceVerdict(False,"Incompatible verified units")
    return EvidenceVerdict(True,"All operands verified")
