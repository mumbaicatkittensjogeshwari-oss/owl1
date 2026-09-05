import threading
import time

_lock = threading.Lock()
_status = {
    "status": "Starting...",
    "symbols_tracked": 0,
    "watchlist": [],
    "last_signals": [],
    "last_error": None,
    "today_pnl_usdt": 0.0,
    "total_trades": 0,
    "win_rate": 0.0,
    "open_positions": [],
    "signals_today": 0,
    "market_movers": [],
    "market_synced_at": None,  # time.time() of the last successful movers fetch, for "Last synced Xs ago"
    "last_signal_by_symbol": {},  # symbol -> {"action": "BUY"/"SELL", "time": "HH:MM:SS"}
    "alerts": [],  # list of {"type": ..., "text": ..., "time": "HH:MM:SS"}, most-recent-first, capped 100
    "api_weight_1m": 0,  # Binance's x-mbx-used-weight-1m from the most recent response, for throttle awareness
    "ws_connected": False,       # Market Watch WebSocket connection state (live ticker cache only - signal engine is unaffected)
    "market_data_state": "DISCONNECTED",  # CONNECTING / LIVE / RECONNECTING / STALE / DISCONNECTED
    "ws_reconnect_count": 0,
    "market_data_error": None,
    "telegram_last_attempt_ts": None,  # time.time() of the most recent send_signal() attempt
    "telegram_last_ok": None,          # True/False once an attempt has been made, else None
    "telegram_last_error": None,       # last failure reason (HTTP code or exception text)
    "last_poll_cycle_ts": None,  # time.time() when the candle-poll loop last finished a
                                   # full cycle across the watchlist - for the Dashboard's
                                   # "data last captured Xs ago" sync check
}

def update(**kwargs):
    with _lock:
        _status.update(kwargs)

def add_signal(signal_text):
    with _lock:
        signals = _status.get("last_signals", [])
        signals.insert(0, signal_text)
        _status["last_signals"] = signals[:10]

def set_last_signal(symbol, action, ts):
    """Record the most recent signal seen for one symbol, for Market Watch
    display. Does not touch any other symbol's entry."""
    with _lock:
        sig_map = dict(_status.get("last_signal_by_symbol", {}))
        sig_map[symbol] = {"action": action, "time": ts}
        _status["last_signal_by_symbol"] = sig_map

def add_alert(alert_type, text):
    """Append one entry to the Alerts feed. alert_type is one of:
    'open', 'tp', 'sl', 'reversed', 'manual', 'target', 'loss', 'skip',
    'error', 'diag' - used by the Alerts screen to pick a color/icon and to
    filter (Trades / Errors / Diag / All). 'diag' is for temporary
    investigation logging (e.g. app.py's 24h%-mismatch probes) that is real
    signal but not an actual error and not a trade event - keeping it a
    separate type stops it from either alarming the user as red ERROR rows
    or burying real trade alerts. Capped at 100 most-recent entries."""
    with _lock:
        alerts = _status.get("alerts", [])
        alerts.insert(0, {"type": alert_type, "text": text, "time": time.strftime("%H:%M:%S")})
        _status["alerts"] = alerts[:100]


def get():
    with _lock:
        return dict(_status)
