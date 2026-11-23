"""No network, Ollama, index or provider credits are used by these tests."""
from __future__ import annotations

from decimal import Decimal
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import market_chat, market_routes
from app.market_data import MarketError


class FakeMarketClient:
    def __init__(self):
        self.search_terms = []
        self.quoted = []
        self.error = None

    def search(self, term):
        self.search_terms.append(term)
        if self.error == "search":
            raise MarketError("market_provider_unavailable", 503)
        rows = {
            "Apple": [("AAPL", "NASDAQ", "Apple Inc.")],
            "Microsoft": [("MSFT", "NASDAQ", "Microsoft Corp.")],
            "AAPL": [("AAPL", "NASDAQ", "Apple Inc.")],
            "MSFT": [("MSFT", "NASDAQ", "Microsoft Corp.")],
            "Nordea": [("NDA", "NASDAQ HELSINKI", "Nordea Bank"), ("NDA", "NASDAQ STOCKHOLM", "Nordea Bank")],
            "DUAL": [("DUAL", "NASDAQ", "Dual"), ("DUAL", "NYSE", "Dual")],
        }.get(term, [])
        return {"candidates": [{"symbol": s, "exchange": e, "name": n,
                                "currency": "USD" if e == "NASDAQ" else "EUR"} for s, e, n in rows]}

    def quote(self, symbol, exchange):
        self.quoted.append((symbol, exchange))
        if self.error == "quote":
            raise MarketError("market_provider_unavailable", 503)
        if (symbol, exchange) not in [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ"),
                                     ("NDA", "NASDAQ HELSINKI"), ("NDA", "NASDAQ STOCKHOLM")]:
            raise MarketError("provider_exchange_mismatch", 409)
        return {"symbol": symbol, "exchange": exchange, "company_name": symbol,
                "currency": "USD" if exchange == "NASDAQ" else "EUR", "close": "12.5000",
                "provider_datetime": "2026-11-17", "fetched_at_utc": "2026-11-18T08:00:00+00:00",
                "percent_change": "0.50"}

    def history(self, symbol, exchange, *, outputsize):
        return {"symbol": symbol, "exchange": exchange, "currency": "USD",
                "points": [{"date": "2026-11-13", "close": "10"},
                           {"date": "2026-11-16", "close": "11"},
                           {"date": "2026-11-17", "close": "12"}],
                "fetched_at_utc": "2026-11-18T08:00:00+00:00"}

    def compare(self, a, b, *, outputsize):
        return {"start_date": "2026-11-13", "end_date": "2026-11-17",
                "results": [{"symbol": a[0], "exchange": a[1], "currency": "USD", "return_percent": "20"},
                            {"symbol": b[0], "exchange": b[1], "currency": "USD", "return_percent": "20"}]}


@pytest.fixture
def service(monkeypatch):
    fake = FakeMarketClient()
    monkeypatch.setattr(market_routes, "market_client", fake)
    monkeypatch.setattr(market_chat.legacy, "_doc", lambda q, selection: {
        "answer": "CET1 was 15.8% in Q3 2026.", "supported": True,
        "sources": [{"document": "Nordea Factbook.pdf", "page": 40}],
        "needs_clarification": False})
    return fake


def ask(q, **kw):
    return market_chat.process_chat(market_chat.ChatRequest(question=q, **kw))


def test_report_question_never_uses_market_provider(service):
    out = ask("What was Nordea's CET1 ratio in Q3 2026?")
    assert out["kind"] == "document" and out["supported"]
    assert service.search_terms == [] and service.quoted == []


def test_stock_price_ticker_resolves_exchange_before_quote(service):
    out = ask("What is AAPL's latest stock price?")
    assert out["kind"] == "market_quote" and out["market"]["close"] == "12.5000"
    assert service.search_terms == ["AAPL"] and service.quoted == [("AAPL", "NASDAQ")]
    assert out["sources"][0]["provider_datetime"] == "2026-11-17"


def test_stock_price_company_name(service):
    out = ask("What's Apple's latest stock price?")
    assert out["kind"] == "market_quote" and service.search_terms == ["Apple"]


def test_ambiguous_bank_returns_choices_not_guess(service):
    out = ask("What's Nordea's stock price?")
    assert out["kind"] == "market_clarification" and out["needs_clarification"]
    assert len(out["candidates"]) == 2 and not service.quoted


def test_click_exchange_listing_only_then_quote(service):
    q = "What's Nordea's stock price?"
    out = ask(q, selected_listings=[{"symbol": "NDA", "exchange": "NASDAQ HELSINKI"}])
    assert out["kind"] == "market_quote"
    assert service.quoted == [("NDA", "NASDAQ HELSINKI")]


def test_invalid_exchange_fails_gracefully_without_price(service):
    out = ask("What's AAPL stock price?", selected_listings=[{"symbol": "AAPL", "exchange": "NASDAW"}])
    assert out["kind"] == "market_unavailable" and out["supported"] is False
    assert "12.5000" not in out["answer"]
    assert out["error_code"] == "provider_exchange_mismatch"


def test_provider_down_is_friendly_not_http503(service):
    service.error = "quote"
    out = ask("What's AAPL stock price?")
    assert out["kind"] == "market_unavailable" and not out["supported"]
    assert "market_provider_unavailable" not in out["answer"]


def test_provider_search_down_is_friendly(service):
    service.error = "search"
    out = ask("What's AAPL stock price?")
    assert out["kind"] == "market_unavailable" and not out["supported"]


def test_history_only_when_asked(service):
    out = ask("Show AAPL stock performance over the past 3 months")
    # The fake provider returns only four calendar days of evidence. The
    # assistant must not label that as a complete three-month request.
    assert out["kind"] == "market_unavailable" and not out["supported"]
    assert "full requested calendar window" in out["answer"]
    assert service.quoted == []


def test_comparison_tickers_with_chart(service):
    out = ask("Compare AAPL and MSFT stock performance over six months")
    assert out["kind"] == "market_compare" and out["supported"]
    assert len(out["market"]["chart_points"]) == 3
    assert Decimal(out["market"]["chart_points"][0]["first_pct"]) == 0


def test_comparison_named_companies(service):
    out = ask("Compare Apple and Microsoft stock performance")
    assert out["kind"] == "market_compare" and out["supported"]


def test_comparison_with_missing_second_entity_is_clarification(service):
    out = ask("Compare AAPL stock performance")
    assert out["kind"] == "market_clarification" and not service.quoted


def test_report_comparison_is_document_not_market(service):
    out = ask("Compare Nordea and SEB CET1 ratios")
    assert out["kind"] == "document"
    assert service.search_terms == []


def test_mixed_data_separate_sources_and_support(service):
    q = "What's Nordea's stock price and its CET1 ratio in Q3 2026?"
    out = ask(q, selected_listings=[{"symbol": "NDA", "exchange": "NASDAQ HELSINKI"}])
    assert out["kind"] == "combined" and out["supported"]
    assert len(out["sources"]) == 2
    assert out["sources"][1]["document"] == "Nordea Factbook.pdf"


def test_mixed_price_failure_keeps_report_portion(service):
    service.error = "quote"
    out = ask("What's Nordea's stock price and its CET1 ratio in Q3 2026?", selected_listings=[{"symbol": "NDA", "exchange": "NASDAQ HELSINKI"}])
    assert out["kind"] == "combined" and out["partial"]
    assert "CET1 was 15.8%" in out["answer"] and not out["supported"]


def test_no_price_fabrication_if_symbol_not_found(service):
    out = ask("What's Nonexistent's stock price?")
    assert out["needs_clarification"] and not out["supported"] and not service.quoted


def test_unrelated_report_word_share_of_profit_does_not_trigger_market(service):
    out = ask("What was Handelsbanken's share of profit of associates in Q3 2026?")
    assert out["kind"] == "document" and service.search_terms == []


def test_endpoint_returns_200_on_provider_outage_and_validation_still_works(service):
    app = FastAPI()
    app.include_router(market_chat.router)
    client = TestClient(app)
    service.error = "quote"
    response = client.post("/chat_v1", json={"question": "What is AAPL stock price?"})
    assert response.status_code == 200
    assert response.json()["kind"] == "market_unavailable"
    assert client.post("/chat_v1", json={"question": " "}).status_code == 422


def test_chosen_exchange_not_silently_replaced(service):
    out = ask("What's Nordea's stock price?", selected_listings=[{"symbol": "NDA", "exchange": "NASDAQ STOCKHOLM"}])
    assert out["kind"] == "market_quote" and service.quoted == [("NDA", "NASDAQ STOCKHOLM")]


def test_source_restriction_preserved_in_doc_call(service, monkeypatch):
    captured = []
    monkeypatch.setattr(market_chat.legacy, "_doc", lambda question, selection: captured.append(selection) or {"answer": "ok", "supported": True, "sources": []})
    doc = market_chat.legacy.DocumentSelectionRequest(mode="manual", document_ids=["abc123"])
    out = ask("What is the CET1 ratio?", document_selection=doc)
    assert out["kind"] == "document" and captured[0].document_ids == ["abc123"]


def test_numerical_output_is_not_hardcoded_in_source(service):
    assert "351.91" not in market_chat.__doc__
    assert "market_provider_unavailable" in market_chat._friendly_error(MarketError("market_provider_unavailable"))["error_code"]


def test_spaces_only_exchange_is_not_silently_assumed(service):
    out = ask("AAPL stock price", selected_listings=[{"symbol": "AAPL", "exchange": "  "}])
    assert out["kind"] == "market_unavailable" and not out["supported"]
    assert service.quoted == []


def test_market_history_does_not_claim_realtime(service):
    out = ask("Show AAPL stock history")
    assert "not guaranteed live" in out["answer"]
    assert out["sources"][0]["provider"] == "Twelve Data"


def test_non_market_report_question_with_price_word_not_stock(service):
    out = ask("What was the report's cost-to-income ratio in Q3 2026?")
    assert out["kind"] == "document" and service.search_terms == []


def test_market_access_requires_auth_or_loopback(service, monkeypatch):
    app = FastAPI()
    app.include_router(market_chat.router)
    monkeypatch.setenv("FINANCE_AI_MARKET_ACCESS_TOKEN", "secret-token")
    with TestClient(app) as client:
        assert client.post("/chat_v1", json={"question": "AAPL stock price"}).status_code == 403
        response = client.post("/chat_v1", headers={"X-Market-Token": "secret-token"}, json={"question": "AAPL stock price"})
        assert response.status_code == 200
        assert response.json()["kind"] == "market_quote"
