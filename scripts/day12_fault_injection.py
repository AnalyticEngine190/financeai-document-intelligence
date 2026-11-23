"""Safe fault-injection harness: simulated transport and validation failures only.

No writes to Chroma, registry, model, gold, or any live PDF. By default
runs entirely in-process; use --live to run only harmless API validation.
"""
import argparse,io,json,urllib.error,urllib.request
from unittest import mock

class FakeResponse:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*_):return None
    def read(self):return self.data


def classify(fn):
    try:r=fn();return 'ok' if isinstance(r,dict) else 'invalid_payload'
    except urllib.error.HTTPError as e:return f'http_{e.code}'
    except TimeoutError:return 'timeout'
    except (ConnectionRefusedError,ConnectionError,urllib.error.URLError):return 'unavailable'
    except (json.JSONDecodeError,UnicodeDecodeError):return 'invalid_json'
    except Exception:return 'unknown_failure'


def run_offline():
    probes={'healthy':(lambda:{'supported':True},'ok'),
            'api_down':(lambda:(_ for _ in ()).throw(ConnectionRefusedError('offline')),'unavailable'),
            'timeout':(lambda:(_ for _ in ()).throw(TimeoutError()),'timeout'),
            'http_503':(lambda:(_ for _ in ()).throw(urllib.error.HTTPError('http://localhost',503,'unavailable',{},None)),'http_503'),
            'invalid_json':(lambda:json.loads('{broken'),'invalid_json'),
            'invalid_payload':(lambda:['not a dict'],'invalid_payload')}
    for name,(call,want) in probes.items():
        got=classify(call)
        assert got==want,(name,got,want)
        print('PASS',name,'->',got)
    print('SAFE FAULT INJECTION: 6 cases; no live index touched')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--offline',action='store_true',default=True)
    p.parse_args();run_offline()
