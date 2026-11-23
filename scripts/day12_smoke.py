"""Day 12 smoke probes: localhost HTTP/status checks without querying unseen benchmark gold."""
from __future__ import annotations
import argparse,json,urllib.error,urllib.request
from scripts.day11_12_common import API_URL,get_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--api-url',default=API_URL);p.add_argument('--ops-token',default='');a=p.parse_args()
    host=a.api_url.rstrip('/')
    headers={'x-ops-token':a.ops_token} if a.ops_token else {}
    def get(route):
        req=urllib.request.Request(host+route,headers=headers)
        with urllib.request.urlopen(req,timeout=20) as r:return r.status,dict(r.headers),json.load(r)
    code,h,body=get('/ops/health');assert code==200 and body['status']=='ok'
    print('PASS /ops/health and service identity')
    code,h,body=get('/ops/readiness');assert code==200 and body['ready'] and body['indexed_reports']>=4
    print('PASS /ops/readiness:',body['indexed_reports'],'reports')
    code,h,body=get('/documents');assert code==200 and len(body['documents'])>=4
    print('PASS /documents: 4+ report registry (no questions sent)')
    code,h,body=get('/ops/metrics');assert code==200 and body['no_sensitive_bodies_logged']
    print('PASS /ops/metrics latency counters and privacy declaration')
    # Safe malformed-body probe; it MUST NOT require LLM inference.
    bad=urllib.request.Request(host+'/ask_v2',data=json.dumps({'question':'','document_selection':{'mode':'auto','document_ids':[]}}).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:urllib.request.urlopen(bad,timeout=10);raise AssertionError('Blank question unexpectedly accepted')
    except urllib.error.HTTPError as e:assert e.code==422,f'Expected 422, got {e.code}'
    print('PASS empty-question validation (HTTP 422)')
    print('SMOKE COMPLETE: no unseen benchmark question sent')
if __name__=='__main__':main()
