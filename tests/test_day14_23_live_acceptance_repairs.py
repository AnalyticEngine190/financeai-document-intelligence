from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

from app import market_chat
from app.financial_vocabulary import metric_matches


def _history_points(end: date, days: int = 120):
    points = []
    current = end - timedelta(days=days)
    while current <= end:
        if current.weekday() < 5:
            points.append({"date": current.isoformat(), "close": str(100 + len(points) / 10)})
        current += timedelta(days=1)
    return points


def test_seb_pre_credit_losses_is_retrieved_from_the_group_statement(monkeypatch):
    path = Path(__file__).parent / "fixtures" / "seb_q3_2026_income_statement_page.json"
    page = json.loads(path.read_text(encoding="utf-8"))
    from app import document_tables
    from app.document_registry import DocumentRecord
    from app.document_resolver import DocumentSelection
    from app.document_answering import answer_with_selection

    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: [page])
    record = DocumentRecord("sebq3", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
    result = answer_with_selection(
        "What was SEB's profit before credit losses in Q3 2026?",
        DocumentSelection(), [record],
    )
    assert result["supported"] is True
    assert result["answer"] == "SEK 10,448 million"
    assert result["sources"][0]["page"] == 5


def test_seb_profit_before_credit_losses_table_reader_value(monkeypatch):
    path = Path(__file__).parent / "fixtures" / "seb_q3_2026_income_statement_page.json"
    page = json.loads(path.read_text(encoding="utf-8"))
    from app import document_tables
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: [page])
    cell = document_tables.generic_cell("profit_before_credit_losses_fees", "Q326", "sebq3")
    assert cell is not None
    assert cell.value == Decimal("10448")
    assert cell.unit == "SEKm"
    assert cell.page == 5


def test_financial_vocabulary_accepts_public_deposits_and_shortened_pre_credit_losses():
    deposits = {hit[0].key for hit in metric_matches("SEB Baltic public deposits")}
    profit = {hit[0].key for hit in metric_matches("SEB profit before credit losses")}
    assert "segment_deposits_public" in deposits
    assert "profit_before_credit_losses_fees" in profit


def test_quarter_followup_preserves_issuer_metric_and_does_not_duplicate_preposition(monkeypatch):
    monkeypatch.setattr("app.document_registry.list_documents", lambda: [])
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda _q: ["Nordea"])
    merged, message = market_chat._merge_period_followup(
        "And in Q2?", "What was Nordea's net profit in Q3 2026?"
    )
    assert message is None
    assert merged == "What was Nordea's net profit in Q2 2026"


def test_metric_followup_changes_only_metric():
    merged = market_chat._merge_metric_followup(
        "What about operating profit?", "What was Nordea's net profit in Q3 2026?"
    )
    assert merged == "What was Nordea's operating profit in Q3 2026?"


def test_process_chat_routes_metric_followup_with_original_issuer_and_period(monkeypatch):
    captured = {}
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda q: ["Nordea"] if "Nordea" in q else [])
    def fake_report(question, _selection):
        captured["question"] = question
        return {"answer": "captured", "supported": True, "sources": []}

    monkeypatch.setattr(market_chat, "_report", fake_report)
    body = market_chat.ChatRequest(
        question="What about operating profit?",
        history=[market_chat.ChatTurn(role="user", content="What was Nordea's net profit in Q3 2026?")],
    )
    market_chat.process_chat(body)
    assert captured["question"] == "What was Nordea's operating profit in Q3 2026?"


def test_process_chat_routes_quarter_followup_with_original_issuer_and_metric(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.document_registry.list_documents", lambda: [])
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda q: ["Nordea"] if "Nordea" in q else [])
    def fake_report(question, _selection):
        captured["question"] = question
        return {"answer": "captured", "supported": True, "sources": []}

    monkeypatch.setattr(market_chat, "_report", fake_report)
    body = market_chat.ChatRequest(
        question="And in Q2?",
        history=[market_chat.ChatTurn(role="user", content="What was Nordea's net profit in Q3 2026?")],
    )
    market_chat.process_chat(body)
    assert captured["question"] == "What was Nordea's net profit in Q2 2026"


def test_inline_quarter_followup_becomes_explicit_two_period_query():
    merged = market_chat._expand_inline_quarter_followup(
        "What was Nordea's net profit in Q3 2026? And in Q2?"
    )
    assert merged is not None
    assert "Nordea's net profit" in merged
    assert "Q2 2026" in merged and "Q3 2026" in merged


def test_segment_followup_inherits_only_one_prior_issuer():
    merged = market_chat._inherit_issuer_for_segment_question(
        "What was Personal Banking's net interest income in Q3 2026?",
        "What was Nordea's CET1 capital ratio in Q3 2026?",
    )
    assert merged.startswith("Nordea ")
    assert "Personal Banking" in merged


def test_process_chat_routes_segment_followup_to_prior_issuer(monkeypatch):
    captured = {}
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda q: ["Nordea"] if "Nordea" in q else [])

    def fake_report(question, _selection):
        captured["question"] = question
        return {"answer": "captured", "supported": True, "sources": []}

    monkeypatch.setattr(market_chat, "_report", fake_report)
    body = market_chat.ChatRequest(
        question="What was Personal Banking's net interest income in Q3 2026?",
        history=[market_chat.ChatTurn(role="user", content="What was Nordea's CET1 capital ratio in Q3 2026?")],
    )
    market_chat.process_chat(body)
    assert captured["question"].startswith("Nordea ")
    assert "Personal Banking" in captured["question"]


def test_previous_close_followup_resolves_ticker_and_calculates_from_two_closes():
    rewritten = market_chat._merge_market_previous_close_followup(
        "What was the USD price change and percentage change from the previous close?",
        "AAPL latest price",
    )
    assert rewritten == "AAPL price change from previous close"
    answer = market_chat._previous_close_answer({
        "symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD",
        "close": "342.56", "previous_close": "349.19",
        "provider_datetime": "2026-11-20",
    })
    assert "USD 6.63" in answer
    assert "-1.90%" in answer


def test_explicit_calendar_window_is_filtered_to_30_days_and_chart_scope_matches():
    question = "Compare AAPL and MSFT over the last 30 calendar days."
    assert market_chat._calendar_days(question) == 30
    assert market_chat._calendar_days("Compare AAPL and MSFT over the last calendar month.") == 30
    assert market_chat._history_request_size(question, 90) >= 60
    end = date(2026, 11, 20)
    points = _history_points(end)
    first = {"symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD", "points": points}
    second = {"symbol": "MSFT", "exchange": "NASDAQ", "currency": "USD", "points": points}
    comparison, first_filtered, second_filtered = market_chat._compare_history_data(first, second, 30)
    assert comparison["requested_calendar_days"] == 30
    assert comparison["start_date"] >= (end - timedelta(days=30)).isoformat()
    assert comparison["end_date"] == end.isoformat()
    assert 20 <= comparison["shared_trading_dates"] <= 23
    assert len(first_filtered["points"]) == len(second_filtered["points"]) <= 23


def test_history_reconstruction_ignores_assistant_responses_and_keeps_latest_financial_context(monkeypatch):
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda q: ["Nordea"] if "Nordea" in q else [])
    body = market_chat.ChatRequest(
        question="What about operating profit?",
        history=[
            market_chat.ChatTurn(role="user", content="What was Nordea's net profit in Q3 2026?"),
            market_chat.ChatTurn(role="assistant", content="Nordea net profit in Q3 2026: EUR 1,181 million."),
        ],
    )
    assert market_chat._prior_user_question(body) == "What was Nordea's net profit in Q3 2026?"


def test_previous_close_followup_is_marked_for_ui_answer_rendering(monkeypatch):
    monkeypatch.setattr(market_chat, "_known_issuer_mentions", lambda _q: [])
    monkeypatch.setattr(
        market_chat.legacy.market_client, "quote",
        lambda *_args, **_kwargs: {
            "symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD",
            "close": "342.56", "previous_close": "349.19",
            "provider_datetime": "2026-11-20", "fetched_at_utc": "2026-11-21T12:00:00+00:00",
            "percent_change": "-1.90",
        },
    )
    body = market_chat.ChatRequest(
        question="What was the USD price change and percentage change from the previous close?",
        history=[market_chat.ChatTurn(role="user", content="AAPL latest price")],
        remembered_listings={
            "aapl": market_chat.Listing(symbol="AAPL", exchange="NASDAQ", entity_key="aapl")
        },
    )
    out = market_chat.process_chat(body)
    assert out["kind"] == "market_quote"
    assert out["show_quote_answer"] is True
    assert "USD 6.63" in out["answer"] and "-1.90%" in out["answer"]


def test_confirmed_listing_memory_is_reused_without_another_symbol_search(monkeypatch):
    def unexpected_search(_term):
        raise AssertionError("a user-confirmed listing should be reused")

    monkeypatch.setattr(market_chat, "_lookup_term", unexpected_search)
    listing, choices = market_chat._selected_or_search(
        "AAPL", 0, [],
        {"aapl": market_chat.Listing(symbol="AAPL", exchange="NASDAQ", entity_key="aapl")},
    )
    assert listing is not None and listing.exchange == "NASDAQ"
    assert choices == []


def test_short_provider_history_is_not_mislabeled_as_full_calendar_window():
    end = date(2026, 11, 20)
    points = _history_points(end, days=10)
    history = {"symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD", "points": points}
    try:
        market_chat._filter_history_calendar_window(history, 30)
    except market_chat.MarketError as exc:
        assert exc.code == "insufficient_shared_dates"
    else:
        raise AssertionError("short provider history must not be presented as a complete 30-day window")
