"""Regression tests for company-name parsing and provider-verified Nordea lookup."""
from decimal import Decimal
import pytest
from app import market_chat, market_routes
from app.market_data import MarketError


class FakeMarketClient:
    def __init__(self):
        self.search_terms = []
        self.quoted = []

    def search(self, term):
        self.search_terms.append(term)
        rows = {
            "Nordea": [("NDA", "NASDAQ HELSINKI", "Nordea Bank Abp"),
                       ("NDA", "NASDAQ STOCKHOLM", "Nordea Bank Abp")],
            "Nordea Bank Abp": [("NDA", "NASDAQ HELSINKI", "Nordea Bank Abp"),
                                ("NDA", "NASDAQ STOCKHOLM", "Nordea Bank Abp")],
            "NDA": [("NDA", "NASDAQ HELSINKI", "Nordea Bank Abp"),
                    ("NDA", "NASDAQ STOCKHOLM", "Nordea Bank Abp")],
        }.get(term, [])
        return {"candidates": [{"symbol": symbol, "exchange": exchange, "name": name,
                                "currency": "EUR" if exchange.endswith("HELSINKI") else "SEK"}
                               for symbol, exchange, name in rows]}

    def quote(self, symbol, exchange):
        self.quoted.append((symbol, exchange))
        return {"symbol": symbol, "exchange": exchange, "company_name": "Nordea Bank Abp",
                "currency": "EUR" if exchange.endswith("HELSINKI") else "SEK",
                "close": "16.80", "provider_datetime": "2026-11-17",
                "fetched_at_utc": "2026-11-18T12:00:00+00:00"}

    def history(self, symbol, exchange, *, outputsize):
        return {"symbol": symbol, "exchange": exchange, "currency": "EUR", "points": [],
                "fetched_at_utc": "2026-11-18T12:00:00+00:00"}

    def compare(self, a, b, *, outputsize):
        return {"start_date": "2026-11-01", "end_date": "2026-11-17", "results": []}


@pytest.fixture
def service(monkeypatch):
    fake = FakeMarketClient()
    monkeypatch.setattr(market_routes, "market_client", fake)
    monkeypatch.setattr(market_chat.legacy, "_doc", lambda q, selection: {
        "answer": "CET1 was 15.8% in Q3 2026.", "supported": True,
        "sources": [{"document": "Nordea Factbook.pdf", "page": 7}],
        "needs_clarification": False})
    return fake


def test_user_wording_extracts_only_nordea_company_name():
    question = "What's Nordea's latest available stock price and what was its CET1 ratio in Q3 2026?"
    assert market_chat._intent(question) == "combined"
    assert market_chat._entities(question, "combined") == ["Nordea"]
    assert market_chat._search_terms_for("Nordea") == ["Nordea", "Nordea Bank Abp", "NDA"]


def test_nordea_company_name_returns_provider_candidates_and_preserves_report(service, monkeypatch):
    """The exact user's phrase should resolve using actual provider results, not a guessed exchange."""
    question = "What's Nordea's latest available stock price and what was its CET1 ratio in Q3 2026?"
    result = market_chat.process_chat(market_chat.ChatRequest(question=question))
    assert result["kind"] == "market_clarification"
    assert result["needs_clarification"] is True
    assert {x["exchange"] for x in result["candidates"]} == {"NASDAQ HELSINKI", "NASDAQ STOCKHOLM"}
    assert result["document"]["supported"] is True
    assert "CET1 was 15.8%" in result["document"]["answer"]
    assert any(source.get("document") == "Nordea Factbook.pdf" for source in result["sources"])
    assert service.quoted == []  # don't quote until the user chooses one of the listings


def test_nordea_fallback_searches_provider_ticker_when_company_names_return_empty(service, monkeypatch):
    question = "What's Nordea's stock price?"
    original_search = service.search
    terms = []

    def search(term):
        terms.append(term)
        if term in {"Nordea", "Nordea Bank Abp"}:
            return {"candidates": []}
        return original_search(term)

    monkeypatch.setattr(service, "search", search)
    result = market_chat.process_chat(market_chat.ChatRequest(question=question))
    assert terms == ["Nordea", "Nordea Bank Abp", "NDA"]
    assert result["kind"] == "market_clarification"
    assert {candidate["symbol"] for candidate in result["candidates"]} == {"NDA"}
    assert service.quoted == []


def test_selected_provider_listing_completes_combined_question(service):
    question = "What's Nordea's latest available stock price and what was its CET1 ratio in Q3 2026?"
    result = market_chat.process_chat(market_chat.ChatRequest(
        question=question,
        selected_listings=[{"symbol": "NDA", "exchange": "NASDAQ HELSINKI"}],
    ))
    assert result["kind"] == "combined"
    assert result["supported"] is True
    assert result["market"]["symbol"] == "NDA"
    assert result["market"]["exchange"] == "NASDAQ HELSINKI"
    assert result["document"]["supported"] is True
    assert len(result["sources"]) == 2


def test_question_with_another_company_does_not_get_nordea_aliases():
    assert market_chat._search_terms_for("Apple") == ["Apple"]


def test_latest_available_is_removed_only_as_trailing_qualifier():
    assert market_chat._clean_entity("What's Bank of America latest available") == "Bank of America"
