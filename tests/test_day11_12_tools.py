import json, os, urllib.error
from pathlib import Path
from unittest import mock
import pytest
from scripts.day11_12_common import validate_dataset,grade,primary_number,safe_reasons
from scripts import day12_fault_injection

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/evaluation/day11_50_candidate.json'


@pytest.fixture(scope='module')
def candidate_dataset():
    """Load the private Day 11 benchmark, or skip only tests that require it.

    The candidate benchmark is intentionally excluded from the public repository.
    In the original local workspace it remains available, so these tests execute
    with their full assertions.
    """
    if not DATA.is_file():
        pytest.skip("Private Day 11 candidate benchmark is not included in the public repository")
    return validate_dataset(DATA, require_sealed=False)


def test_exactly_50_fresh_cases(candidate_dataset):
    ds=candidate_dataset
    assert len(ds['cases'])==50
    assert len({c['question'] for c in ds['cases']})==50
    assert all(c['id'].startswith('D11_') for c in ds['cases'])


def test_provisional_cannot_be_used_as_first_run(candidate_dataset):
    with pytest.raises(ValueError,match='not PDF audited'):
        validate_dataset(DATA,require_sealed=True)


def test_document_references_are_in_dataset(candidate_dataset):
    ds=candidate_dataset
    docids={d['document_id'] for d in ds['documents'].values()}
    assert all(s['document_id'] in docids for c in ds['cases'] for s in c['expected_sources'])


def test_short_verified_narrative_is_review_not_fail(candidate_dataset):
    c=next(c for c in candidate_dataset['cases'] if c['expected_outcome']=='explanation')
    r={'supported':True,'needs_clarification':False,'answer':'UK and Netherlands','sources':[{'document_id':s['document_id'],'page':s['page']} for s in c['expected_sources']]}
    assert grade(c,r)[0]=='REVIEW'


def test_unverified_narrative_fails(candidate_dataset):
    c=next(c for c in candidate_dataset['cases'] if c['expected_outcome']=='explanation')
    assert grade(c,{'supported':False,'sources':[]})[0]=='FAIL'


def test_numeric_correct_with_citation_passes(candidate_dataset):
    c=next(c for c in candidate_dataset['cases'] if c['id']=='D11_01')
    assert grade(c,{'supported':True,'answer':'SEK 10,051 million','sources':c['expected_sources']})==('PASS','')


def test_wrong_citation_even_same_number_fails(candidate_dataset):
    c=next(c for c in candidate_dataset['cases'] if c['id']=='D11_01')
    assert grade(c,{'supported':True,'answer':'SEK 10,051 million','sources':[{'document_id':c['expected_sources'][0]['document_id'],'page':25}]})[0]=='FAIL'


def test_wrong_number_fails(candidate_dataset):
    c=next(c for c in candidate_dataset['cases'] if c['id']=='D11_01')
    assert grade(c,{'supported':True,'answer':'SEK 10,052 million','sources':c['expected_sources']})[1]=='wrong_primary_number'


def test_currency_protection_necessary(candidate_dataset):
    c=next(c for c in candidate_dataset['cases'] if c.get('expected_reason')=='currency_incompatible')
    assert grade(c,{'supported':False,'answer':'Cannot verify','sources':[]})[1]=='missing_currency_explanation'
    assert grade(c,{'supported':False,'answer':'SEK and EUR cannot be compared without a conversion rate','sources':[]})[0]=='PASS'


def test_primary_number_keeps_sign_and_commas():
    assert str(primary_number('SEK -5,910 million. Q2 value was -5,963'))=='-5910'
    assert str(primary_number('SEK 10,051 million'))=='10051'


def test_calc_operands_correct(candidate_dataset):
    ds=candidate_dataset
    by={x['id']:x for x in ds['cases']}
    assert by['D11_31']['expected_value']=='43'
    assert by['D11_34']['expected_value']=='131798'
    assert by['D11_39']['expected_value']=='2059'


def test_fault_scenarios_complete():
    day12_fault_injection.run_offline()


def test_result_classification_does_not_fake_root_cause():
    assert safe_reasons({'failure_code':'answer_not_supported'})=='retrieval_or_understanding_or_verification_unknown'


def test_evidence_presence_normalization(candidate_dataset):
    from scripts.prepare_day11 import searchable,audit_gold
    assert searchable('SEK 1,479,245')=='sek 1479245'
    ds=candidate_dataset
    pages=['']*25
    pages[23]='Net interest income 10,051 10,008\nTotal income 13,959'
    rows=audit_gold({'cases':ds['cases'][:2],'documents':ds['documents']},pages,'NEW_PDF_SHA256_SET_BY_PREPARE')
    assert len(rows)==2 and all(x['pass_presence'] for x in rows)


def test_log_privacy_and_rotation(tmp_path):
    from app.day12_observability import SafeMetrics
    logfile=tmp_path/'events.jsonl'
    m=SafeMetrics(log_path=logfile)
    m.record({'timestamp_utc':'2026-11-16','request_id':'safe','route':'/ask_v2','method':'POST','status':200,'elapsed_ms':12,
              'question':'SECRET PERSONAL CONTENT','authorization':'SUPER SECRET TOKEN','document_text':'PRIVATE SOURCE'})
    value=logfile.read_text()
    assert 'SECRET' not in value and 'PRIVATE' not in value
    assert 'POST' in value and m.summary()['requests_observed']==1


def test_request_id_and_metrics_http(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.day12_observability import install,SafeMetrics
    monkeypatch.setenv('FINANCE_AI_OPS_TOKEN','test-secret')
    app=FastAPI()
    @app.get('/documents')
    def docs():return {'documents':[{'document_id':'stub'}]*4}
    @app.post('/ask_v2')
    def ask():return {'supported':False}
    m=SafeMetrics(log_path=tmp_path/'events.jsonl')
    install(app,m)
    cli=TestClient(app,client=('127.0.0.1',50000))
    r=cli.get('/ops/health');assert r.status_code==200 and r.headers['X-Request-ID']
    assert cli.get('/ops/metrics').status_code==403
    assert cli.get('/ops/metrics',headers={'X-Ops-Token':'test-secret'}).status_code==200
    assert cli.post('/ask_v2',json={'question':'sensitive'}).status_code==200
    assert m.summary()['requests_observed']>=3
    log=(tmp_path/'events.jsonl').read_text()
    assert 'sensitive' not in log and 'test-secret' not in log


def test_ops_localhost_only_without_token(tmp_path,monkeypatch):
    from app.day12_observability import allow_ops
    from starlette.requests import Request
    monkeypatch.delenv('FINANCE_AI_OPS_TOKEN',raising=False)
    req=Request({'type':'http','client':('8.8.8.8',12345),'headers':[],'method':'GET','path':'/ops/metrics','scheme':'http','server':('a',80)})
    with pytest.raises(Exception):allow_ops(req)


def test_overly_long_bounded_load_refused():
    from scripts.day12_load_test import run
    with pytest.raises(ValueError):run('http://127.0.0.1:8000',count=999)


def test_freeze_prevents_mutation(tmp_path,monkeypatch):
    from scripts import freeze_day11_12 as f
    monkeypatch.setattr(f,'FREEZE',tmp_path/'freeze')
    monkeypatch.setattr(f,'MANIFEST',tmp_path/'freeze/manifest.json')
    monkeypatch.setattr(f,'packages',lambda:'mock-pkg==1\n')
    monkeypatch.setattr(f,'current',lambda url:{'files':{},'documents':{},'gold_sha256':'a','audit_sha256':'b','source_manifest_sha256':'c','source_pdf_sha256':'d','runtime':{},'baseline':{}})
    f.freeze('test://')
    f.check('test://')
    with pytest.raises(RuntimeError,match='already exists'):f.freeze('test://')
    monkeypatch.setattr(f,'current',lambda url:{'files':{'changed':'yes'},'documents':{},'gold_sha256':'a','audit_sha256':'b','source_manifest_sha256':'c','source_pdf_sha256':'d','runtime':{},'baseline':{}})
    with pytest.raises(RuntimeError,match='FREEZE VIOLATION'):f.check('test://')


def test_runner_preserves_first_run_records(tmp_path,monkeypatch,candidate_dataset):
    from scripts import evaluate_day11 as r
    base=tmp_path/'first';f=tmp_path/'freeze';f.mkdir()
    (f/'manifest.json').write_text('{}')
    monkeypatch.setattr(r,'FIRST',base);monkeypatch.setattr(r,'FREEZE',f)
    monkeypatch.setattr(r,'check',lambda url:{'mock':True})
    monkeypatch.setattr(r,'validate_dataset',lambda:candidate_dataset)
    monkeypatch.setattr(r,'SEALED',DATA)
    # Simulated transport, not the user's actual system or new-bank responses.
    def fake_response(url,payload):
        return {'answer':'cannot verify','supported':False,'needs_clarification':False,'sources':[]}
    monkeypatch.setattr(r,'post_json',fake_response)
    r.run('http://fake:8000',False)
    assert len((base/'results.jsonl').read_text().splitlines())==50
    assert 'first_run' in (base/'run_metadata.json').read_text()
    with pytest.raises(RuntimeError,match='already exists'):r.run('http://fake:8000',False)
    # Ensure resume detects sealed complete result rather than repeating questions.
    with pytest.raises(RuntimeError,match='already summarized'):r.run('http://fake:8000',True)

def test_decimal_primary_number_and_ratio_units():
    assert str(primary_number('SEK 2.87 per share.'))=='2.87'
    assert str(primary_number('0.00% was the credit loss ratio.'))=='0.00'


def test_decimal_benchmark_case_grades_correctly(candidate_dataset):
    c=candidate_dataset['cases'][25]
    assert grade(c,{'supported':True,'answer':'SEK 2.87 per share','sources':c['expected_sources']})[0]=='PASS'


def test_manual_isolation_rejects_unselected_document(candidate_dataset):
    ds=candidate_dataset
    c=ds['cases'][1]
    assert c['document_selection']['mode']=='manual'
    r={'supported':True,'answer':'SEK 10,008 million','sources':c['expected_sources'],
       'selected_documents':[{'document_id':'rogue-report'}]}
    assert grade(c,r)[1]=='manual_source_isolation_violation'


def test_real_http_smoke_and_bounded_load(monkeypatch):
    import http.server,threading,sys
    from scripts import day12_smoke,day12_load_test
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            val={'/ops/health':{'status':'ok'},'/ops/readiness':{'ready':True,'indexed_reports':4},
                 '/documents':{'documents':[{'document_id':'test'}]*4},'/ops/metrics':{'no_sensitive_bodies_logged':True}}.get(self.path)
            raw=json.dumps(val or {}).encode('utf8');self.send_response(200 if val else 404);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            # BaseHTTPRequestHandler does not consume the request body automatically.
            # Drain the declared Content-Length before closing the connection; otherwise
            # Windows can reset a socket with unread request bytes (WinError 10053),
            # hiding the HTTP 422 response that this smoke test is meant to assert.
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > 0:
                self.rfile.read(content_length)
            self.send_response(422)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        monkeypatch.setattr(sys,'argv',['smoke','--api-url',url]);day12_smoke.main()
        day12_load_test.run(url,count=8,workers=2)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)

def test_observability_log_failure_cannot_break_financial_request(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.day12_observability import SafeMetrics,install
    blocked=tmp_path/'not-a-directory'
    blocked.write_text('file')
    m=SafeMetrics(log_path=blocked/'events.jsonl')
    app=FastAPI()
    @app.get('/ask_v2')
    def answer():return {'supported':False,'answer':'safe'}
    install(app,m)
    cli=TestClient(app)
    assert cli.get('/ask_v2').status_code==200
    assert m.summary()['log_write_failures']==1


def test_telemetry_redacts_unknown_path_and_query(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.day12_observability import SafeMetrics,install
    m=SafeMetrics(log_path=tmp_path/'safe.jsonl')
    app=FastAPI()
    install(app,m)
    cli=TestClient(app)
    cli.get('/hidden/SECRETIDENTIFIER?token=APISECRET')
    data=(tmp_path/'safe.jsonl').read_text()
    assert 'SECRETIDENTIFIER' not in data and 'APISECRET' not in data and '"route": "/other"' in data
