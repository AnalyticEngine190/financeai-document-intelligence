"""Instrument the existing Day 10 app without changing its answering behavior.

    python -m uvicorn app.api_day11_12:app --host 127.0.0.1 --port 8000
"""
from app.api_day10 import app
from app.day12_observability import install
install(app)
