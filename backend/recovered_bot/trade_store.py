import json
import os
import threading
from datetime import datetime, timezone, timedelta

_lock = threading.Lock()
_file = None
_data = {
    "date": None,           # trading-day key this data belongs to (see _today_utc below)
    "today_pnl_usdt": 0.0,
    "total_trades": 0,
    "wins": 0,
    "closed_trades": [],    # capped history, most recent last
    "signal_count_today": 0,
}
MAX_HISTORY = 200

# Mirrors app.py's _current_trading_day_key(): the user's trading day rolls
# over at 5:30 AM IST, not UTC midnight. This used to be a plain UTC-midnight
# date here while app.py's Market Watch gainers/losers already used the 5:30
# AM IST boundary - the two disagreed for the ~5.5 hour window between UTC
# midnight and 5:30 AM IST, so Today's P&L could reset at a different moment
# than the gainers/losers "today" did. Kept as a local copy (not imported
# from app.py) to avoid a circular import - app.py already imports this module.
_IST_OFFSET = timedelta(hours=5, minutes=30)
_DAY_RESET_HOUR, _DAY_RESET_MINUTE = 5, 30


def _today_utc():
    ist_dt = datetime.now(timezone.utc) + _IST_OFFSET
    reset_today = ist_dt.replace(hour=_DAY_RESET_HOUR, minute=_DAY_RESET_MINUTE, second=0, microsecond=0)
    trading_date = ist_dt.date() if ist_dt >= reset_today else (ist_dt - timedelta(days=1)).date()
    return trading_date.isoformat()


def init(path):
    global _file
    _file = path
    _load()
    _check_rollover()


def _load():
    if not _file or not os.path.exists(_file):
        with _lock:
            _data["date"] = _today_utc()
        return
    try:
        with open(_file, "r") as f:
            saved = json.load(f)
        with _lock:
            _data.update(saved)
    except Exception:
        with _lock:
            _data["date"] = _today_utc()


def _save():
    if not _file:
        return
    try:
        with _lock:
            snapshot = dict(_data)
        with open(_file, "w") as f:
            json.dump(snapshot, f)
    except Exception:
        pass


def reset():
    """Reset all trade history and today's stats."""
    with _lock:
        _data["today_pnl_usdt"] = 0.0
        _data["total_trades"] = 0
        _data["wins"] = 0
        _data["closed_trades"] = []
        _data["date"] = _today_utc()
    _save()


def _check_rollover():
    """Reset today's P&L/trades/wins when the trading day rolls over at
    5:30 AM IST - matches app.py's Market Watch trading-day boundary, not
    just app restart."""
    today = _today_utc()
    with _lock:
        changed = _data.get("date") != today
        if changed:
            _data["date"] = today
            _data["today_pnl_usdt"] = 0.0
            _data["total_trades"] = 0
            _data["wins"] = 0
            _data["signal_count_today"] = 0
    if changed:
        _save()
    return changed


def record_close(pos):
    """Call when a paper trade closes. pos must have pnl_usdt."""
    _check_rollover()
    pnl = float(pos.get("pnl_usdt", 0.0))
    with _lock:
        _data["today_pnl_usdt"] += pnl
        _data["total_trades"] += 1
        if pnl > 0:
            _data["wins"] += 1
        _data["closed_trades"].append(pos)
        _data["closed_trades"] = _data["closed_trades"][-MAX_HISTORY:]
    _save()


def bump_signal_count():
    """Increment and persist today's signal counter. Persisted to disk (unlike
    a plain in-memory variable) so restarting the app mid-day doesn't reset
    numbering back to #1 and collide with signal numbers already used earlier
    that day for currently-open or already-closed positions."""
    _check_rollover()
    with _lock:
        _data["signal_count_today"] += 1
        count = _data["signal_count_today"]
    _save()
    return count


def get_today_stats():
    _check_rollover()
    with _lock:
        total = _data["total_trades"]
        wins = _data["wins"]
        return {
            "today_pnl_usdt": _data["today_pnl_usdt"],
            "total_trades": total,
            "win_rate": (wins / total * 100.0) if total else 0.0,
            "wins": wins,
            "losses": total - wins,
            "signal_count_today": _data["signal_count_today"],
        }


def get_recent_closed(limit=50):
    """Most recent closed trades first, for the History tab in Positions screen."""
    with _lock:
        return list(reversed(_data["closed_trades"][-limit:]))


def _trading_day_key_for_ts(ts):
    """Same 5:30 AM IST trading-day boundary as _today_utc(), but for an
    arbitrary past timestamp instead of 'now' - used to group closed trades
    by the trading day they actually closed on, for the equity curve."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) + _IST_OFFSET
    reset_today = dt.replace(hour=_DAY_RESET_HOUR, minute=_DAY_RESET_MINUTE, second=0, microsecond=0)
    trading_date = dt.date() if dt >= reset_today else (dt - timedelta(days=1)).date()
    return trading_date.isoformat()


def get_equity_curve(days=30):
    """Daily realized P&L + running cumulative total, for the Dashboard
    equity curve chart. Groups every closed trade in history (not just
    today's, capped at MAX_HISTORY=200 trades total) by the trading day it
    closed on (5:30 AM IST boundary, same as Today's P&L), then returns the
    most recent `days` trading days that actually had a close, oldest
    first, as [{"date": "YYYY-MM-DD", "pnl": daily_total, "cumulative": running_total}, ...].
    Trades with no closed_at_ts (very old, pre-dating that field) are
    skipped - there is no reliable day to bucket them under.
    """
    with _lock:
        trades = list(_data["closed_trades"])

    daily = {}
    for t in trades:
        ts = t.get("closed_at_ts")
        if not ts:
            continue
        day = _trading_day_key_for_ts(ts)
        daily[day] = daily.get(day, 0.0) + float(t.get("pnl_usdt", 0.0) or 0.0)

    ordered_days = sorted(daily.keys())[-max(1, days):]
    running = 0.0
    curve = []
    for day in ordered_days:
        pnl = daily[day]
        running += pnl
        curve.append({"date": day, "pnl": pnl, "cumulative": running})
    return curve
