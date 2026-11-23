"""Strict, optional Twelve Data market-data adapter.

No model guesses, no guessed exchange, no real-time claims, no hardcoded prices.
Credentials live on the server in TWELVE_DATA_API_KEY and are sent via HTTPS
Authorization header, not as query parameters. Tests inject httpx.MockTransport.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any, Callable
import os
import re
import time

import httpx

API_ORIGIN = "https://api.twelvedata.com"
SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$")
SAFE_EXCHANGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._&-]{0,49}$")
SEARCH_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&'()_-]{0,79}$")
INTERVALS = {"1day", "1week", "1month"}


class MarketError(Exception):
    """Client-safe error code; provider text and auth are never propagated."""

    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


def require_symbol(value: str) -> str:
    value = (value or "").strip().upper()
    if not SAFE_SYMBOL.fullmatch(value):
        raise MarketError("invalid_symbol", 422)
    return value


def require_exchange(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not SAFE_EXCHANGE.fullmatch(value):
        raise MarketError("invalid_exchange", 422)
    return value


def require_search(value: str) -> str:
    value = " ".join((value or "").split())
    if not SEARCH_TERM.fullmatch(value):
        raise MarketError("invalid_search", 422)
    return value


def _decimal(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise MarketError("invalid_provider_data")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise MarketError("invalid_provider_data") from None
    if not result.is_finite() or (positive and result <= 0):
        raise MarketError("invalid_provider_data")
    return result


def _optional_decimal(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(_decimal(value))


def _string(value: Any, limit: int = 130) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CacheEntry:
    expires: float
    data: dict


class MarketClient:
    """Single-process fixed-host client; inject HTTP client/clock in offline tests."""

    def __init__(self, *, api_key: str | None = None, http_client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 max_retries: int = 1, retry_delay: float = 0.15):
        self.api_key = api_key if api_key is not None else os.getenv("TWELVE_DATA_API_KEY", "")
        self.http_client = http_client
        self.clock = clock
        self.sleep = sleep
        self.max_retries = max(0, min(int(max_retries), 2))
        self.retry_delay = max(0.0, min(float(retry_delay), 1.0))
        self._lock = RLock()
        self._cache: dict[tuple, CacheEntry] = {}
        self._blocked_until = 0.0

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    def _fetch(self, endpoint: str, params: dict[str, Any], ttl: int) -> dict:
        if endpoint not in {"/quote", "/time_series", "/symbol_search"}:
            raise MarketError("invalid_endpoint", 500)
        if not self.is_configured():
            raise MarketError("market_provider_not_configured", 503)
        key = (endpoint, tuple(sorted((str(k), str(v)) for k, v in params.items())))
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit.expires > now:
                return {**hit.data, "cache_hit": True}
            if now < self._blocked_until:
                raise MarketError("market_provider_rate_limited", 429)

        # All endpoints are idempotent GET requests. Retry only transient
        # transport/5xx failures once; never retry quota, auth, validation, or
        # symbol errors. Credentials remain in the HTTPS Authorization header.
        headers = {"Authorization": "apikey " + self.api_key.strip(), "Accept": "application/json"}
        owned = self.http_client is None
        client = self.http_client or httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0),
                                                   follow_redirects=False)
        transient_statuses = {503, 504}
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = client.get(API_ORIGIN + endpoint, params=params, headers=headers,
                                          timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False)
                except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError):
                    if attempt < self.max_retries:
                        self.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise MarketError("market_provider_unavailable", 503) from None
                except httpx.HTTPError:
                    if attempt < self.max_retries:
                        self.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise MarketError("market_provider_unavailable", 503) from None

                if response.status_code == 429:
                    with self._lock:
                        self._blocked_until = self.clock() + 60
                    raise MarketError("market_provider_rate_limited", 429)
                if response.status_code == 401:
                    raise MarketError("market_provider_authentication_failed", 503)
                if response.status_code == 403:
                    raise MarketError("market_provider_access_denied", 503)
                if response.status_code in transient_statuses:
                    if attempt < self.max_retries:
                        self.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise MarketError("market_provider_unavailable", 503)
                if response.status_code >= 400 or response.status_code < 200 or response.status_code >= 300:
                    raise MarketError("market_provider_unavailable", 503)
                try:
                    data = response.json()
                except (ValueError, TypeError):
                    raise MarketError("invalid_provider_data", 503) from None
                if not isinstance(data, dict):
                    raise MarketError("invalid_provider_data")
                if str(data.get("status", "")).lower() == "error" or data.get("code") is not None:
                    # Twelve Data sometimes returns errors inside HTTP 200 JSON.
                    # Do not propagate provider text or credentials to the caller.
                    code = str(data.get("code", ""))
                    if code == "429":
                        with self._lock:
                            self._blocked_until = self.clock() + 60
                        raise MarketError("market_provider_rate_limited", 429)
                    if code == "401":
                        raise MarketError("market_provider_authentication_failed", 503)
                    if code == "403":
                        raise MarketError("market_provider_access_denied", 503)
                    if code in {"400", "404"}:
                        raise MarketError("market_symbol_unavailable", 404)
                    if code in {"500", "502", "503", "504"}:
                        if attempt < self.max_retries:
                            self.sleep(self.retry_delay * (attempt + 1))
                            continue
                        raise MarketError("market_provider_unavailable", 503)
                    raise MarketError("market_provider_unavailable", 503)

                with self._lock:
                    if len(self._cache) >= 256:
                        oldest = min(self._cache, key=lambda x: self._cache[x].expires)
                        self._cache.pop(oldest)
                    self._cache[key] = CacheEntry(expires=self.clock() + ttl, data=data)
                return {**data, "cache_hit": False}
            raise MarketError("market_provider_unavailable", 503)
        finally:
            if owned:
                client.close()

    def search(self, query: str) -> dict:
        query = require_search(query)
        data = self._fetch("/symbol_search", {"symbol": query, "outputsize": 30}, 3600)
        rows = data.get("data", [])
        if not isinstance(rows, list):
            raise MarketError("invalid_provider_data")
        results: list[dict] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            symbol = _string(raw.get("symbol"), 20).upper()
            exchange = _string(raw.get("exchange"), 50)
            if not SAFE_SYMBOL.fullmatch(symbol) or not exchange or not SAFE_EXCHANGE.fullmatch(exchange):
                continue
            asset_type = _string(raw.get("instrument_type") or raw.get("type"), 55)
            if asset_type and not any(word in asset_type.casefold() for word in
                                      ("stock", "share", "equity", "depositary receipt", "reit")):
                continue
            candidate = {"symbol": symbol, "exchange": exchange,
                         "name": _string(raw.get("instrument_name") or raw.get("name"), 120),
                         "currency": _string(raw.get("currency"), 10),
                         "country": _string(raw.get("country"), 55),
                         "instrument_type": asset_type}
            from app.market_entitlements import classify_listing
            candidate["entitlement"] = classify_listing(
                symbol, exchange, name=candidate["name"], currency=candidate["currency"],
                country=candidate["country"], instrument_type=asset_type,
            )
            results.append(candidate)
            if len(results) >= 20:
                break
        return {"provider": "Twelve Data", "query": query, "candidates": results,
                "cache_hit": bool(data.get("cache_hit"))}

    def quote(self, symbol: str, exchange: str | None = None) -> dict:
        symbol = require_symbol(symbol)
        exchange = require_exchange(exchange)
        from app.market_entitlements import require_listing_entitlement, security_identity
        entitlement = require_listing_entitlement(symbol, exchange or "")
        params: dict[str, Any] = {"symbol": symbol}
        if exchange:
            params["exchange"] = exchange
        raw = self._fetch("/quote", params, 75)
        returned_symbol = _string(raw.get("symbol"), 20).upper()
        if returned_symbol and returned_symbol != symbol:
            raise MarketError("provider_symbol_mismatch")
        close = str(_decimal(raw.get("close"), positive=True))
        currency = _string(raw.get("currency"), 10).upper()
        reported_exchange = _string(raw.get("exchange"), 50)
        if not currency or not reported_exchange:
            raise MarketError("invalid_provider_data")
        if exchange and reported_exchange.casefold() != exchange.casefold():
            # A wrong listing is worse than a missing answer.
            raise MarketError("provider_exchange_mismatch", 409)
        local_time = _string(raw.get("datetime"), 40)
        if not local_time or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", local_time):
            raise MarketError("invalid_provider_data")
        unix = raw.get("last_quote_at")
        quoted_utc = None
        if unix not in (None, ""):
            try:
                timestamp = int(unix)
                if not 946684800 <= timestamp <= 4102444800:
                    raise ValueError()
                quoted_utc = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError):
                raise MarketError("invalid_provider_data") from None
        company_name = _string(raw.get("name"), 120)
        # Re-check against the actual venue reported by the provider. When a
        # direct API caller omitted exchange, this prevents an unverified
        # non-US/OTC venue from slipping through a provisional symbol lookup.
        entitlement = require_listing_entitlement(symbol, reported_exchange, name=company_name, currency=currency)
        identity = security_identity(symbol, reported_exchange, company_name)
        return {"provider": "Twelve Data", "symbol": symbol, "exchange": reported_exchange,
                "company_name": company_name, "currency": currency,
                "close": close, "previous_close": _optional_decimal(raw.get("previous_close")),
                "percent_change": _optional_decimal(raw.get("percent_change")),
                "provider_datetime": local_time, "last_quote_at_utc": quoted_utc,
                "fetched_at_utc": _iso_now(), "cache_hit": bool(raw.get("cache_hit")),
                "price_type": "provider_reported_close",
                "freshness": "latest_available_not_guaranteed_realtime",
                "delay": "exchange_entitlement_not_verified",
                "entitlement": entitlement,
                "security_type": identity.get("security_type"),
                "security_label": identity.get("security_label"),
                "security_note": identity.get("security_note")}

    def history(self, symbol: str, exchange: str | None = None,
                *, interval: str = "1day", outputsize: int = 90) -> dict:
        symbol = require_symbol(symbol)
        exchange = require_exchange(exchange)
        from app.market_entitlements import require_listing_entitlement, security_identity
        entitlement = require_listing_entitlement(symbol, exchange or "")
        if interval not in INTERVALS or not isinstance(outputsize, int) or not 2 <= outputsize <= 250:
            raise MarketError("invalid_history_request", 422)
        params: dict[str, Any] = {"symbol": symbol, "interval": interval,
                                  "outputsize": outputsize}
        if exchange:
            params["exchange"] = exchange
        raw = self._fetch("/time_series", params, 900)
        meta, values = raw.get("meta"), raw.get("values")
        if not isinstance(meta, dict) or not isinstance(values, list) or not values:
            raise MarketError("invalid_provider_data")
        m_symbol = _string(meta.get("symbol"), 20).upper()
        if m_symbol and m_symbol != symbol:
            raise MarketError("provider_symbol_mismatch")
        reported_exchange = _string(meta.get("exchange"), 50)
        currency = _string(meta.get("currency"), 10).upper()
        if not reported_exchange or not currency:
            raise MarketError("invalid_provider_data")
        if exchange and reported_exchange.casefold() != exchange.casefold():
            raise MarketError("provider_exchange_mismatch", 409)
        points: list[dict] = []
        for v in values:
            if not isinstance(v, dict):
                raise MarketError("invalid_provider_data")
            date = _string(v.get("datetime"), 22)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", date):
                raise MarketError("invalid_provider_data")
            price = str(_decimal(v.get("close"), positive=True))
            points.append({"date": date, "close": price})
        dates = [v["date"] for v in points]
        if len(set(dates)) != len(dates):
            raise MarketError("invalid_provider_data")
        points.sort(key=lambda v: v["date"])
        company_name = _string(meta.get("name"), 120)
        entitlement = require_listing_entitlement(symbol, reported_exchange, name=company_name, currency=currency)
        identity = security_identity(symbol, reported_exchange, company_name)
        return {"provider": "Twelve Data", "symbol": symbol,
                "company_name": company_name,
                "exchange": reported_exchange, "currency": currency,
                "interval": interval, "points": points, "fetched_at_utc": _iso_now(),
                "cache_hit": bool(raw.get("cache_hit")),
                "price_type": "unadjusted_provider_close_dividends_excluded",
                "freshness": "historical_not_guaranteed_realtime",
                "entitlement": entitlement,
                "security_type": identity.get("security_type"),
                "security_label": identity.get("security_label"),
                "security_note": identity.get("security_note")}

    def compare(self, a: tuple[str, str | None], b: tuple[str, str | None],
                *, outputsize: int = 90) -> dict:
        if (require_symbol(a[0]), (require_exchange(a[1]) or "").casefold()) == \
           (require_symbol(b[0]), (require_exchange(b[1]) or "").casefold()):
            raise MarketError("comparison_requires_distinct_instruments", 422)
        from app.market_entitlements import require_listing_entitlement
        require_listing_entitlement(require_symbol(a[0]), require_exchange(a[1]) or "")
        require_listing_entitlement(require_symbol(b[0]), require_exchange(b[1]) or "")
        left = self.history(a[0], a[1], outputsize=outputsize)
        right = self.history(b[0], b[1], outputsize=outputsize)
        la = {p["date"]: _decimal(p["close"], positive=True) for p in left["points"]}
        rb = {p["date"]: _decimal(p["close"], positive=True) for p in right["points"]}
        overlap = sorted(set(la) & set(rb))
        if len(overlap) < 2:
            raise MarketError("insufficient_shared_dates", 422)
        start, end = overlap[0], overlap[-1]
        def pct(p: dict, label: str) -> dict:
            c0, c1 = p[start], p[end]
            return {"symbol": label, "first_close": str(c0), "last_close": str(c1),
                    "return_percent": str((c1 / c0 - Decimal(1)) * Decimal(100))}
        return {"provider": "Twelve Data", "start_date": start, "end_date": end,
                "shared_trading_dates": len(overlap),
                "results": [{**pct(la, left["symbol"]), "exchange": left["exchange"],
                             "currency": left["currency"]},
                            {**pct(rb, right["symbol"]), "exchange": right["exchange"],
                             "currency": right["currency"]}],
                "comparison_type": "unadjusted_price_return_percent_excludes_dividends",
                "warning": "Comparing percentage price returns, not currency-denominated prices or total returns; markets may have different trading calendars.",
                "sources": ["Twelve Data /time_series"], "fetched_at_utc": _iso_now()}
