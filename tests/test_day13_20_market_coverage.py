from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import market_chat, market_routes
from app.market_data import MarketClient, MarketError
from app.market_entitlements import classify_listing, status_payload


def test_basic_default_us_feed_allows_aapl():
    profile = classify_listing("AAPL", "NASDAQ", name="Apple Inc.", country="United States")
    assert profile["allowed"] is True
    assert profile["entitlement_label"] == "Default US equity feed"


def test_basic_blocks_nordea_primary_hel_listing_as_pro():
    profile = classify_listing("NDA", "NASDAQ HELSINKI", name="Nordea Bank Abp", country="Finland")
    assert profile["allowed"] is False
    assert profile["reason_code"] == "market_plan_restricted"
    assert profile["required_plan"] == "pro"
    assert "Basic" in profile["message"] and "Pro" in profile["message"]


def test_pro_configuration_allows_nordic_exchange(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PLAN", "pro")
    profile = classify_listing("NDA", "NASDAQ STOCKHOLM", name="Nordea Bank Abp", country="Sweden")
    assert profile["allowed"] is True


def test_nbnkf_is_otc_ordinary_not_nordea_primary_listing():
    profile = classify_listing("NBNKF", "OTC Markets OTCQX", name="Nordea Bank Abp")
    assert profile["allowed"] is False
    assert profile["reason_code"] == "market_otc_license_required"
    assert profile["security_type"] == "ordinary_shares"
    assert "non-primary" in profile["security_label"]
    assert "OTC" in profile["message"] and "separate licence" in profile["message"]
    assert "17.91" not in profile["message"]  # no hardcoded market price


def test_nrdby_is_distinguished_as_a_sponsored_adr():
    profile = classify_listing("NRDBY", "OTC Markets", name="Nordea Bank Abp")
    assert profile["allowed"] is False
    assert profile["security_type"] == "sponsored_adr"
    assert "1 ADR = 1 ordinary share" in profile["security_label"]


def test_otc_is_blocked_even_when_plan_is_pro(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PLAN", "pro")
    monkeypatch.delenv("TWELVE_DATA_ALLOW_OTC", raising=False)
    profile = classify_listing("NBNKF", "OTC", name="Nordea Bank Abp")
    assert profile["allowed"] is False
    assert profile["reason_code"] == "market_otc_license_required"


def test_otc_can_only_be_enabled_explicitly_after_separate_licence(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_ALLOW_OTC", "1")
    profile = classify_listing("NBNKF", "OTC", name="Nordea Bank Abp")
    assert profile["allowed"] is True
    assert profile["entitlement_label"] == "OTC access explicitly enabled"


def test_market_status_explains_plan_is_not_auto_detected(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PLAN", "basic")
    status = status_payload()
    assert status["configured_plan"] == "basic"
    assert status["plan_auto_detected"] is False
    assert "XHEL" not in status["coverage_note"]  # prose names the Nordic exchanges
    assert "Pro" in status["coverage_note"] and "separate licence" in status["coverage_note"]


def test_market_client_blocks_otc_before_spending_provider_credit(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MarketError, match="market_otc_license_required"):
        client.quote("NBNKF", "OTC")
    assert calls == []


def test_market_client_blocks_nordic_before_spending_provider_credit(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MarketError, match="market_plan_restricted"):
        client.history("NDA", "NASDAQ HELSINKI")
    assert calls == []


def test_market_client_retries_transient_503_once():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"status": "error"})
        return httpx.Response(200, json={
            "symbol": "AAPL", "exchange": "NASDAQ", "name": "Apple Inc.",
            "currency": "USD", "close": "250.10", "previous_close": "249.50",
            "percent_change": "0.24", "datetime": "2026-11-20", "last_quote_at": 1795200000,
        })
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                          sleep=lambda _delay: None)
    result = client.quote("AAPL", "NASDAQ")
    assert result["close"] == "250.10"
    assert len(calls) == 2


def test_market_client_does_not_retry_rate_limit():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"status": "error", "code": 429})
    client = MarketClient(api_key="FAKE", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                          sleep=lambda _delay: None)
    with pytest.raises(MarketError, match="market_provider_rate_limited"):
        client.quote("AAPL", "NASDAQ")
    assert len(calls) == 1


def test_chat_reports_explicit_basic_plan_limitation():
    error = MarketError("market_plan_restricted", 403)
    answer = market_chat._friendly_error(error)["answer"]
    assert "Basic" in answer and "US listed-equity feed" in answer and "Pro" in answer


def test_chat_reuses_user_confirmed_listing_without_search(monkeypatch):
    class FakeMarket:
        search_terms = []
        quoted = []
        def search(self, term):
            self.search_terms.append(term)
            return {"candidates": []}
        def quote(self, symbol, exchange):
            self.quoted.append((symbol, exchange))
            return {"symbol": symbol, "exchange": exchange, "company_name": "Apple Inc.",
                    "currency": "USD", "close": "250.10", "provider_datetime": "2026-11-20",
                    "fetched_at_utc": "2026-11-21T09:00:00+00:00", "percent_change": "0.24"}
    fake = FakeMarket()
    monkeypatch.setattr(market_routes, "market_client", fake)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="What's Apple's latest stock price?",
        remembered_listings={"apple": {"symbol": "AAPL", "exchange": "NASDAQ", "entity_key": "apple"}},
    ))
    assert result["kind"] == "market_quote"
    assert fake.search_terms == []
    assert fake.quoted == [("AAPL", "NASDAQ")]


def test_combined_query_keeps_pdf_answer_if_market_plan_blocks(monkeypatch):
    class RestrictedMarket:
        def search(self, term):
            return {"candidates": [{
                "symbol": "NBNKF", "exchange": "OTC", "name": "Nordea Bank Abp", "currency": "USD",
                "entitlement": {"allowed": False, "reason_code": "market_otc_license_required",
                                "message": "Twelve Data Basic excludes this OTC security; a separate OTC licence is required."},
            }]}
    monkeypatch.setattr(market_routes, "market_client", RestrictedMarket())
    monkeypatch.setattr(market_chat.legacy, "_doc", lambda question, selection: {
        "answer": "Nordea CET1 ratio in Q3 2026: 15.8%.", "supported": True,
        "sources": [{"document": "Nordea Q3 2026 factbook.pdf", "page": 7}],
        "needs_clarification": False,
    })
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="What's Nordea's stock price and its CET1 ratio in Q3 2026?"))
    assert result["kind"] == "combined"
    assert result["error_code"] == "market_plan_restricted"
    assert "Basic subscription" in result["market_message"]
    assert "US listed-equity feed" in result["market_message"]
    assert "OTC securities" in result["market_message"]
    assert result["document"]["supported"] is True
    assert "15.8%" in result["answer"] and "15.8%" in result["document"]["answer"]
    assert result["sources"][0]["page"] == 7


def test_streamlit_history_charts_pin_axis_to_observation_dates():
    from pathlib import Path
    source = Path("streamlit_market_app.py").read_text(encoding="utf-8")
    assert "scale=alt.Scale(domain=domain, nice=False, clamp=True)" in source
    assert "Observed dates:" in source
    assert "Shared observations:" in source


def test_streamlit_listing_memory_is_session_scoped_and_reset_on_new_conversation():
    from pathlib import Path
    source = Path("streamlit_market_app.py").read_text(encoding="utf-8")
    assert 'st.session_state.market_listing_memory = {}' in source
    assert '"remembered_listings": st.session_state.get("market_listing_memory", {})' in source


def test_nordea_basic_no_provider_candidates_still_explains_plan(monkeypatch):
    class EmptyMarket:
        def search(self, term):
            return {"candidates": []}
    monkeypatch.setenv("TWELVE_DATA_PLAN", "basic")
    monkeypatch.setattr(market_routes, "market_client", EmptyMarket())
    result = market_chat.process_chat(market_chat.ChatRequest(question="What's Nordea's latest stock price?"))
    assert result["kind"] == "market_unavailable"
    assert result["error_code"] == "market_plan_restricted"
    assert "Basic subscription" in result["answer"]
    assert "US listed-equity feed" in result["answer"]
    assert "Nasdaq Helsinki" in result["answer"]
    assert "separate data licence" in result["answer"]


def test_nordea_alias_search_continues_after_restricted_otc_candidate(monkeypatch):
    calls = []
    class RestrictedThenPrimaryMarket:
        def search(self, term):
            calls.append(term)
            if term in {"Nordea", "Nordea Bank Abp"}:
                row = {"symbol": "NBNKF", "exchange": "OTC", "name": "Nordea Bank Abp",
                       "entitlement": {"allowed": False, "reason_code": "market_otc_license_required"}}
            else:
                row = {"symbol": "NDA", "exchange": "NASDAQ HELSINKI", "name": "Nordea Bank Abp",
                       "entitlement": {"allowed": False, "reason_code": "market_plan_restricted"}}
            return {"candidates": [row]}
    monkeypatch.setenv("TWELVE_DATA_PLAN", "basic")
    monkeypatch.setattr(market_routes, "market_client", RestrictedThenPrimaryMarket())
    candidates = market_chat._lookup_term("Nordea")
    assert calls == ["Nordea", "Nordea Bank Abp", "NDA"]
    assert {(x["symbol"], x["exchange"]) for x in candidates} == {
        ("NBNKF", "OTC"), ("NDA", "NASDAQ HELSINKI")
    }


def test_nordea_saved_otc_selection_is_not_reused_on_basic(monkeypatch):
    class EmptyMarket:
        def __init__(self):
            self.calls = []
        def search(self, term):
            self.calls.append(term)
            return {"candidates": []}
    fake = EmptyMarket()
    monkeypatch.setenv("TWELVE_DATA_PLAN", "basic")
    monkeypatch.setattr(market_routes, "market_client", fake)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="What's Nordea's latest stock price?",
        remembered_listings={"nordea": {"symbol": "NBNKF", "exchange": "OTC", "entity_key": "nordea"}},
    ))
    assert result["error_code"] == "market_plan_restricted"
    assert fake.calls  # stale remembered OTC choice was discarded and resolution retried


def test_basic_plan_message_never_embeds_historical_quote_value():
    from app.market_entitlements import nordea_basic_coverage_message, otc_message
    text = nordea_basic_coverage_message() + otc_message("NBNKF")
    assert "17.91" not in text
    assert "-2.86" not in text


def test_streamlit_combined_error_renders_report_separately():
    from pathlib import Path
    source = Path("streamlit_market_app.py").read_text(encoding="utf-8")
    assert 'st.warning(result.get("market_message") or answer)' in source
    assert 'doc.get("answer") or "No verified report answer."' in source


def test_basic_plan_message_is_explicit_in_sidebar_copy():
    from pathlib import Path
    source = Path("streamlit_market_app.py").read_text(encoding="utf-8")
    assert "Basic coverage in this app: default US listed-equity feed" in source
    assert "Nordic primary exchanges require Pro" in source


def test_non_us_venue_is_not_allowed_just_because_issuer_country_is_us():
    profile = classify_listing("USCO", "LONDON STOCK EXCHANGE", name="US Company plc",
                               country="United States")
    assert profile["allowed"] is False


def test_pro_plan_error_does_not_misstate_account_as_basic(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PLAN", "pro")
    answer = market_chat._friendly_error(MarketError("market_plan_restricted", 403))["answer"]
    assert "Pro" in answer
    assert "configured for Twelve Data Pro" in answer
    assert "Basic subscription" not in answer


def test_otc_message_explains_basic_us_feed_and_separate_license(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PLAN", "basic")
    answer = classify_listing("NBNKF", "OTC", name="Nordea Bank Abp")["message"]
    assert "Basic subscription" in answer
    assert "US-listed equities" in answer
    assert "separate licence" in answer
    assert "primary" in answer.casefold()


def test_capabilities_answer_does_not_claim_basic_when_configured_pro(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PLAN", "pro")
    result = market_chat.process_chat(market_chat.ChatRequest(question="What can you do?"))
    assert "configured for Twelve Data Pro" in result["answer"]
    assert "Basic default feed" not in result["answer"]


def test_streamlit_chart_uses_only_observed_dates_for_axis_ticks():
    from pathlib import Path
    source = Path("streamlit_market_app.py").read_text(encoding="utf-8")
    assert "def _actual_date_ticks" in source
    assert "axis=alt.Axis(values=ticks" in source
    assert "clamp=True" in source


def test_provider_plan_is_explicitly_not_auto_detected_in_sidebar():
    from pathlib import Path
    source = Path("streamlit_market_app.py").read_text(encoding="utf-8")
    assert "The plan label is set by TWELVE_DATA_PLAN" in source
    assert "not auto-detected" in source
