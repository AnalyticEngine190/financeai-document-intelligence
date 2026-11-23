"""Conservative Twelve Data exchange-entitlement and instrument identity rules.

The provider key does not expose a reliably machine-readable subscription name,
so TWELVE_DATA_PLAN is explicit application configuration (default: basic).
OTC data is a separate entitlement and is disabled unless explicitly enabled.
"""
from __future__ import annotations

import os
import re
from typing import Any

PLAN_RANK = {"basic": 1, "grow": 2, "pro": 3, "ultra": 4}
NORDIC_VENUES = {
    "XHEL": "Nasdaq Helsinki",
    "NASDAQ HELSINKI": "Nasdaq Helsinki",
    "XSTO": "Nasdaq Stockholm",
    "NASDAQ STOCKHOLM": "Nasdaq Stockholm",
    "XCSE": "Nasdaq Copenhagen",
    "NASDAQ COPENHAGEN": "Nasdaq Copenhagen",
}
US_VENUE_TOKENS = (
    "NASDAQ", "NASDAQ GLOBAL", "NASDAQ CAPITAL", "NYSE", "NEW YORK STOCK",
    "NYSE ARCA", "NYSE AMERICAN", "CBOE", "IEX", "BATS", "NYSE NATIONAL",
)


def configured_plan() -> str:
    """Return configured plan; this is configuration, not provider auto-detection."""
    value = os.getenv("TWELVE_DATA_PLAN", "basic").strip().casefold()
    return value if value in PLAN_RANK else "basic"


def otc_enabled() -> bool:
    return os.getenv("TWELVE_DATA_ALLOW_OTC", "0").strip().casefold() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_otc(symbol: str, exchange: str) -> bool:
    symbol = symbol.upper()
    venue = exchange.upper()
    # Nordea's NBNKF ordinary shares and NRDBY sponsored ADR are OTC instruments.
    return "OTC" in venue or symbol in {"NBNKF", "NRDBY"}


def _nordic_venue(exchange: str) -> str | None:
    key = re.sub(r"\s+", " ", exchange.upper()).strip()
    for token, label in NORDIC_VENUES.items():
        if key == token or token in key:
            return label
    return None


def _is_us_default_equity(exchange: str, country: str) -> bool:
    # Use the exchange/venue, not the issuer's country of incorporation: a
    # Finnish company can have a US-listed ADR, and that does not make its
    # primary Nordic listing part of the US feed.
    venue = exchange.upper()
    return any(token in venue for token in US_VENUE_TOKENS)


def security_identity(symbol: str, exchange: str, name: str = "") -> dict[str, str | None]:
    """Describe known Nordea instruments without conflating ordinary shares and ADRs."""
    symbol = symbol.upper().strip()
    name = name or ""
    if symbol == "NBNKF":
        return {
            "security_type": "ordinary_shares",
            "security_label": "OTC ordinary shares · non-primary listing",
            "security_note": "NBNKF represents Nordea Bank Abp ordinary shares traded on an OTC venue; it is not Nordea's primary Nasdaq Nordic listing.",
        }
    if symbol == "NRDBY":
        return {
            "security_type": "sponsored_adr",
            "security_label": "Sponsored ADR · 1 ADR = 1 ordinary share · OTC",
            "security_note": "NRDBY is Nordea's sponsored US ADR. Nordea states the ratio is 1 ADR to 1 ordinary share; it trades on an OTC venue, not on the primary Nordic exchanges.",
        }
    if "NORDEA" in name.upper() or symbol == "NDA":
        venue = _nordic_venue(exchange)
        if venue:
            return {
                "security_type": "ordinary_shares",
                "security_label": f"Primary ordinary shares · {venue}",
                "security_note": "Nordea's ordinary shares are listed on the Nasdaq Nordic exchanges.",
            }
    return {"security_type": None, "security_label": None, "security_note": None}


def classify_listing(symbol: str, exchange: str, *, name: str = "", currency: str = "",
                     country: str = "", instrument_type: str = "") -> dict:
    """Attach entitlement and security identity to a provider-returned listing."""
    symbol = _text(symbol).upper()
    exchange = _text(exchange)
    name = _text(name)
    country = _text(country)
    plan = configured_plan()
    otc = _is_otc(symbol, exchange)
    nordic = _nordic_venue(exchange)
    identity = security_identity(symbol, exchange, name)

    base = {
        "plan": plan,
        "plan_auto_detected": False,
        "security_type": identity["security_type"] or (_text(instrument_type).casefold() or None),
        "security_label": identity["security_label"],
        "security_note": identity["security_note"],
        "is_otc": otc,
        "required_plan": None,
        "allowed": False,
        "reason_code": None,
        "entitlement_label": "",
        "message": "",
    }

    if otc:
        if otc_enabled():
            base.update(allowed=True, entitlement_label="OTC access explicitly enabled")
            return base
        base.update(
            reason_code="market_otc_license_required",
            entitlement_label="Separate OTC licence required",
            message=otc_message(symbol, identity),
        )
        return base

    if nordic:
        if PLAN_RANK.get(plan, 1) >= PLAN_RANK["pro"]:
            base.update(allowed=True, required_plan="pro", entitlement_label="Pro exchange access configured")
            return base
        base.update(
            reason_code="market_plan_restricted",
            required_plan="pro",
            entitlement_label="Pro exchange required",
            message=nordic_message(nordic, symbol),
        )
        return base

    if _is_us_default_equity(exchange, country):
        base.update(allowed=True, entitlement_label="Default US equity feed")
        return base

    # Direct quote/history calls without an exchange may be resolved by Twelve
    # Data's response. This provisional allowance is rechecked against the
    # provider-reported exchange before any result is returned. Known OTC
    # tickers and Nordea's NDA ticker are never provisionally allowed.
    if not exchange and symbol not in {"NDA", "NBNKF", "NRDBY"}:
        base.update(allowed=True, entitlement_label="Provider exchange will be validated")
        return base

    # A provider search hit is not proof of subscription entitlement. Unknown
    # non-US venues are therefore not automatically promoted to Basic access.
    base.update(
        reason_code="market_plan_restricted",
        entitlement_label="Exchange entitlement not established",
        message=(
            (nordic_message("Nasdaq Helsinki, Stockholm or Copenhagen", symbol) if symbol == "NDA" else
             f"This FinanceAI installation is configured for the Twelve Data {plan.title()} subscription. "
            "This installation assumes the default US listed-equity feed for Basic access. "
            f"Twelve Data returned {exchange or 'this exchange'} as a search candidate, but a search result "
            "does not prove that your key is entitled to retrieve its prices. Check that exchange in your "
            "Twelve Data account before enabling it. US-listed equities on the default feed remain available; "
            "financial-report questions are unaffected.")
        ),
    )
    return base


def nordic_message(venue: str, symbol: str = "NDA") -> str:
    plan = configured_plan().title()
    basic_clause = (
        "For this installation, the Basic subscription uses Twelve Data's default US listed-equity feed; "
        "it cannot retrieve the primary Nordic exchange quote. "
        if plan == "Basic" else
        f"The configured {plan} plan is shown here as the application's entitlement setting; verify the exchange is enabled in your provider account. "
    )
    return (
        f"This FinanceAI installation is configured for the Twelve Data {plan} subscription. "
        f"Nordea's primary ordinary shares trade on {venue}. {basic_clause}"
        "Twelve Data lists Nasdaq Helsinki, Stockholm and Copenhagen as Pro exchange access. "
        "The provider's primary ordinary-share symbol is NDA (EUR in Helsinki, SEK in Stockholm, DKK in Copenhagen). "
        "US-listed equities such as AAPL and MSFT remain available on the Basic default feed. "
        "A US OTC match is not a substitute for the primary listing; OTC securities require a separate licence. "
        "Financial-report questions still work."
    )


def nordea_basic_coverage_message() -> str:
    return (
        "This FinanceAI installation is configured for the Twelve Data Basic subscription. For equities, this installation "
        "uses the Basic default US listed-equity feed, so US-listed stocks such as AAPL and MSFT are available. "
        "Nordea's primary ordinary shares are listed as NDA on Nasdaq Helsinki (EUR), Nasdaq Stockholm (SEK), "
        "and Nasdaq Copenhagen (DKK); Twelve Data marks those Nordic exchange feeds as Pro. "
        "The US OTC symbols NBNKF and NRDBY are not substitutes for Nordea's primary listing: OTC securities "
        "require a separate data licence. I won't silently substitute an OTC instrument or show an unentitled quote. "
        "Nordea's indexed financial reports remain available."
    )


def otc_message(symbol: str, identity: dict[str, str | None] | None = None) -> str:
    plan = configured_plan().title()
    identity = identity or security_identity(symbol, "OTC")
    detail = identity.get("security_note") or f"{symbol} is an over-the-counter security."
    if symbol.upper() in {"NBNKF", "NRDBY"}:
        return (
            f"This FinanceAI installation is configured for the Twelve Data {plan} subscription. {detail} "
            + ("The Basic default equity feed used here is US-listed equities; this OTC security is not included. " if plan == "Basic" else "")
            + "Twelve Data excludes OTC securities from its default plans; OTC data requires a separate licence. "
            "This is not Nordea's primary Helsinki, Stockholm or Copenhagen listing. The app will not treat "
            "an OTC quotation as a Basic-plan quote unless OTC access is explicitly enabled after the provider "
            "confirms your separate licence. "
            "Nordea's main ordinary-share listings require Pro exchange access. Financial-report answers still work."
        )
    return (
        f"This FinanceAI installation is configured for the Twelve Data {plan} subscription. {detail} "
        + ("The Basic default equity feed used here is US-listed equities. " if plan == "Basic" else "")
        + "OTC securities are not included in Twelve Data's default subscription plans and require a separate "
        "OTC data licence. I won't display this as an entitled Basic-plan quote. Financial-report questions still work."
    )


def status_payload() -> dict:
    plan = configured_plan()
    return {
        "configured_plan": plan,
        "plan_auto_detected": False,
        "plan_configuration": "TWELVE_DATA_PLAN environment variable (defaults to basic)",
        "default_us_equity_feed": True,
        "nordic_exchange_requirement": "pro",
        "otc_data_requirement": "separate_license",
        "otc_enabled_for_app": otc_enabled(),
        "coverage_note": (
            f"Configured for Twelve Data {plan.title()} via TWELVE_DATA_PLAN (the provider plan is not auto-detected from the API key). "
            "Basic uses the default US listed-equity feed; Nasdaq Helsinki, Stockholm and Copenhagen require Pro "
            "according to Twelve Data's exchange directory. OTC securities require a separate licence and are "
            "disabled unless TWELVE_DATA_ALLOW_OTC=1."
        ),
    }


def require_listing_entitlement(symbol: str, exchange: str, *, name: str = "", currency: str = "",
                                country: str = "", instrument_type: str = "") -> dict:
    profile = classify_listing(symbol, exchange, name=name, currency=currency,
                               country=country, instrument_type=instrument_type)
    if not profile["allowed"]:
        raise _entitlement_error(profile)
    return profile


def _entitlement_error(profile: dict):
    # Import lazily to keep this metadata module independent at import time.
    from app.market_data import MarketError
    code = str(profile.get("reason_code") or "market_plan_restricted")
    raise_error = MarketError(code, 403)
    raise_error.coverage_message = str(profile.get("message") or "This listing is not enabled by the configured market-data plan.")
    return raise_error
