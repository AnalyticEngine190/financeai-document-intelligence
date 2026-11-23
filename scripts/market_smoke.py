"""Smoke the market feature without ever touching the 50-question gold.

--offline runs mock-provider checks; --live spends Twelve Data credits.
"""
from __future__ import annotations

import argparse
import os
import sys
import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--offline", action="store_true")
    g.add_argument("--live", action="store_true")
    parser.add_argument("--api-url", default=os.getenv("FINANCE_AI_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--exchange", default="NASDAQ")
    args = parser.parse_args()
    if args.offline:
        from app.market_data import MarketClient
        def handler(request):
            if request.url.path == "/quote":
                return httpx.Response(200, json={
                    "symbol": "DEMO", "exchange": "NASDAQ", "name": "Demo Company",
                    "currency": "USD", "close": "10.25", "datetime": "2026-11-17 16:00:00"})
            return httpx.Response(404, json={"status": "error", "code": 404})
        client = MarketClient(api_key="IN-MEMORY-MOCK", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        row = client.quote("DEMO", "NASDAQ")
        assert row["close"] == "10.25" and row["freshness"] == "latest_available_not_guaranteed_realtime"
        assert client.quote("DEMO", "NASDAQ")["cache_hit"] is True
        print("OFFLINE MARKET SMOKE PASSED: mock HTTPS quote, credentials, provenance, and caching; no real API credits used")
        return
    url = args.api_url.rstrip("/")
    headers = {"X-Market-Token": os.environ["FINANCE_AI_MARKET_ACCESS_TOKEN"]} if os.getenv("FINANCE_AI_MARKET_ACCESS_TOKEN") else {}
    print("LIVE MARKET SMOKE: one quote, one history, plus status; consumes provider credits if uncached")
    try:
        with httpx.Client(timeout=httpx.Timeout(35.0, connect=4.0), follow_redirects=False) as session:
            status = session.get(url + "/market/status", headers=headers)
            status.raise_for_status()
            if not status.json().get("configured"):
                raise RuntimeError("The API has no TWELVE_DATA_API_KEY; restart FastAPI with the variable set")
            quote = session.get(url + "/market/quote", params={"symbol": args.symbol, "exchange": args.exchange}, headers=headers)
            quote.raise_for_status()
            data = quote.json()
            print("QUOTE:", data["symbol"], data["exchange"], data["currency"], data["close"],
                  "provider local:", data["provider_datetime"], "not guaranteed real-time")
            history = session.get(url + "/market/history", params={"symbol": args.symbol,
                "exchange": args.exchange, "interval": "1day", "outputsize": 20}, headers=headers)
            history.raise_for_status()
            bars = history.json()
            print("HISTORY:", bars["symbol"], len(bars["points"]), "bars")
            print("LIVE MARKET SMOKE PASSED; exchange entitlement/freshness require independent confirmation")
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as e:
        print("LIVE MARKET SMOKE FAILED: verify symbol/exchange, API key and provider entitlements.")
        if isinstance(e, httpx.HTTPStatusError):
            try:
                print("Server error code:", e.response.json().get("detail", "unspecified"))
            except (ValueError, AttributeError):
                pass
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
