"""Offline chat smoke test: no live API, Twelve Data, Chroma, or Ollama calls.

Run: python -m scripts.chat_smoke --offline
"""
from __future__ import annotations

import argparse
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import market_chat, market_routes
from app.market_data import MarketError


class StubProvider:
    def search(self, term: str) -> dict:
        choices = [{"symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD", "name": "Apple Inc."}] if term in ("Apple", "AAPL") else []
        return {"candidates": choices}

    def quote(self, symbol: str, exchange: str) -> dict:
        if exchange != "NASDAQ":
            raise MarketError("provider_exchange_mismatch", 409)
        return {"symbol": symbol, "exchange": exchange, "currency": "USD", "close": "10.00",
                "provider_datetime": "2026-11-17", "fetched_at_utc": "2026-11-18T00:00:00+00:00"}


def run_offline() -> None:
    original = market_routes.market_client
    original_doc = market_routes._doc
    market_routes.market_client = StubProvider()
    market_routes._doc = lambda q, s: {"answer": "Mock report result", "supported": True, "sources": []}
    try:
        app = FastAPI()
        app.include_router(market_chat.router)
        with TestClient(app) as client:
            cases = [
                ("What's Apple's latest stock price?", "market_quote"),
                ("What was Nordea's CET1 ratio in Q3 2026?", "document"),
                ("What's Apple stock price?", "market_quote"),
            ]
            for question, kind in cases:
                response = client.post("/chat_v1", json={"question": question})
                assert response.status_code == 200 and response.json()["kind"] == kind, response.text
                print("PASS", kind, "·", question)
            response = client.post("/chat_v1", json={"question": "Apple stock price", "selected_listings": [{"symbol": "AAPL", "exchange": "WRONG"}]})
            assert response.status_code == 200 and response.json()["kind"] == "market_unavailable"
            print("PASS provider exchange mismatch -> safe, human-readable error")
            assert client.post("/chat_v1", json={"question": " "}).status_code == 422
            print("PASS empty question rejected")
    finally:
        market_routes.market_client = original
        market_routes._doc = original_doc
    print("CHAT SMOKE PASSED: no live APIs, no data-provider credits used")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", required=True)
    parser.parse_args()
    run_offline()


if __name__ == "__main__":
    main()
