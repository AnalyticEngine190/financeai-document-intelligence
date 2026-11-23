"""Shared, standard-library-only evaluation utilities. No LLM or index writes."""
from __future__ import annotations
import hashlib, json, os, re, time, urllib.request
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_URL = os.getenv('FINANCE_AI_API_URL', 'http://127.0.0.1:8000').rstrip('/')
EVAL_DIR = ROOT / 'data' / 'evaluation'
CANDIDATE = EVAL_DIR / 'day11_50_candidate.json'
SEALED = EVAL_DIR / 'day11_50_sealed.json'
SOURCE = EVAL_DIR / 'day11_source.json'
AUDIT = EVAL_DIR / 'day11_gold_audit.json'
FREEZE = ROOT / 'reports' / 'evaluation' / 'day11_12_freeze'
FIRST = ROOT / 'reports' / 'evaluation' / 'day11_first_run'
EXPECTED_CATEGORIES = {'income_statement':14,'balance_sheet':10,'ratios':6,'calculations':8,'comparisons':5,'explanations':4,'safety':3}


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20), b''):h.update(chunk)
    return h.hexdigest()


def load(path:Path)->dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_once(path: Path, obj:dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n')


def get_json(url:str, timeout=15):
    req=urllib.request.Request(url,headers={'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)


def post_json(url:str, data:dict, timeout:int=180):
    req=urllib.request.Request(url,data=json.dumps(data).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)


def doc_fingerprint(api_url=API_URL):
    records=get_json(api_url.rstrip('/')+'/documents')['documents']
    if not isinstance(records,list):raise ValueError('/documents did not return an array')
    return {d['document_id']:{k:d.get(k) for k in ('issuer','report_year','report_quarter','currency','chunk_count','report_type')} for d in records}


def validate_dataset(path=SEALED, require_sealed=True):
    d=load(Path(path)); cases=d.get('cases')
    if len(cases or [])!=50 or Counter(x.get('category') for x in cases)!=EXPECTED_CATEGORIES:
        raise ValueError('Day 11 benchmark must contain 50 cases in the fixed category distribution')
    if len({c['id'] for c in cases})!=50:raise ValueError('Duplicate case IDs')
    if require_sealed and (d.get('dataset_version')!='day11-sealed-v1' or d.get('status')!='PDF_AUDITED_AND_SEALED'):
        raise ValueError('Gold is not PDF audited and sealed; do not run a first evaluation')
    docs={x['document_id'] for x in d['documents'].values()}
    for c in cases:
        o=c['expected_outcome'];sel=c['document_selection'];ids=sel['document_ids']
        if o not in {'answer','abstain','clarify','explanation'}:raise ValueError(c['id']+' invalid outcome')
        if not c['question'].strip():raise ValueError(c['id']+' blank question')
        if sel['mode']=='auto' and ids or sel['mode']=='manual' and len(ids)!=1 or sel['mode']=='compare' and len(ids)!=2:
            raise ValueError(c['id']+' invalid selection')
        if any(x not in docs for x in ids):raise ValueError(c['id']+' unknown selected document')
        for s in c['expected_sources']:
            if s['document_id'] not in docs or not isinstance(s['page'],int) or s['page']<1:
                raise ValueError(c['id']+' bad reference')
        if o=='answer':
            Decimal(c['expected_value'])
            if not c['expected_sources'] or not c['expected_unit'] or not c.get('evidence'):
                raise ValueError(c['id']+' missing numeric gold')
        if o=='explanation' and (not c['expected_sources'] or not c.get('human_review_criteria')):
            raise ValueError(c['id']+' incomplete narrative rubric')
        if o in ('abstain','clarify') and c['expected_sources']:
            raise ValueError(c['id']+' unsupported query cannot require source citation')
    return d


def primary_number(text):
    # Match only within the leading answer clause, never a subsequently explained operand.
    m=re.search(r'(?<![\w])[-+]?(?:\d{1,3}(?:[, ]\d{3})+|\d+)(?:\.\d+)?(?!\w)',str(text)[:170])
    if not m:return None
    try:return Decimal(m.group().replace(',','').replace(' ',''))
    except InvalidOperation:return None


def grade(case, response):
    """Numerical / source tests are strict; narrative questions always need human review.

    Never consider a short but verified narrative automatically wrong.
    """
    outcome=case['expected_outcome'];text=str(response.get('answer',''))
    supported=response.get('supported');clarify=bool(response.get('needs_clarification'))
    sources=response.get('sources') or []
    # Manual selections must never include an unselected report, even if the
    # response happens not to cite it. This is separate from citation grading.
    if case['document_selection']['mode']=='manual':
        permitted=set(case['document_selection']['document_ids'])
        picked={d.get('document_id') for d in response.get('selected_documents',[]) if isinstance(d,dict)}
        cited={s.get('document_id') for s in sources if isinstance(s,dict)}
        if not picked.issubset(permitted) or not cited.issubset(permitted):
            return 'FAIL','manual_source_isolation_violation'
    if outcome=='clarify':
        return ('PASS','') if supported is False and clarify and not sources else ('FAIL','expected_clarification')
    if outcome=='abstain':
        if supported is not False or clarify or sources:return 'FAIL','expected_clean_abstention'
        if case.get('expected_reason')=='currency_incompatible':
            lo=text.casefold()
            if 'currency' not in lo and 'exchange rate' not in lo and not ('sek' in lo and 'eur' in lo):
                return 'FAIL','missing_currency_explanation'
        return 'PASS',''
    if supported is not True or clarify:return 'FAIL','answer_not_supported'
    if not isinstance(sources,list) or any(not isinstance(s,dict) or not s.get('document_id') or type(s.get('page')) is not int for s in sources):
        return 'FAIL','invalid_citation'
    expected={}
    for src in case['expected_sources']:
        expected.setdefault(src['document_id'],set()).add(src['page'])
    observed={(s['document_id'],s['page']) for s in sources}
    if any(doc not in expected or pg not in expected[doc] for doc,pg in observed):return 'FAIL','wrong_or_unexpected_citation'
    if any(not any((doc,p) in observed for p in pages) for doc,pages in expected.items()):return 'FAIL','missing_required_source'
    if outcome=='explanation':return 'REVIEW','human_semantic_and_evidence_review_required'
    v=primary_number(text)
    if v is None:return 'FAIL','missing_primary_number'
    if v!=Decimal(case['expected_value']):return 'FAIL','wrong_primary_number'
    u=case['expected_unit'];head=text.casefold()[:170]
    if u=='percent' and not ('%' in head or 'per cent' in head):return 'FAIL','missing_percent_unit'
    if u=='SEKm' and not ('sek' in head and ('million' in head or 'sekm' in head)):
        return 'FAIL','missing_currency_or_scale'
    if u=='SEK_per_share' and not ('sek' in head or 'krona' in head):return 'FAIL','missing_share_currency'
    return 'PASS',''


def safe_reasons(r):
    """Classify errors using only public response codes, never claim hidden root causes."""
    reason=r.get('failure_code','')
    if reason in {'wrong_primary_number','missing_primary_number'}:return 'arithmetic_or_metric'
    if 'citation' in reason or 'source' in reason:return 'provenance_or_selection'
    if reason in {'answer_not_supported'}:return 'retrieval_or_understanding_or_verification_unknown'
    if reason in {'transport_or_runtime_error'}:return 'transport'
    if 'abstention' in reason or 'clarification' in reason or 'currency' in reason:return 'safety_or_intent'
    return 'other'
