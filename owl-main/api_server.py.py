"""
FastAPI server for Trading Bot - serves REST endpoints + WebSocket for real-time data.
Compatible with Flutter frontend.
"""
import asyncio
import json
import time
import threading
from datetime import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

# Import your existing bot modules
from recovered_bot import (
    status as bot_status,
    settings_store,
    trade_store,
    signal_log,
    market_ws,
    paper_trader,
)

app = FastAPI(title="Trading Bot API", version="2.0")

# CORS - allow Flutter app to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = threading.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._lock:
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        """Send data to all connected clients."""
        with self._lock:
            connections = self.active_connections.copy()
        message = json.dumps(data)
        for conn in connections:
            try:
                await conn.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# ============ REST Endpoints ============

@app.get("/")
async def root():
    return {"status": "Trading Bot API is running", "version": "2.0"}

@app.get("/api/status")
async def get_status():
    """Full bot status - P&L, positions, signals, market data."""
    data = bot_status.get()
    # Add open positions count
    data["open_count"] = paper_trader.get_open_count()
    return data

@app.get("/api/positions")
async def get_positions():
    """Open positions list."""
    data = bot_status.get()
    return {"open_positions": data.get("open_positions", [])}

@app.get("/api/history")
async def get_history(limit: int = 50):
    """Closed trades history."""
    trades = trade_store.get_recent_closed(limit)
    return {"trades": trades, "count": len(trades)}

@app.get("/api/market")
async def get_market():
    """Market movers (gainers/losers)."""
    data = bot_status.get()
    return {
        "movers": data.get("market_movers", []),
        "synced_at": data.get("market_synced_at"),
        "symbols_tracked": data.get("symbols_tracked", 0),
    }

@app.get("/api/signals")
async def get_signals(limit: int = 100):
    """Signal history."""
    signals = signal_log.get_recent(limit)
    return {"signals": signals, "count": len(signals)}

@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """Alert feed."""
    data = bot_status.get()
    return {"alerts": data.get("alerts", [])[:limit]}

@app.get("/api/settings")
async def get_settings():
    """All user settings."""
    return settings_store.get_all()

@app.post("/api/settings")
async def update_settings(settings: dict):
    """Update settings."""
    settings_store.update(**settings)
    return {"status": "saved"}

@app.get("/api/equity")
async def get_equity(days: int = 14):
    """Equity curve data for chart."""
    curve = trade_store.get_equity_curve(days)
    return {"curve": curve, "days": days}

@app.post("/api/close/{trade_id}")
async def close_position(trade_id: int):
    """Manually close a position."""
    success = paper_trader.close_position_manual(trade_id)
    return {"success": success, "trade_id": trade_id}

@app.post("/api/close_all")
async def close_all_positions():
    """Close all open positions."""
    # Get all open positions
    data = bot_status.get()
    positions = data.get("open_positions", [])
    closed = []
    for pos in positions:
        tid = pos.get("id")
        if tid:
            if paper_trader.close_position_manual(tid):
                closed.append(tid)
    return {"closed": len(closed), "ids": closed}

# ============ WebSocket Endpoint ============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state immediately
        await websocket.send_text(json.dumps({
            "type": "init",
            "data": bot_status.get()
        }))
        
        # Keep connection alive and listen for client messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Handle client messages (e.g., ping, commands)
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send heartbeat ping
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# ============ Background Broadcaster ============

async def broadcast_loop():
    """Background task that broadcasts status updates to all WebSocket clients."""
    last_broadcast = 0
    while True:
        try:
            now = time.time()
            # Broadcast every 1 second (or when important data changes)
            if now - last_broadcast >= 1.0:
                last_broadcast = now
                data = bot_status.get()
                # Add live price overlay from market_ws
                await manager.broadcast({
                    "type": "update",
                    "data": data
                })
        except Exception as e:
            print(f"[Broadcast] Error: {e}")
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def startup_event():
    """Start background broadcaster."""
    asyncio.create_task(broadcast_loop())
    print("[API] Started broadcast loop")


def run_server():
    """Run the FastAPI server with uvicorn."""
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    run_server()