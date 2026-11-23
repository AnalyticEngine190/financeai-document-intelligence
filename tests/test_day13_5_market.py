"""Offline tests: no live market credits, no writes to reports or Chroma."""
from __future__ import annotations

import json
from decimal import Decimal
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.market_data import MarketClient, MarketError, require_exchange, require_search, require_symbol
from app import market_routes


def provider_data(path, params):
    if path == "/symbol_search":
        term = params["symbol"]
        if term == "DUAL":
            data = [{"symbol": "DUAL", "exchange": "NASDAQ", "instrument_name": "Dual Inc", "instrument_type": "Common Stock"},
                    {"symbol": "DUAL", "exchange": "NYSE", "instrument_name": "Dual Inc", "instrument_type": "Common Stock"}]
        elif term in ("Nordea", "NDA"):
            data = [{"symbol": "NDA", "exchange": "NASDAQ HELSINKI", "instrument_name": "Nordea Bank", "instrument_type": "Common Stock", "currency": "EUR"},
                    {"symbol": "NDA", "exchange": "NASDAQ STOCKHOLM", "instrument_name": "Nordea Bank", "instrument_type": "Common Stock", "currency": "SEK"}]
        else:
            data = [{"symbol": term.upper(), "exchange": "NASDAQ", "instrument_name": term + " Inc", "instrument_type": "Common Stock", "currency": "USD", "country": "United States"},
                    {"symbol": "CRYPT", "exchange": "UNLISTED", "instrument_type": "Digital Currency"}]
        return {"data": data}
    if path == "/quote":
        return {"symbol": params["symbol"], "exchange": params.get("exchange", "NASDAQ"),
                "name": "Demo Corp", "currency": "USD", "close": "193.25",
                "previous_close": "192.25", "percent_change": "0.52",
                "datetime": "2026-11-17 16:00:00", "last_quote_at": 1794950000}
    if path == "/time_series":
        return {"meta": {"symbol": params["symbol"], "exchange": params.get("exchange", "NASDAQ"),
                         "currency": "USD", "name": "Demo Corp"},
                "values": [{"datetime": "2026-11-17", "close": "120"},
                           {"datetime": "2026-11-14", "close": "110"},
                           {"datetime": "2026-11-13", "close": "100"}]}
    raise AssertionError(path)


@pytest.fixture
def raw_transport():
    calls = []
    def handler(request: httpx.Request):
        calls.append(request)
        assert request.url.host == "api.twelvedata.com"
        assert request.url.scheme == "https"
        assert "apikey" not in dict(request.url.params)
        assert request.headers["Authorization"] == "apikey FAKE-TEST-KEY"
        return httpx.Response(200, json=provider_data(request.url.path, dict(request.url.params)))
    return httpx.MockTransport(handler), calls


@pytest.fixture
def client(raw_transport):
    transport, calls = raw_transport
    http = httpx.Client(transport=transport)
    return MarketClient(api_key="FAKE-TEST-KEY", http_client=http), calls


def test_symbols_and_inputs_fail_closed():
    assert require_symbol(" aapl ") == "AAPL"
    assert require_symbol("NDA-FI") == "NDA-FI"
    assert require_exchange("NASDAQ") == "NASDAQ"
    assert require_search(" Nordea   Bank ") == "Nordea Bank"
    for bad in ("", "AAPL/USD", "http://localhost", "AAPL&apikey=stolen", ".ticker"):
        with pytest.raises(MarketError):
            require_symbol(bad)
    for bad in (";rm -rf /", "NAS\nDAQ", "NASDAQ?apikey=bad"):
        with pytest.raises(MarketError):
            require_exchange(bad)
    with pytest.raises(MarketError):
        require_search("Nordea?apikey=secret")


def test_no_key_does_not_issue_network_call(raw_transport):
    http = httpx.Client(transport=raw_transport[0])
    c = MarketClient(api_key="", http_client=http)
    with pytest.raises(MarketError, match="market_provider_not_configured"):
        c.quote("AAPL", "NASDAQ")
    assert not raw_transport[1]


def test_provider_auth_header_never_url(client):
    c, calls = client
    assert c.quote("AAPL", "NASDAQ")["close"] == "193.25"
    assert calls[0].headers["Authorization"] == "apikey FAKE-TEST-KEY"
    assert "FAKE-TEST-KEY" not in str(calls[0].url)


def test_quote_fields_with_timestamp(client):
    c, _ = client
    q = c.quote("AAPL", "NASDAQ")
    assert q["currency"] == "USD"
    assert q["price_type"] == "provider_reported_close"
    assert "not_guaranteed_realtime" in q["freshness"]
    assert q["last_quote_at_utc"].endswith("+00:00")
    assert "api_key" not in str(q)


def test_search_candidates_and_filter_non_equities(client):
    c, _ = client
    rows = c.search("Nordea")["candidates"]
    assert len(rows) == 2
    assert {x["currency"] for x in rows} == {"EUR", "SEK"}
    rows = c.search("AAPL")["candidates"]
    assert len(rows) == 1
    assert rows[0]["instrument_type"] == "Common Stock"


def test_history_sorted_and_has_provenance(client):
    c, _ = client
    h = c.history("AAPL", "NASDAQ")
    assert [r["close"] for r in h["points"]] == ["100", "110", "120"]
    assert h["currency"] == "USD"
    assert "dividends_excluded" in h["price_type"]


def test_invalid_intervals_and_history_ranges(client):
    c, calls = client
    for kwargs in ({"interval": "1min"}, {"outputsize": 1}, {"outputsize": 500}):
        with pytest.raises(MarketError, match="invalid_history_request"):
            c.history("AAPL", "NASDAQ", **kwargs)
    assert calls == []


def test_cached_quote_no_extra_credits(client):
    c, calls = client
    assert c.quote("AAPL", "NASDAQ")["cache_hit"] is False
    assert c.quote("AAPL", "NASDAQ")["cache_hit"] is True
    assert len(calls) == 1


def test_cache_expires(client):
    c, calls = client
    clock = [100.0]
    c.clock = lambda: clock[0]
    c.quote("AAPL", "NASDAQ")
    clock[0] += 76
    assert c.quote("AAPL", "NASDAQ")["cache_hit"] is False
    assert len(calls) == 2


def test_compare_price_returns_uses_overlapping_days(client):
    c, _ = client
    result = c.compare(("AAPL", "NASDAQ"), ("MSFT", "NASDAQ"))
    assert result["start_date"] == "2026-11-13"
    assert Decimal(result["results"][0]["return_percent"]) == 20
    assert "dividends" in result["comparison_type"]


def test_comparison_rejects_same_instrument(client):
    c, calls = client
    with pytest.raises(MarketError, match="comparison_requires_distinct_instruments"):
        c.compare(("AAPL", "NASDAQ"), ("AAPL", "NASDAQ"))
    assert not calls


def test_provider_http_429_blocks_repeat_calls():
    count = []
    def handler(req):
        count.append(req)
        return httpx.Response(429, json={"status": "error", "code": 429})
    c = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MarketError, match="market_provider_rate_limited"):
        c.quote("AAPL")
    with pytest.raises(MarketError, match="market_provider_rate_limited"):
        c.quote("AAPL")
    assert len(count) == 1


def test_provider_json_errors_fail_closed():
    for payload in ({"status":"error", "code":400, "message":"PRIVATE"},
                    {"status":"error", "code":403, "message":"PRIVATE"},
                    {"status":"error", "code":429, "message":"PRIVATE"}):
        c = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=payload))))
        with pytest.raises(MarketError) as ex:
            c.quote("AAPL")
        assert "PRIVATE" not in str(ex.value)


def test_provider_bad_price_or_nan_is_rejected():
    for value in ("NaN", "Inf", "-5", "0", "notprice", None):
        def handler(req):
            d = provider_data(req.url.path, dict(req.url.params))
            d["close"] = value
            return httpx.Response(200, json=d)
        c = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with pytest.raises(MarketError, match="invalid_provider_data"):
            c.quote("AAPL", "NASDAQ")


def test_provider_exchange_mismatch_rejected():
    def handler(req):
        d = provider_data(req.url.path, dict(req.url.params))
        d["exchange"] = "NYSE"
        return httpx.Response(200, json=d)
    c = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MarketError, match="provider_exchange_mismatch"):
        c.quote("AAPL", "NASDAQ")


def test_bad_history_cannot_invent_series():
    def handler(req):
        d = provider_data(req.url.path, dict(req.url.params))
        d["values"][0]["close"] = "Infinity"
        return httpx.Response(200, json=d)
    c = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MarketError, match="invalid_provider_data"):
        c.history("AAPL", "NASDAQ")


@pytest.fixture
def routes_api(client, monkeypatch):
    monkeypatch.setattr(market_routes, "market_client", client[0])
    app = FastAPI()
    app.include_router(market_routes.router)
    return TestClient(app)


def test_routes_quote_and_history(routes_api):
    response = routes_api.get("/market/quote", params={"symbol": "AAPL", "exchange": "NASDAQ"})
    assert response.status_code == 200
    assert response.json()["close"] == "193.25"
    response = routes_api.get("/market/history", params={"symbol": "AAPL", "exchange": "NASDAQ"})
    assert response.status_code == 200
    assert len(response.json()["points"]) == 3


def test_market_status_never_leaks_key(routes_api):
    response = routes_api.get("/market/status")
    assert response.json()["configured"] is True
    assert "FAKE-TEST-KEY" not in response.text


def test_routes_restrict_remote_by_default(routes_api, monkeypatch):
    monkeypatch.setattr(market_routes.os, "getenv", lambda name, default="": "" if name == "FINANCE_AI_MARKET_ACCESS_TOKEN" else default)
    # TestClient uses host='testclient' and is allowed. Direct gate with simulated remote request.
    class Remote:
        headers={}
        client=type("Client", (), {"host":"198.51.100.77"})()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ex:
        market_routes._gate(Remote())
    assert ex.value.status_code == 403


def test_routes_access_token_required(client, monkeypatch):
    monkeypatch.setenv("FINANCE_AI_MARKET_ACCESS_TOKEN", "SECRET")
    app = FastAPI()
    app.include_router(market_routes.router)
    client_http = TestClient(app)
    assert client_http.get("/market/quote?symbol=AAPL").status_code == 403
    assert client_http.get("/market/status", headers={"X-Market-Token": "SECRET"}).status_code == 200
    assert "SECRET" not in client_http.get("/market/status", headers={"X-Market-Token": "SECRET"}).text


def test_ask_v3_plain_document_delegates(routes_api, monkeypatch):
    monkeypatch.setattr(market_routes, "_doc", lambda q, s: {"answer":"17.0%", "supported":True,
                                                             "sources":[{"document":"PDF", "page":2}]})
    result = routes_api.post("/ask_v3", json={"question":"What was Nordea's CET1 ratio Q3 2026?"})
    assert result.status_code == 200
    assert result.json()["kind"] == "document"
    assert result.json()["answer"] == "17.0%"


def test_ask_v3_ticker_quote_no_exchange_unique_search(routes_api):
    response = routes_api.post("/ask_v3", json={"question":"AAPL stock price"})
    assert response.status_code == 200
    assert response.json()["kind"] == "market_quote"
    assert response.json()["market"]["currency"] == "USD"
    assert "not guaranteed live" in response.json()["answer"].lower()


def test_ask_v3_company_name_disambiguates(routes_api):
    response = routes_api.post("/ask_v3", json={"question":"What is Nordea's share price?"})
    obj = response.json()
    assert obj["kind"] == "market_clarification"
    assert obj["needs_clarification"]
    assert len(obj["candidates"]) == 2


def test_ask_v3_ambiguous_duplicate_ticker_disambiguates(routes_api):
    response = routes_api.post("/ask_v3", json={"question":"DUAL stock price"})
    assert response.json()["supported"] is False
    assert len(response.json()["candidates"]) == 2


def test_ask_v3_history_returns_chart_data(routes_api):
    obj = routes_api.post("/ask_v3", json={"question":"AAPL stock chart"}).json()
    assert obj["kind"] == "market_history"
    assert len(obj["market"]["points"]) == 3


def test_ask_v3_combines_two_sources_but_no_conflation(routes_api, monkeypatch):
    def fake_doc(q, sel):
        assert q == "What was the Q3 CET1 ratio?"
        assert sel.mode == "manual" and sel.document_ids == ["DOC123"]
        return {"answer":"17.0%", "supported":True, "sources":[{"document":"Bank.pdf", "page":2, "document_id":"DOC123"}]}
    monkeypatch.setattr(market_routes, "_doc", fake_doc)
    payload={"question":"What is AAPL stock price?", "market_symbol":"AAPL", "market_exchange":"NASDAQ",
             "document_question":"What was the Q3 CET1 ratio?",
             "document_selection":{"mode":"manual", "document_ids":["DOC123"]}}
    response=routes_api.post("/ask_v3",json=payload)
    data=response.json()
    assert response.status_code==200
    assert data["kind"] == "combined"
    assert data["supported"] is True
    assert len(data["sources"]) == 2
    assert "different timestamps" in data["answer"]


def test_ask_v3_partial_document_abstention(routes_api, monkeypatch):
    monkeypatch.setattr(market_routes, "_doc", lambda q, s: {"supported":False, "answer":"Cannot verify", "sources":[]})
    obj=routes_api.post("/ask_v3", json={"question":"AAPL stock price", "market_symbol":"AAPL",
                                              "market_exchange":"NASDAQ", "document_question":"Q3 ratio?"}).json()
    assert obj["supported"] is False
    assert "not verified" in obj["answer"]
    assert len(obj["sources"]) == 1


def test_market_compare_endpoint(routes_api):
    response=routes_api.post("/market/compare", json={"first":{"symbol":"AAPL", "exchange":"NASDAQ"},
                                                        "second":{"symbol":"MSFT", "exchange":"NASDAQ"}})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


def test_invalid_quote_safe_http_error(routes_api):
    response = routes_api.get("/market/quote?symbol=../../etc")
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_symbol"


def test_question_empty_rejected(routes_api):
    response = routes_api.post("/ask_v3", json={"question":"   "})
    assert response.status_code == 422


def test_market_secrets_not_in_route_spec(routes_api):
    spec=routes_api.get("/openapi.json").json()
    raw=json.dumps(spec)
    assert "TWELVE_DATA_API_KEY" not in raw
    assert "FAKE-TEST-KEY" not in raw

@pytest.mark.parametrize("code", [301, 302, 307, 400, 500, 502])
def test_upstream_error_and_redirect_never_followed(code):
    seen = []
    def handler(request):
        seen.append(request.url)
        return httpx.Response(code, headers={"Location": "http://169.254.169.254/metadata"}, text="sensitive data")
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MarketError, match="market_provider_unavailable"):
        client.quote("AAPL", "NASDAQ")
    assert len(seen) == 1
    assert seen[0].host == "api.twelvedata.com"


def test_upstream_malformed_json_does_not_return_payload():
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"<PRIVATE>internal secret</PRIVATE>"))))
    with pytest.raises(MarketError, match="invalid_provider_data") as ex:
        client.quote("AAPL", "NASDAQ")
    assert "internal secret" not in str(ex.value)


def test_missing_currency_and_timestamp_are_rejected():
    for remove in ("currency", "datetime", "exchange"):
        def handler(req):
            d = provider_data(req.url.path, dict(req.url.params))
            d.pop(remove)
            return httpx.Response(200, json=d)
        client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with pytest.raises(MarketError, match="invalid_provider_data"):
            client.quote("AAPL", "NASDAQ")


def test_ask_v3_missing_symbol_returns_clarification_without_network(routes_api, client):
    original_calls = len(client[1])
    obj = routes_api.post("/ask_v3", json={"question":"Show me the latest stock price"}).json()
    assert obj["needs_clarification"] is True
    assert obj["sources"] == []
    assert len(client[1]) == original_calls


def test_provider_200_rate_limit_cooldown():
    seen=[]
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"status":"error", "code":429, "message":"You have exceeded limit"})
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    for _ in range(2):
        with pytest.raises(MarketError, match="market_provider_rate_limited"):
            client.quote("AAPL", "NASDAQ")
    assert len(seen) == 1


def test_same_symbol_exchange_case_insensitive_rejected(client):
    c, calls = client
    with pytest.raises(MarketError, match="comparison_requires_distinct_instruments"):
        c.compare(("aapl", "NASDAQ"), ("AAPL", "nasdaq"))
    assert not calls
