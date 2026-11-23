"""Opt-in FastAPI observability: request IDs, aggregate metrics, readiness and safe JSONL logs.

No question text, PDF passages, auth headers, IP addresses or model prompts logged.
Single-worker, in-memory metrics; event logs rotate locally at ~5 MiB.
Do not publish /ops endpoints on the open Internet without FINANCE_AI_OPS_TOKEN.
"""
from __future__ import annotations
import hmac,json,os,statistics,threading,time,uuid
from collections import Counter,deque
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse

class SafeMetrics:
    def __init__(self,max_events=1000,log_path=None):
        self.lock=threading.RLock()
        self.events=deque(maxlen=max_events)
        self.counts=Counter()
        self.log_failures=0
        self.log_path=Path(log_path) if log_path else Path(__file__).resolve().parents[1]/'reports/observability/day12_events.jsonl'

    def record(self,ev):
        # Only this fixed allowlist goes to disk; never log raw requests/responses.
        allowed=('timestamp_utc','request_id','route','method','status','elapsed_ms','exception_type')
        sanitized={k:ev[k] for k in allowed if k in ev}
        with self.lock:
            self.events.append(sanitized);self.counts[(sanitized.get('route'),sanitized.get('status'))]+=1
            if os.getenv('FINANCE_AI_DAY12_FILE_LOG','1')!='1':return
            try:
                self.log_path.parent.mkdir(parents=True,exist_ok=True)
                if self.log_path.exists() and self.log_path.stat().st_size>5_000_000:
                    prev=self.log_path.with_suffix('.jsonl.1')
                    if prev.exists():prev.unlink()
                    self.log_path.replace(prev)
                with self.log_path.open('a',encoding='utf8') as f:
                    f.write(json.dumps(sanitized,ensure_ascii=False)+'\n')
            except OSError:
                # Observability should never turn a successful financial answer
                # into an API 500 merely because the log disk is full/unwritable.
                self.log_failures+=1

    def summary(self):
        with self.lock:
            data=list(self.events);codes={f'{k[0]}:{k[1]}':v for k,v in self.counts.items()}
        durations=sorted(float(x['elapsed_ms']) for x in data if 'elapsed_ms' in x)
        pct=lambda p:durations[round((len(durations)-1)*p)] if durations else None
        return {'requests_observed':len(data),'status_counts':codes,'log_write_failures':self.log_failures,
                'latency_ms':{'p50':pct(.5),'p95':pct(.95),'max':durations[-1] if durations else None},
                'retention':'most recent 1000 events per process; in-memory counters reset on restart',
                'no_sensitive_bodies_logged':True}


def allow_ops(request:Request):
    secret=os.getenv('FINANCE_AI_OPS_TOKEN','')
    if secret:
        provided=request.headers.get('x-ops-token','')
        if not hmac.compare_digest(provided,secret):raise HTTPException(403,'Ops token required')
        return
    host=request.client.host if request.client else ''
    if host not in {'127.0.0.1','::1','localhost','testclient'}:
        raise HTTPException(403,'Operations endpoints are localhost-only unless FINANCE_AI_OPS_TOKEN is configured')


def install(app:FastAPI, metrics=None):
    if getattr(app.state,'day12_instrumented',False):return app.state.day12_metrics
    m=metrics or SafeMetrics();app.state.day12_instrumented=True;app.state.day12_metrics=m

    @app.middleware('http')
    async def telemetry(request:Request,call_next):
        start=time.perf_counter();rid=str(uuid.uuid4());status=500;etype=None
        try:
            response=await call_next(request)
            status=response.status_code
            response.headers['X-Request-ID']=rid
            response.headers['X-Process-Time-Ms']=str(round((time.perf_counter()-start)*1000,2))
            return response
        except Exception as e:
            etype=type(e).__name__
            raise
        finally:
            route=request.url.path
            if route not in {'/ask_v2','/ask','/documents','/health','/ops/health','/ops/readiness','/ops/metrics'}:route='/other'
            # Never log path query strings, request body or response bodies.
            ms=round((time.perf_counter()-start)*1000,2)
            m.record({'timestamp_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                      'request_id':rid,'route':route,'method':request.method,'status':status,
                      'elapsed_ms':ms,'exception_type':etype})
            # Headers are populated on success using inner middleware below.

    @app.get('/ops/health',include_in_schema=False)
    def ops_health():
        return {'status':'ok','service':'finance-ai','mode':'day11-12'}

    @app.get('/ops/readiness',include_in_schema=False)
    def ops_readiness(request:Request):
        allow_ops(request)
        try:
            from app.document_registry import list_documents
            docs=list_documents()
            if not docs:raise ValueError('Empty document index')
            return {'ready':True,'indexed_reports':len(docs),
                    'issuers':sorted({d.issuer for d in docs})}
        except Exception as exc:
            # Redact raw paths and underlying failure messages from API clients.
            return JSONResponse({'ready':False,'reason':'document_registry_unavailable'},status_code=503)

    @app.get('/ops/metrics',include_in_schema=False)
    def ops_metrics(request:Request):
        allow_ops(request)
        return m.summary()

    return m
