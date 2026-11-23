"""Tests for optional presentation-only reaction logic."""

from app.fun_mode import REACTIONS, choose_reaction, classify_reaction, image_path


def test_unsupported_always_wins():
    assert classify_reaction("Profits increased 10%", supported=False) == "unsupported"


def test_naked_financial_number_is_neutral():
    assert classify_reaction("15.8%", supported=True) == "neutral"
    assert classify_reaction("The combined LCR is 160%.", supported=True) == "neutral"


def test_generic_credit_risk_is_not_an_alarm():
    assert classify_reaction("Credit risk exposure was EUR 120,716m.", True) == "neutral"


def test_explicit_positive_change():
    assert classify_reaction("Revenue increased by 5%.", True) == "positive"


def test_explicit_negative_change():
    assert classify_reaction("Loan losses increased by 5%.", True) == "negative"


def test_mixed_changes():
    assert classify_reaction("Revenue increased by 5%, but loan losses increased by 8%.", True) == "mixed"


def test_explicit_risk_warning():
    assert classify_reaction("The bank reported a liquidity shortfall.", True) == "risk"


def test_question_is_not_used_for_sentiment():
    r = choose_reaction("Did profits fall?", "15.8%", True)
    assert r.category == "neutral"


def test_reaction_is_stable():
    question = "What was the CET1 ratio?"
    assert choose_reaction(question, "15.8%", True) == choose_reaction(question, "15.8%", True)


def test_twelve_reactions_have_stable_optional_asset_paths():
    # The UI supports an emoji/text fallback when the optional image bundle is
    # omitted from a lightweight project snapshot.
    assert sum(len(group) for group in REACTIONS.values()) == 12
    assert all(image_path(reaction).name == reaction.image_file
               for group in REACTIONS.values() for reaction in group)
