"""Static UI contract; Streamlit itself is installed on the user's Windows PC."""
from pathlib import Path
import ast


def test_single_chat_layout_no_form_tabs():
    root = Path(__file__).resolve().parents[1]
    source = (root / "streamlit_market_app.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "st.chat_input(" in source
    assert "st.chat_message(" in source
    assert "st.tabs(" not in source
    assert '"/chat_v1"' in source
    assert 'st.selectbox("Evidence scope"' in source  # optional sidebar only
    assert "source_lines(result)" in source
    assert "market_clarification" in source
    assert "market_compare" in source
    assert "market_history" in source
    assert "Market data may be delayed" in source


def test_old_financial_answerers_are_not_modified_by_chat_extension():
    root = Path(__file__).resolve().parents[1]
    assert (root / "app" / "api_day13_6.py").is_file()
    code = (root / "app" / "api_day13_6.py").read_text(encoding="utf-8")
    assert "from app.api_day13_5 import app" in code
    assert "include_router(chat_router)" in code


def test_combined_market_clarification_keeps_report_answer_and_citation_visible():
    root = Path(__file__).resolve().parents[1]
    source = (root / "streamlit_market_app.py").read_text(encoding="utf-8")
    ast.parse(source)
    clarification = source.split('if kind == "market_clarification":', 1)[1].split('if kind in ("market_quote", "combined"):', 1)[0]
    assert 'result.get("document")' in clarification
    assert 'source_lines(result)' in clarification
    assert 'Financial-report evidence' in clarification


def test_portfolio_disclaimer_and_recruiter_prompts_are_visible():
    root = Path(__file__).resolve().parents[1]
    source = (root / "streamlit_market_app.py").read_text(encoding="utf-8")
    assert "Educational portfolio demonstration by Serhat Turan" in source
    assert "Twelve Data Basic" in source
    assert "instrument and exchange coverage varies" in source
    assert "Not investment advice" in source
    assert '"Who made you?"' in source
    assert '"What can you do?"' in source
