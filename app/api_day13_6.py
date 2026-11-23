"""Compatibility alias for the market-enabled and chat-enabled API.

Both api_day13_5 and api_day13_6 now expose /chat_v1. The conditional avoids
registering the same router twice when api_day13_6 imports api_day13_5.
"""
from app.api_day13_5 import app
from app.market_chat import router as chat_router

if not any(getattr(route, "path", None) == "/chat_v1" for route in app.routes):
    app.include_router(chat_router)
