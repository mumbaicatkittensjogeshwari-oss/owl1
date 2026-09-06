"""
OWL Terminal backend - FastAPI wrapper around the real bot logic in
recovered_bot/ (the same strategy/paper-trading/telegram code that ran
inside the old Kivy app). This replaces the old placeholder api_server.py
that only sent fake random BTC prices and never touched recovered_bot at
all - that's why the Flutter app showed "0 coins" / "No signals" / "No
market data" even though it connected fine (WS was live, just had nothing
real to send).

Run with: python start.py  (same as before, still uvicorn on port 8000)
Meant to run in Termux on the same phone as the Flutter app, so the
frontend's existing http://localhost:8000 / ws://localhost:8000/ws URLs
work as-is - no IP/domain needed for that setup.
"""
import asyncio
import json
import os
import threading
import time
import traceback

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from recovered_bot import settings_store, trade_store, paper_trader, status, signal_log
from recovered_bot.app import main as bot_main

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

_bot_thread_started = False
_bot_thread_lock = threading.Lock()


def _run_bot():
    try:
        bot_main()
    except Exception:
        tb = traceback.format_exc()
        status.update(last_error=f"BOT CRASHED:\n{tb}")


@app.on_event("startup")
def on_startup():
    global _bot_thread_started
    with _bot_thread_lock:
        if _bot_thread_started:
            return
        settings_store.init(os.path.join(DATA_DIR, "settings.json"))
        trade_store.init(os.path.join(DATA_DIR, "trades.json"))
        paper_trader.init(os.path.join(DATA_DIR, "positions.json"))
        signal_log.init(os.path.join(DATA_DIR, "signal_log.json"))
        status.update(**trade_store.get_today_stats())
        threading.Thread(target=_run_bot, daemon=True).start()
        _bot_thread_started = True


# ---------------------------------------------------------------- basic ----

@app.get("/")
async def root():
    return {"status": "OWL Terminal API", "bot_status": status.get().get("status")}


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ---------------------------------------------------------------- reads ----

@app.get("/api/status")
async def get_status():
    return status.get()


@app.get("/api/market")
async def get_market():
    return {"movers": status.get().get("market_movers", [])}


@app.get("/api/signals")
async def get_signals():
    return {"signals": signal_log.get_recent(100)}


@app.get("/api/alerts")
async def get_alerts():
    return {"alerts": status.get().get("alerts", [])}


@app.get("/api/history")
async def get_history():
    return {"trades": trade_store.get_recent_closed(50)}


@app.get("/api/equity")
async def get_equity():
    return {"curve": trade_store.get_equity_curve(14)}


@app.get("/api/settings")
async def get_settings():
    return settings_store.get_all()


@app.post("/api/settings")
async def update_settings(payload: dict):
    settings_store.update(**payload)
    return settings_store.get_all()


# ---------------------------------------------------------------- writes ----

@app.post("/api/close/{trade_id}")
async def close_one(trade_id: int):
    paper_trader.close_position_manual(trade_id)
    return {"ok": True}


@app.post("/api/close_all")
async def close_all():
    open_positions = status.get().get("open_positions", [])
    for pos in open_positions:
        pid = pos.get("id")
        if pid is not None:
            paper_trader.close_position_manual(pid)
    return {"ok": True, "closed": len(open_positions)}


# -------------------------------------------------------------- websocket --

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send an immediate snapshot on connect so the UI doesn't have to
        # wait for the next tick before showing anything.
        await websocket.send_text(json.dumps({"type": "init", "data": status.get()}))
        while True:
            # Frontend only reads pushed messages (see updateFromWebSocket in
            # app_provider.dart) - it doesn't need to send anything back, but
            # we still drain the socket so a client-sent ping doesn't pile up.
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            await websocket.send_text(json.dumps({"type": "update", "data": status.get()}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
