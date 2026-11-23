"""Optional market-enabled API; existing /ask_v2 and Day 12 ops remain unchanged.

Start: python -m uvicorn app.api_day13_5:app --host 127.0.0.1 --port 8000
"""
from app.api_day11_12 import app
from app.market_routes import router as market_router

app.include_router(market_router)

from app.market_chat import router as chat_router

app.include_router(chat_router)
