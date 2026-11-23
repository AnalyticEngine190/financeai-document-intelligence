"""Day 13.8 tests for author attribution, capabilities and market-data disclosure."""
from pathlib import Path
import ast

import pytest

from app import market_chat


class NoProviderCalls:
    def search(self, *_args, **_kwargs):
        raise AssertionError("about questions must not call the market provider")

    def quote(self, *_args, **_kwargs):
        raise AssertionError("about questions must not call the market provider")


def test_who_made_you_is_deterministic_and_credits_serhat():
    result = market_chat.process_chat(market_chat.ChatRequest(question="Who made you?"))
    assert result["kind"] == "about"
    assert result["supported"] is True
    assert "Serhat Turan" in result["answer"]
    assert "B.S. in Mathematics" in result["answer"]
    assert "top 10%" in result["answer"]
    assert result["sources"] == []


def test_creator_question_does_not_call_documents_or_market_provider(monkeypatch):
    monkeypatch.setattr(market_chat.legacy, "market_client", NoProviderCalls())
    monkeypatch.setattr(
        market_chat.legacy,
        "_doc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("document RAG must not run")),
    )
    result = market_chat.process_chat(market_chat.ChatRequest(question="Who created you?"))
    assert result["kind"] == "about"
    assert "Serhat Turan" in result["answer"]


def test_capabilities_question_explains_basic_plan_and_limitations():
    result = market_chat.process_chat(market_chat.ChatRequest(question="What can you do?"))
    assert result["kind"] == "about"
    answer = result["answer"].casefold()
    assert "twelve data basic" in answer
    assert "may be delayed" in answer
    assert "pdf-page citations" in answer
    assert "not investment advice" in answer


def test_about_question_variants_are_recognized():
    for question in (
        "Who made you?",
        "Who created you?",
        "Who developed this assistant?",
        "What are your capabilities?",
        "What is your purpose?",
    ):
        assert market_chat._intent(question) == "about"


def test_unrelated_who_question_is_not_routed_to_creator_answer():
    assert market_chat._intent("Who created Nordea?") == "document"


def test_ui_displays_disclaimer_capabilities_and_recommended_prompts():
    root = Path(__file__).resolve().parents[1]
    source = (root / "streamlit_market_app.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "Twelve Data Basic" in source
    assert "Not investment advice" in source
    assert "Capabilities, data coverage & limitations" in source
    assert '"Who made you?"' in source
    assert '"What can you do?"' in source
    assert "st.chat_input(" in source
    assert "st.tabs(" not in source
