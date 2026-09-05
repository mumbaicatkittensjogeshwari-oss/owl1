"""Persisted log of every generated signal (not just open/closed trades),
for the Signals screen - matches what Telegram shows (Symbol, Direction,
Entry, SL, TP1-4, IST time) plus whether a trade actually opened from it.

Separate from trade_store.py on purpose: trade_store only knows about
trades that actually opened and later closed. A signal that got skipped
(Auto OFF, direction-only mode, daily target/loss hit) never becomes a
trade at all, but the user still wants to see it here - same as it still
goes to Telegram regardless of any skip reason.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()
_file = None
_signals = []  # most-recent-last, capped at MAX_HISTORY
MAX_HISTORY = 300
IST_OFFSET = timedelta(hours=5, minutes=30)


def init(path):
    global _file
    _file = path
    _load()


def _load():
    if not _file or not os.path.exists(_file):
        return
    try:
        with open(_file, "r") as f:
            data = json.load(f)
        with _lock:
            _signals.extend(data if isinstance(data, list) else [])
    except Exception:
        pass


def _save():
    if not _file:
        return
    try:
        with _lock:
            snapshot = list(_signals)
        with open(_file, "w") as f:
            json.dump(snapshot, f)
    except Exception:
        pass


def record(signal, symbol, opened, skip_reason=None):
    """Call once per generated signal, right after Telegram send - same
    place _open_signal already computes signal_no/skip reasons, so this
    never gets out of sync with what Telegram/Recent Signals showed."""
    try:
        utc_dt = datetime.strptime(signal.signal_time, "%Y-%m-%d %H:%M:%S")
    except Exception:
        utc_dt = datetime.now(timezone.utc)
    ist_dt = utc_dt + IST_OFFSET
    entry = {
        "signal_no": getattr(signal, "signal_no", None),
        "symbol": symbol,
        "action": signal.action,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "tp1": signal.tp1,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "tp4": signal.tp4,
        "signal_time": signal.signal_time,      # UTC, "%Y-%m-%d %H:%M:%S"
        "ist_time_str": ist_dt.strftime("%d %b, %I:%M %p"),
        "date_str": ist_dt.strftime("%Y-%m-%d"),  # IST calendar date, for date-grouping
        "recorded_at": time.time(),             # wall-clock, for the "Live" (<10min) window
        "opened": opened,
        "skip_reason": skip_reason,
    }
    with _lock:
        _signals.append(entry)
        del _signals[:-MAX_HISTORY]
    _save()


def get_recent(limit=100):
    """Most-recent-first."""
    with _lock:
        return list(reversed(_signals[-limit:]))
