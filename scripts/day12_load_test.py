"""Bounded latency check using /ops/health only; never load-test LLM or unseen questions.

Max 30 requests, concurrency 1–3. No performance guarantee implied.
"""
import argparse,concurrent.futures,statistics,time,urllib.request
from scripts.day11_12_common import API_URL

def run(url,count=12,workers=2):
    if not 1<=count<=30 or not 1<=workers<=3:raise ValueError('Bounded load limits exceeded')
    def one(_):
        t=time.perf_counter()
        with urllib.request.urlopen(url.rstrip('/')+'/ops/health',timeout=10) as r:
            status=r.status;r.read()
        return (status,round((time.perf_counter()-t)*1000,2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as e:results=list(e.map(one,range(count)))
    if any(code!=200 for code,_ in results):raise RuntimeError('HTTP health errors detected')
    v=sorted(t for _,t in results)
    print(f'LOAD SMOKE: {count} /ops/health requests; concurrency={workers}; p50={statistics.median(v)}ms p95={v[round((len(v)-1)*.95)]}ms')
    print('No LLM latency measured; /ask_v2 load must be separately reviewed and bounded.')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--api-url',default=API_URL);p.add_argument('--count',type=int,default=12);p.add_argument('--workers',type=int,default=2)
    a=p.parse_args();run(a.api_url,a.count,a.workers)
