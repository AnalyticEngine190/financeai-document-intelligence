"""Diagnose company-name to provider-confirmed listing resolution.

Offline mode prints the search terms the chat will try. --live queries Twelve Data
and prints only candidate instrument metadata; it never displays API credentials
or asks for a quote. A live run may consume up to three symbol-search credits.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.market_chat import _search_terms_for
from app.market_data import MarketError
from app.market_routes import market_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default="Nordea", help="Issuer name, e.g. Nordea")
    parser.add_argument("--live", action="store_true", help="Query the configured Twelve Data provider")
    args = parser.parse_args()

    terms = _search_terms_for(args.company)
    if not terms:
        print("No safe search term could be extracted.")
        return 2

    print("Company:", args.company)
    print("Provider search sequence:", " -> ".join(terms))
    if not args.live:
        print("Offline diagnosis only; no provider request was sent. Add --live to check actual candidates.")
        return 0

    if not market_client.is_configured():
        print("Market provider is not configured in this process. Set TWELVE_DATA_API_KEY in this terminal.")
        return 2

    found = False
    for term in terms:
        try:
            payload = market_client.search(term)
        except MarketError as exc:
            print(f"{term!r}: provider error code={exc.code}; no credentials or raw provider message displayed")
            if exc.code in {"market_provider_rate_limited", "market_provider_access_denied", "market_provider_unavailable", "invalid_provider_data"}:
                return 3
            continue
        candidates = payload.get("candidates") or []
        if term.isupper() and len(term) <= 10:
            exact = [row for row in candidates if str(row.get("symbol", "")).upper() == term]
            if exact:
                candidates = exact
        print(f"\nSearch {term!r}: {len(candidates)} provider-confirmed candidate(s)")
        for row in candidates[:12]:
            safe = {key: row.get(key) for key in ("name", "symbol", "exchange", "currency", "country", "instrument_type")}
            print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
        if candidates:
            found = True
            break
    if not found:
        print("No provider-confirmed listing was returned for the approved search sequence.")
        return 1
    print("\nNo quote was requested. Choose an exact listing in the chat before a quote is fetched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
