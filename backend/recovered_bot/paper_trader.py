import time
import json
import os
import threading
from datetime import datetime
from . import status
from . import settings_store
from . import trade_store
from . import market_ws

_lock = threading.Lock()
_positions = {}
_next_id = 1
_file = None

REST_BASE = "https://fapi.binance.com/fapi/v1"


def init(path):
    """Load any positions that were open before the app was killed/restarted."""
    global _file, _positions, _next_id
    _file = path
    if _file and os.path.exists(_file):
        try:
            with open(_file, "r") as f:
                saved = json.load(f)
            with _lock:
                _positions = {int(k): v for k, v in saved.get("positions", {}).items()}
                _next_id = saved.get("next_id", 1)
        except Exception:
            pass
    _refresh_status_positions()


def reset():
    """Clear all open positions."""
    global _positions
    with _lock:
        _positions.clear()
    _save()
    _refresh_status_positions()


def _save():
    if not _file:
        return
    try:
        with _lock:
            snapshot = {"positions": _positions, "next_id": _next_id}
        with open(_file, "w") as f:
            json.dump(snapshot, f)
    except Exception:
        pass


def _get_live_price(symbol):
    """WS-first, REST-fallback. Returns just the price (None on total
    failure) - callers that also want to show the source (LIVE_WS/
    REST_FALLBACK/OFFLINE) should call market_ws.get_best_live_price()
    directly instead."""
    price, source, error = market_ws.get_best_live_price(symbol)
    if price is None:
        status.update(market_data_error=error)
    return price


def _push_today_stats():
    stats = trade_store.get_today_stats()
    status.update(**stats)


def open_paper_trade(signal):
    global _next_id
    s = settings_store.get_all()
    entry = float(signal.entry_price)
    action = signal.action.upper()

    # Only the latest signal's trade should stay open per symbol - close any
    # existing open position(s) on this symbol first (same or opposite side).
    with _lock:
        existing_ids = [tid for tid, p in _positions.items() if p["symbol"] == signal.symbol]
    for tid in existing_ids:
        _close_position(tid, "REVERSED", entry)

    invest_amount = float(s.get("invest_amount", 10.0))
    with _lock:
        trade_id = _next_id
        _next_id += 1
        _positions[trade_id] = {
            "id": trade_id,
            "signal_no": getattr(signal, "signal_no", None),
            "symbol": signal.symbol,
            "action": action,
            "entry_price": entry,
            "invest_usdt": invest_amount,
            "leverage": float(s.get("leverage", 1)),
            "tp_mode": s.get("tp_mode", "percent"),
            "tp_value": float(s.get("tp_value", 3.0)),
            "sl_value": float(s.get("sl_value", 1.5)),
            # The strategy's own absolute SL/TP levels (same numbers sent to
            # Telegram) - unchanged, straight from strategy.py. SL is FIXED
            # for the life of the trade (no trailing/moving) since that was
            # only ever needed for the old 25%-per-TP partial-booking system,
            # which has been removed. TP1-4 are reference lines only; the
            # position fully closes (100%, one trade, no partial chunks) the
            # moment price reaches whichever level is the current target -
            # user_target_tp if the user tapped a TP box, else TP4 by default.
            "signal_stop_loss": getattr(signal, "stop_loss", None),
            "signal_tp1": getattr(signal, "tp1", None),
            "signal_tp2": getattr(signal, "tp2", None),
            "signal_tp3": getattr(signal, "tp3", None),
            "signal_tp4": getattr(signal, "tp4", None),
            "user_target_tp": None,     # manual override from tapping a TP1-4 box (None = default target TP4)
            "tp_hit_levels": [],        # TP levels (1-4) price has ever reached, even if it later retraced -
                                         # monotonic (only grows) so the UI can show a permanent tick instead
                                         # of a live fill % that disappears again on a pullback.
            "opened_at": datetime.now().strftime("%H:%M:%S"),
            "opened_at_ts": time.time(),
            "status": "OPEN",
            "ltp": entry,
            "pnl_usdt": 0.0,
            "roi_pct": 0.0,
        }
    status.add_signal(f"OPEN {signal.symbol} {action} @ {entry}")
    status.add_alert("open", f"#{trade_id} {signal.symbol} {action} @ {entry}")
    _save()
    _refresh_status_positions()
    return trade_id


def _calc_roi(pos, ltp):
    entry = pos["entry_price"]
    if entry == 0:
        return 0.0
    raw = (ltp - entry) / entry * 100.0 if pos["action"] == "BUY" else (entry - ltp) / entry * 100.0
    return raw * pos["leverage"]


def _close_position(trade_id, reason, ltp):
    """Fully close the position - one trade, full invest amount, full P&L.
    No partial-booking chunks anymore, so there's nothing to double-count."""
    with _lock:
        pos = _positions.pop(trade_id, None)
    if not pos:
        return
    roi = _calc_roi(pos, ltp)
    pnl = pos["invest_usdt"] * (roi / 100.0)
    pos.update(status=f"CLOSED_{reason}", ltp=ltp, roi_pct=roi, pnl_usdt=pnl,
               closed_at=datetime.now().strftime("%H:%M:%S"),
               closed_at_ts=time.time())
    trade_store.record_close(pos)
    _push_today_stats()
    status.add_signal(f"{reason} {pos['symbol']} P&L {pnl:+.2f} USDT ({roi:+.2f}%)")
    # Same "type" vocabulary the Alerts screen already color-codes by
    # (tp/target=green, sl/loss=red, reversed=yellow, manual=grey) -
    # scalp-target closes are a profit-taking event same as a TP, so they
    # get the "tp" alert color too.
    if reason == "SCALP_TARGET":
        alert_type = "tp"
    elif reason.startswith("TP"):
        alert_type = "tp"
    elif reason == "SL":
        alert_type = "sl"
    elif reason == "REVERSED":
        alert_type = "reversed"
    elif reason == "MANUAL":
        alert_type = "manual"
    else:
        alert_type = "manual"
    status.add_alert(alert_type, f"{reason} #{trade_id} {pos['symbol']} P&L {pnl:+.2f} USDT ({roi:+.2f}%)")
    _save()
    _refresh_status_positions()


def set_target_tp(trade_id, level):
    """Manual override from tapping a TP1-4 box on an open position card
    that price has NOT reached yet.

    level is 1-4, or None to clear back to the default target (TP4). There
    is no partial booking - the position fully closes the moment price
    reaches whichever level is the current target, nothing more."""
    with _lock:
        pos = _positions.get(trade_id)
        if not pos:
            return
        pos["user_target_tp"] = level
    _save()
    _refresh_status_positions()


def trail_sl_to_level(trade_id, level):
    """Manual trailing SL - tapping a TP1-4 box that price has ALREADY
    passed moves the STOP LOSS up to that level's price instead of closing
    the trade (closing is what set_target_tp()/the monitor loop would do
    for an already-satisfied level otherwise). The full-close target
    (user_target_tp, whatever it currently is) is untouched - the trade
    keeps running toward it, just with a tighter/locked-in SL now.

    Refuses (does nothing) if the level's price isn't actually on the
    profit side of the CURRENT active SL, i.e. it would loosen the SL
    instead of tightening it - e.g. tapping TP1 after already trailing to
    TP2 is a no-op, not a step backward."""
    with _lock:
        pos = _positions.get(trade_id)
        if not pos:
            return False
        tp_price = pos.get(f"signal_tp{level}")
        if tp_price is None:
            return False
        is_long = pos["action"] == "BUY"
        current_active_sl = pos.get("trailed_sl") or pos.get("signal_stop_loss")
        if current_active_sl is not None:
            tightens = (tp_price > current_active_sl) if is_long else (tp_price < current_active_sl)
            if not tightens:
                return False
        pos["trailed_sl"] = tp_price
    _save()
    status.add_alert("manual", f"Trailing SL: #{trade_id} {pos['symbol']} SL moved to TP{level} ({tp_price:g})")
    _refresh_status_positions()
    return True


def _stale_target_level(pos, level, ltp, is_long):
    """A tapped TP1-4 level is 'stale' if the live price has already
    advanced past a FARTHER target too - meaning this level was skipped
    over while a later level was the active target, not freshly reached
    just now. E.g. price is already between TP2 and TP3, and the user
    taps TP1: TP1's price is technically satisfied, but TP2 (farther)
    is ALSO already satisfied, so this isn't a genuine "just hit TP1"
    event. Closing a stale target counts as an SL, not a TP profit."""
    for higher in range(level + 1, 5):
        higher_price = pos.get(f"signal_tp{higher}")
        if higher_price is None:
            continue
        if is_long and ltp >= higher_price:
            return True
        if not is_long and ltp <= higher_price:
            return True
    return False


def close_position_manual(trade_id):
    """Manually close an open paper trade at a fresh live price (WS first,
    REST fallback). Refuses to close on a stale/guessed price - if both
    sources fail, does nothing and reports the error so the user can retry
    instead of getting a fake fill."""
    with _lock:
        pos = _positions.get(trade_id)
    if not pos:
        return False
    price, source, error = market_ws.get_best_live_price(pos["symbol"])
    if price is None:
        status.update(market_data_error=error, last_manual_close_error=f"{pos['symbol']}: {error}")
        return False
    _close_position(trade_id, "MANUAL", price)
    return True


def close_all_profit():
    """Manually close every open position currently in profit."""
    with _lock:
        ids = [tid for tid, p in _positions.items() if p.get("pnl_usdt", 0.0) >= 0]
    for tid in ids:
        close_position_manual(tid)


def close_all_loss():
    """Manually close every open position currently in loss."""
    with _lock:
        ids = [tid for tid, p in _positions.items() if p.get("pnl_usdt", 0.0) < 0]
    for tid in ids:
        close_position_manual(tid)


def _refresh_status_positions():
    with _lock:
        snapshot = [dict(p) for p in _positions.values()]
    status.update(open_positions=snapshot)


def get_open_symbols():
    """Symbols that currently have an open position - used by the
    min-open-positions top-up scan to avoid duplicating a symbol that's
    already open."""
    with _lock:
        return {p["symbol"] for p in _positions.values()}


def get_open_count():
    with _lock:
        return len(_positions)


def paper_trade_monitor_loop():
    print("[PaperTrade] Monitor loop started")
    while True:
        try:
            _push_today_stats()
            scalp_target = float(settings_store.get("scalp_close_roi_pct", 0.0) or 0.0)
            with _lock:
                ids = list(_positions.keys())
            for tid in ids:
                with _lock:
                    pos = _positions.get(tid)
                if not pos:
                    continue
                price, source, price_error = market_ws.get_best_live_price(pos["symbol"])
                if source == "REST_FALLBACK":
                    # Minor throttle: when the WebSocket is down/stale, every
                    # open position falls back to its own individual REST
                    # call once per loop tick (loop runs every 1s) - with
                    # several positions open that can add up to several req/s
                    # against Binance. A tiny stagger here caps it to roughly
                    # 6-7 REST fallback calls/sec instead of unbounded, without
                    # meaningfully slowing down TP/SL checks.
                    time.sleep(0.15)
                if price is None:
                    # Never act on a stale/guessed price - skip this position
                    # this cycle, try again next tick. Surface why, so a
                    # DNS/offline stretch shows up clearly instead of the
                    # position just silently not updating.
                    status.update(market_data_error=price_error)
                    continue
                ltp = price
                roi = _calc_roi(pos, ltp)
                pnl = pos["invest_usdt"] * (roi / 100.0)
                with _lock:
                    if tid in _positions:
                        _positions[tid].update(ltp=ltp, roi_pct=roi, pnl_usdt=pnl, price_source=source)
                # Record any TP1-4 reference level price has ever reached -
                # monotonic (never removed), so a later pullback doesn't erase
                # the fact that level WAS touched. Direction-aware (>= for a
                # long, <= for a short), same comparison the actual TP/SL
                # check below uses.
                is_long_for_hits = pos["action"] == "BUY"
                newly_hit = []
                for lvl in (1, 2, 3, 4):
                    lvl_price = pos.get(f"signal_tp{lvl}")
                    if lvl_price is None or lvl in pos.get("tp_hit_levels", []):
                        continue
                    reached = (ltp >= lvl_price) if is_long_for_hits else (ltp <= lvl_price)
                    if reached:
                        newly_hit.append(lvl)
                if newly_hit:
                    with _lock:
                        if tid in _positions:
                            hits = list(_positions[tid].get("tp_hit_levels", []))
                            for lvl in newly_hit:
                                if lvl not in hits:
                                    hits.append(lvl)
                            _positions[tid]["tp_hit_levels"] = hits
                sig_sl = pos.get("trailed_sl") or pos.get("signal_stop_loss")
                sig_tp1 = pos.get("signal_tp1")
                is_long = pos["action"] == "BUY"
                # Scalp target: checked BEFORE the signal's own SL/TP levels,
                # for every position independently, on its own current roi_pct
                # (already leverage-adjusted by _calc_roi) - regardless of
                # whether this specific position happens to be near its TP1-4
                # or not. This is deliberately an override, not tied to any
                # other open position - one position touching the target
                # never closes any OTHER position (that's a different,
                # not-yet-built "close everything" feature).
                if scalp_target > 0 and roi >= scalp_target:
                    _close_position(tid, "SCALP_TARGET", ltp); continue
                if sig_sl is not None and sig_tp1 is not None:
                    # SL is the strategy's own level, fixed for the life of
                    # the trade - no trailing (that was only ever tied to the
                    # removed 25%-per-TP partial-booking system).
                    hit_sl = (ltp <= sig_sl) if is_long else (ltp >= sig_sl)
                    if hit_sl:
                        _close_position(tid, "SL", ltp); continue
                    # TP1-4 are reference lines; the trade fully closes (no
                    # partial chunks) the moment price reaches the current
                    # target - whatever the user tapped, or TP4 by default.
                    target_level = pos.get("user_target_tp") or 4
                    target_price = pos.get(f"signal_tp{target_level}")
                    if target_price is not None:
                        hit_tp = (ltp >= target_price) if is_long else (ltp <= target_price)
                        if hit_tp:
                            # A manually-tapped level that's stale (price is
                            # already past a farther target too) closes as an
                            # SL, not a TP profit - see _stale_target_level().
                            if pos.get("user_target_tp") and _stale_target_level(pos, target_level, ltp, is_long):
                                _close_position(tid, "SL", ltp); continue
                            reason = f"TP{target_level}_TARGET" if pos.get("user_target_tp") else f"TP{target_level}"
                            _close_position(tid, reason, ltp); continue
                elif pos["tp_mode"] == "percent":
                    # Fallback for positions opened before this update, which
                    # don't have signal_stop_loss/signal_tp1 saved yet.
                    if roi >= pos["tp_value"]:
                        _close_position(tid, "TP", ltp); continue
                    if roi <= -pos["sl_value"]:
                        _close_position(tid, "SL", ltp); continue
                else:
                    if pnl >= pos["tp_value"]:
                        _close_position(tid, "TP", ltp); continue
                    if pnl <= -pos["sl_value"]:
                        _close_position(tid, "SL", ltp); continue
            _save()
            _refresh_status_positions()
        except Exception as e:
            print(f"[PaperTrade] Monitor error: {e}")
        # Was 5s when every check meant a REST call per position. Now that
        # get_best_live_price() reads the WS cache first (no network call
        # when WS is live), positions can be checked much more often without
        # hammering Binance - only falls back to a REST call per symbol when
        # WS is actually down/stale for it.
        time.sleep(1)
