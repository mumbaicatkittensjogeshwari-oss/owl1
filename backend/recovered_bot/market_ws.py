"""Binance Futures !miniTicker@arr WebSocket client.

SCOPE - READ THIS BEFORE TOUCHING ANYTHING ELSE IN THIS FILE:
This module ONLY feeds a live price/% cache that Market Watch (main.py's
MarketScreen) can optionally read from for a fresher displayed number. It
does NOT touch, replace, or feed:
  - fetch_initial_history() / _fetch_kline_for() (REST 3m candles)
  - history_cache / process_candle() / strategy.generate_signal()
  - refresh_watchlist() / get_current_movers() (still decide WHICH symbols
    are top gainers/losers - REST-based, unchanged, every
    WATCHLIST_REFRESH_INTERVAL seconds)
The signal engine keeps running on REST candles exactly as before. This
module can be deleted or disabled entirely and nothing about signal
generation, paper trading, or Telegram changes - Market Watch just falls
back to the REST snapshot price it already had, same as before this file
existed.

Why a plain websocket-client thread instead of asyncio/FastAPI: this is a
single Kivy process, not a server - there is no second client to serve, so
there is nothing to gain from an HTTP/SSE layer between this module and the
UI. status.py + Kivy's own Clock refresh (already polling every ~2s) is the
existing update mechanism; this module just gives it fresher numbers to read.

Thread-safety: one dict behind one lock. Reads never block on the network -
the WebSocket thread writes to the cache, everyone else only reads a
snapshot copy.
"""
import json
import threading
import time

from . import status

try:
    import websocket  # websocket-client package
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

try:
    import certifi
    _CA_CERTS = certifi.where()
except ImportError:
    _CA_CERTS = None

WS_URL = "wss://fstream.binance.com/ws/!miniTicker@arr"
STALE_AFTER_SECONDS = 8       # no push for this long on a symbol -> treat that symbol's cached value as not-fresh
DISCONNECTED_AFTER_SECONDS = 15  # no push AT ALL for this long -> overall state becomes STALE, not just per-symbol
MAX_BACKOFF = 30

_lock = threading.Lock()
_live_cache = {}          # symbol -> {"price": float, "change_pct": float, "high": float, "low": float, "volume": float, "updated_at": float}
_last_message_time = 0.0  # time.time() of the most recent successfully parsed message (any symbol)
_ws_state = "DISCONNECTED"   # one of: CONNECTING, LIVE, RECONNECTING, STALE, DISCONNECTED
_reconnect_count = 0
_last_error = None
_started = False           # guards against starting a second thread by accident


def start():
    """Start the WebSocket thread if it isn't already running. Safe to call
    more than once - only the first call actually starts anything, so
    app.py's main() can call this unconditionally without needing to track
    whether it already did."""
    global _started
    if _started:
        return
    _started = True
    if not _WS_AVAILABLE:
        _set_state("DISCONNECTED", error="websocket-client not installed")
        status.update(market_data_error="websocket-client package missing - Market Watch stays on REST snapshot only")
        return
    t = threading.Thread(target=_run_forever_with_backoff, daemon=True)
    t.start()


def get_live(symbol):
    """Returns {"price", "change_pct", "updated_at"} for symbol if we have a
    push for it that's fresh (<STALE_AFTER_SECONDS old), else None - callers
    (Market Watch) should fall back to their own REST-snapshot value on
    None, never guess or hold a stale number silently."""
    with _lock:
        entry = _live_cache.get(symbol)
    if not entry:
        return None
    if time.time() - entry["updated_at"] > STALE_AFTER_SECONDS:
        return None
    return entry


REST_PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"


def get_best_live_price(symbol):
    """WS-first, REST-fallback live price for one symbol - the single
    source every paper-trading price read (TP/SL checks, manual close,
    Close All Profit/Loss, position LTP display) should go through instead
    of a bare requests.get(). Never returns a silently-stale number: if a
    fresh WS push exists, uses it; otherwise makes one REST call; if both
    fail, returns (None, "OFFLINE", None) so the caller can skip that
    position this cycle rather than act on a guess.

    Returns (price, source, error) where source is one of
    'LIVE_WS' / 'REST_FALLBACK' / 'OFFLINE' and error is only set on
    OFFLINE (a short label from http_client.error_label)."""
    live = get_live(symbol)
    if live:
        return live["price"], "LIVE_WS", None
    try:
        from . import http_client
        resp = http_client.get_session().get(REST_PRICE_URL, params={"symbol": symbol}, timeout=8)
        if resp.status_code != 200:
            return None, "OFFLINE", http_client.error_label(resp)
        return float(resp.json()["price"]), "REST_FALLBACK", None
    except Exception as e:
        try:
            from . import http_client
            err = http_client.error_label(e)
        except Exception:
            err = str(e)
        return None, "OFFLINE", err


def get_ws_state():
    """Snapshot of connection health for a UI status badge (LIVE/STALE/etc)."""
    with _lock:
        age = (time.time() - _last_message_time) if _last_message_time else None
        return {
            "state": _ws_state,
            "connected": _ws_state == "LIVE",
            "last_update_age": age,
            "reconnect_count": _reconnect_count,
            "symbols_cached": len(_live_cache),
            "error": _last_error,
        }


def _set_state(new_state, error=None):
    global _ws_state, _last_error
    with _lock:
        _ws_state = new_state
        if error is not None:
            _last_error = error
    status.update(ws_connected=(new_state == "LIVE"), market_data_state=new_state)


def _on_open(ws):
    global _backoff
    _set_state("LIVE")
    _backoff = 1  # successful connection - forget any earlier failure streak
    print("[market_ws] connected")


def _on_message(ws, message):
    global _last_message_time
    try:
        data = json.loads(message)
    except Exception:
        return
    if not isinstance(data, list):
        return
    now = time.time()
    updated = {}
    for t in data:
        try:
            sym = t.get("s")
            close = float(t.get("c", 0))
            open_ = float(t.get("o", 0))
            if not sym or open_ == 0:
                continue
            change_pct = (close - open_) / open_ * 100.0
            updated[sym] = {
                "price": close,
                "open": open_,
                "change_pct": change_pct,
                "high": float(t.get("h", 0)),
                "low": float(t.get("l", 0)),
                "volume": float(t.get("v", 0)),
                "updated_at": now,
            }
        except (TypeError, ValueError):
            continue
    if not updated:
        return
    with _lock:
        _live_cache.update(updated)
    global _last_message_time
    _last_message_time = now
    if _ws_state != "LIVE":
        _set_state("LIVE")


def _on_error(ws, error):
    print(f"[market_ws] error: {error}")
    with _lock:
        global _last_error
        _last_error = str(error)


def _on_close(ws, close_status_code, close_msg):
    print(f"[market_ws] closed: {close_status_code} {close_msg}")


_backoff = 1  # module-level so _on_open (fired from inside run_forever) can reset it


def _run_forever_with_backoff():
    global _reconnect_count, _backoff
    while True:
        _set_state("CONNECTING" if _reconnect_count == 0 else "RECONNECTING")
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            # ping_interval/ping_timeout give us keepalive for free - no
            # manual ping/pong bookkeeping needed, websocket-client handles it.
            # sslopt: python-for-android's Python doesn't reliably wire up
            # the Android system CA trust store the way desktop Python does,
            # so the default SSL context here fails with
            # CERTIFICATE_VERIFY_FAILED even though the cert is fine -
            # pointing it at certifi's bundled CA file (already a dependency
            # via requests) fixes that on-device.
            sslopt = {"ca_certs": _CA_CERTS} if _CA_CERTS else None
            ws.run_forever(ping_interval=20, ping_timeout=10, sslopt=sslopt)
        except Exception as e:
            print(f"[market_ws] run_forever crashed: {e}")
            with _lock:
                global _last_error
                _last_error = str(e)

        # run_forever() returned - connection dropped (network change, app
        # backgrounded, Binance-side close, etc). Reconnect with exponential
        # backoff instead of hammering the endpoint.
        # NOTE: this used to keep doubling a local `backoff` variable that
        # only ever reset when the whole thread restarted (never, in
        # practice) - so after a rough patch of several reconnects it would
        # sit at MAX_BACKOFF (30s) forever, even for a connection that later
        # stayed LIVE for hours before dropping once. _on_open() now resets
        # the shared _backoff back to 1 on every successful connect, so a
        # single late drop reconnects fast again instead of waiting 30s.
        _reconnect_count += 1
        status.update(ws_reconnect_count=_reconnect_count)
        _set_state("RECONNECTING")
        time.sleep(_backoff)
        _backoff = min(_backoff * 2, MAX_BACKOFF)


def stale_watchdog_tick():
    """Call periodically (e.g. once per poll_klines loop iteration) to flip
    overall state to STALE if literally nothing has arrived in a while, even
    though the socket technically didn't close (e.g. silently hung). Cheap -
    just a time comparison, no network call."""
    if not _WS_AVAILABLE:
        return
    if _last_message_time and (time.time() - _last_message_time) > DISCONNECTED_AFTER_SECONDS:
        if _ws_state == "LIVE":
            _set_state("STALE")
