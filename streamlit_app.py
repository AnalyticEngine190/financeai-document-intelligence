"""Compatibility entrypoint for older FinanceAI launch instructions.

Run this or streamlit_market_app.py; both launch the current chat-first UI.
"""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("streamlit_market_app.py")), run_name="__main__")
