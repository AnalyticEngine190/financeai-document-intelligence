"""Offline diagnostic for Twelve Data plan and instrument-identity rules.
No provider requests are made and no API key is read or displayed.
"""
from app.market_entitlements import classify_listing, configured_plan, otc_enabled, status_payload


def main() -> None:
    print("FinanceAI market coverage diagnostic (offline; no provider requests)")
    print(f"Configured plan: {configured_plan().upper()} (application setting; not auto-detected from key)")
    print(f"OTC access explicitly enabled: {otc_enabled()}\n")
    examples = [
        ("AAPL", "NASDAQ", "Apple Inc.", "United States", "USD", "Common Stock"),
        ("MSFT", "NASDAQ", "Microsoft Corporation", "United States", "USD", "Common Stock"),
        ("NDA", "NASDAQ HELSINKI", "Nordea Bank Abp", "Finland", "EUR", "Common Stock"),
        ("NDA", "NASDAQ STOCKHOLM", "Nordea Bank Abp", "Finland", "SEK", "Common Stock"),
        ("NDA", "NASDAQ COPENHAGEN", "Nordea Bank Abp", "Finland", "DKK", "Common Stock"),
        ("NBNKF", "OTC Markets OTCQX", "Nordea Bank Abp", "Finland", "USD", "Common Stock"),
        ("NRDBY", "OTC Markets", "Nordea Bank Abp", "United States", "USD", "Depositary Receipt"),
    ]
    for symbol, exchange, name, country, currency, kind in examples:
        result = classify_listing(symbol, exchange, name=name, country=country,
                                  currency=currency, instrument_type=kind)
        state = "ALLOWED" if result["allowed"] else "BLOCKED"
        print(f"{symbol:6} | {exchange:22} | {currency:3} | {state:7} | {result['entitlement_label'] or result['reason_code']}")
        if result.get("security_label"):
            print(f"       identity: {result['security_label']}")
    print("\nStatus payload:")
    for key, value in status_payload().items():
        print(f"  {key}: {value}")
    print("\nNo Twelve Data API credits consumed.")


if __name__ == "__main__":
    main()
