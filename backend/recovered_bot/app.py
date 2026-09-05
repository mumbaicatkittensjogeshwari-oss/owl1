import os
import time
import threading
import concurrent.futures
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from .telegram import TelegramNotifier
from .strategy import ABCDStrategy
from .paper_trader import open_paper_trade, paper_trade_monitor_loop, get_open_symbols, get_open_count
from . import status
from . import settings_store
from . import trade_store
from . import signal_log
from .http_client import get_session, get_used_weight
from . import market_ws
from . import dns_fallback

BOT_TOKEN = os.getenv("BOT_TOKEN") or settings_store.get("telegram_bot_token", "")
CHAT_ID = os.getenv("CHAT_ID") or settings_store.get("telegram_chat_id", "")
REST_BASE = "https://fapi.binance.com/fapi/v1"
WATCHLIST_REFRESH_INTERVAL = 10
last_watchlist_refresh = 0
_diag_last_logged = {}  # symbol -> time.time() of last DIAG alert (throttle, see get_current_movers)
POLL_INTERVAL = 8
INITIAL_HISTORY_LIMIT = 250
POLL_WORKERS = 10
TOPUP_SCAN_INTERVAL = 45   # seconds between wider-market top-up scans (rate-limit friendly)
TOPUP_CANDIDATE_LIMIT = 40  # how many extra (non-watchlist) symbols to check per scan
last_topup_scan = 0
_last_used_weight = 0  # x-mbx-used-weight-1m from the most recent Binance response


def _track_weight(response):
    """Record Binance's reported request-weight-used-in-the-last-minute so
    poll_klines() can back off before the API starts returning 429s, instead
    of only reacting after a ban already happened."""
    global _last_used_weight
    w = get_used_weight(response)
    if w is not None:
        _last_used_weight = w
        status.update(api_weight_1m=w)

bot = TelegramNotifier(bot_token=BOT_TOKEN, chat_id=CHAT_ID)
# Strategy params are read from settings_store (Settings screen) at import time.
# settings_store.init() is called by main.py's build() before this module is
# first imported (in safe_start), so the saved values are already loaded here.
# Changing ema_length/lookback_len in Settings takes effect on the NEXT bot
# start (app restart) - not live, since this object is only built once.
strategy = ABCDStrategy(
    lookback_len=int(settings_store.get("lookback_len", 200) or 200),
    ema_length=int(settings_store.get("ema_length", 50) or 50),
)
watchlist = set()
history_cache = {}
lock = threading.Lock()
sent_signals = set()
BOT_START_TIME = int(time.time() * 1000)
last_processed_candle = {}  # symbol -> close_time (ms) of the last CLOSED candle the strategy already ran on

def fetch_initial_history(symbol: str) -> List[Dict]:
    # Request one extra candle and drop the last one below - Binance's
    # klines endpoint always includes the currently-forming (not yet closed)
    # candle as the final element when no endTime is given. _fetch_kline_for()
    # (the live poll path) correctly uses data[-2] to only ever see CLOSED
    # candles, but this startup path used to keep every returned candle
    # including that open one. That open candle's timestamp is newer than
    # the most recent closed candle, so the very next process_candle() call
    # for the real closed candle would append it as a new entry instead of
    # updating in place (its timestamp never matches candles[-1]) - corrupting
    # ordering right after startup. Fetching limit+1 and slicing off the last
    # element keeps history_cache's length at INITIAL_HISTORY_LIMIT while
    # guaranteeing every candle in it is closed, matching the live path.
    url = f"{REST_BASE}/klines"
    params = {"symbol": symbol, "interval": "3m", "limit": INITIAL_HISTORY_LIMIT + 1}
    try:
        resp = get_session().get(url, params=params, timeout=10)
        _track_weight(resp)
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            return []
        data = data[:-1]  # drop the currently-forming candle
        candles = []
        for k in data:
            candles.append({
                'timestamp': int(k[0]),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5])
            })
        return candles
    except Exception:
        return []

IST_OFFSET = timedelta(hours=5, minutes=30)
DAY_RESET_HOUR, DAY_RESET_MINUTE = 5, 30  # crypto/Indian-trading-day rollover the user goes by


def _ist_now():
    return datetime.now(timezone.utc) + IST_OFFSET


def _current_trading_day_key(ist_dt=None):
    """Trading-day key that rolls over at 5:30 AM IST (not UTC midnight, and
    not Binance's own rolling 24h window). Returns 'YYYY-MM-DD' for whichever
    trading day is currently in progress - e.g. at 2 AM IST this still
    returns yesterday's date, since the new trading day hasn't started yet."""
    ist_dt = ist_dt or _ist_now()
    reset_today = ist_dt.replace(hour=DAY_RESET_HOUR, minute=DAY_RESET_MINUTE, second=0, microsecond=0)
    trading_date = ist_dt.date() if ist_dt >= reset_today else (ist_dt - timedelta(days=1)).date()
    return trading_date.isoformat()


def get_current_movers():
    try:
        resp = get_session().get(f"{REST_BASE}/ticker/24hr", timeout=25)
        _track_weight(resp)
        ticker_data = resp.json()
        if not isinstance(ticker_data, list):
            return [], [], []
        # No volume filter here on purpose - Binance's own Top Gainers/Losers
        # list (the one the user compares against) does NOT drop low-volume
        # pairs before ranking by 24h %, so our old ">$5M quoteVolume" filter
        # was silently excluding coins Binance itself was showing (and
        # sometimes including different ones ranks 8-10 that Binance had
        # already dropped further down), producing a different top-10 SET,
        # not just a different % for the same coin. That's the real
        # explanation for the "5 days stuck, % keeps being off" mismatch.
        valid_pairs = [t for t in ticker_data if t['symbol'].endswith('USDT')]

        # Exclude contracts that have been listed for less than a full 24h
        # (openTime/closeTime span under ~20h, same threshold the DIAG check
        # below already used to just LOG this). A brand-new contract has no
        # real "price 24h ago" yet, so its priceChangePercent from THIS
        # endpoint isn't measuring the same thing a mature pair's is - and
        # Binance's own app Ranking screen (Futures > Gainers/Losers, the
        # screen the user actually compares against) visibly does NOT show
        # these very-new listings in Gainers/Losers, even though they often
        # have the single largest raw % move on the whole exchange. Without
        # this, our top-10/bottom-10 kept pulling in exactly those coins
        # (BULLAUSDT, CATIUSDT, MAGMAUSDT, etc.) ranked by a number that
        # isn't comparable to everyone else's, producing a completely
        # different SET of symbols than Binance's own screen shows - not
        # just a different % for the same coin (that part was already fixed
        # by removing the WS % overlay in main.py). Filtering these out
        # before ranking is what actually makes the SET match, not just the
        # numbers for whichever coins happen to overlap.
        now_ms = time.time() * 1000
        def _is_mature(t):
            ot, ct = t.get('openTime'), t.get('closeTime')
            if not ot or not ct:
                return True  # no timestamps to check - don't exclude on a guess
            return (ct - ot) / 3600000.0 >= 20
        valid_pairs = [t for t in valid_pairs if _is_mature(t)]

        # Rank/display by Binance's own rolling 24h priceChangePercent - the
        # exact number Binance's own app shows. This USED to rank by %
        # change "since this trading day's 5:30 AM IST reset" instead (a
        # custom day-open snapshot), which drifts a lot from Binance's real
        # 24h % - worst right after installing the app or opening it late
        # in the day, since the very first snapshot for a new trading day
        # got taken at "whenever the app happened to next run", mislabeled
        # as if it were the true 5:30 AM price. A coin that had already
        # pumped hard before you opened the app would show a falsely SMALL
        # change here (measured only from your late snapshot onward)
        # while Binance's app correctly showed the full 24h move - exactly
        # the MARSCOINUSDT +8% (here) vs +54% (Binance) mismatch reported.
        # It also disagreed with _topup_positions() below, which already
        # used Binance's raw priceChangePercent - so the Market Watch list
        # and the wider top-up scan could rank two DIFFERENT sets of coins
        # as "top movers" for the same moment, which is the most likely
        # explanation for trades opening on a symbol that didn't look like
        # a mover on the Market Watch screen. Both now agree.
        for t in valid_pairs:
            t['_day_change_pct'] = float(t['priceChangePercent'])

        valid_pairs.sort(key=lambda x: x['_day_change_pct'], reverse=True)
        top = valid_pairs[:10]
        bottom = valid_pairs[-10:]
        gainers = [d['symbol'] for d in top]
        losers = [d['symbol'] for d in bottom]

        # DIAGNOSTIC (temporary, per user's request to prove the data path
        # before changing anything): a coin whose Binance-reported 24h %
        # doesn't match what Binance's own app shows is most explainable by
        # the coin having been LISTED less than 24h ago - there is no real
        # "price 24h ago" for a brand-new contract yet, so different Binance
        # endpoints/screens can legitimately fill that gap differently. This
        # checks each top-10 gainer/loser's own openTime/closeTime (both
        # already in the REST response) - if the span between them is
        # meaningfully short of 24h, that symbol's 24h % is not really a
        # full rolling day yet, which is a very different bug (or non-bug)
        # than "our WS/REST pipeline is inconsistent". Logged to the Alerts
        # tab (not just print/logcat) since the user reads this from the
        # phone, not a computer.
        now_t = time.time()
        try:
            for d in top + bottom:
                ot, ct = d.get('openTime'), d.get('closeTime')
                if ot and ct:
                    span_hours = (ct - ot) / 3600000.0
                    # Throttled to once per symbol per 5 minutes - this runs
                    # every WATCHLIST_REFRESH_INTERVAL (10s), so without a
                    # throttle a persistently-young listing would flood the
                    # 100-entry Alerts cap and bury real trade alerts within
                    # a couple of minutes.
                    if span_hours < 20 and (now_t - _diag_last_logged.get(d['symbol'], 0)) > 300:
                        _diag_last_logged[d['symbol']] = now_t
                        status.add_alert(
                            "diag",
                            f"DIAG {d['symbol']}: 24h%={d['_day_change_pct']:+.2f} but "
                            f"its own ticker window is only {span_hours:.1f}h old "
                            f"(recently listed) - Binance's app may show a different "
                            f"% for this exact reason, not an app bug"
                        )
        except Exception:
            pass

        # DIAGNOSTIC #2 (temporary): a single timestamped snapshot of the
        # top 8 gainers, precise to the second. Comparing our rendered
        # screen against a separately-taken Binance screenshot always has a
        # few seconds of drift built in - for coins swinging double-digit %
        # per minute that alone can make two genuinely-correct snapshots
        # look like a "bug". Alerts tab timestamps let a comparison be made
        # against Binance's OWN "as of HH:MM:SS" moment instead.
        try:
            if (now_t - _diag_last_logged.get("_snapshot", 0)) > 60:
                _diag_last_logged["_snapshot"] = now_t
                ts = datetime.now().strftime("%H:%M:%S")
                names = ", ".join(f"{d['symbol']} {d['_day_change_pct']:+.2f}%" for d in top[:8])
                status.add_alert(
                    "diag",
                    f"DIAG SNAPSHOT @ {ts} ({len(valid_pairs)} eligible symbols) top8: {names}"
                )
        except Exception:
            pass

        # DIAGNOSTIC #3 (temporary): DASHUSDT has shown the same ~30-point
        # gap vs Binance's own app across multiple separate sessions/hours,
        # which rules out both "just listed" and "just a few seconds of
        # timing drift" - a real, stable, reproducible number is coming from
        # SOMEWHERE. This logs Binance's RAW REST fields (openPrice/
        # lastPrice/priceChangePercent, completely unprocessed by us) next
        # to our WebSocket's raw fields for the same symbol, so we can see
        # exactly which of our two data sources already disagrees with
        # Binance at the point we receive it, instead of guessing further
        # downstream.
        try:
            for t in valid_pairs:
                if t['symbol'] != 'DASHUSDT':
                    continue
                if (now_t - _diag_last_logged.get("_dash_probe", 0)) > 60:
                    _diag_last_logged["_dash_probe"] = now_t
                    from . import market_ws
                    ws_live = market_ws.get_live('DASHUSDT')
                    ws_part = (f"WS: open={ws_live['open']:g} close={ws_live['price']:g} "
                               f"pct={ws_live['change_pct']:+.2f}") if ws_live else "WS: no fresh entry"
                    status.add_alert(
                        "diag",
                        f"DIAG DASH RAW: REST openPrice={t.get('openPrice')} "
                        f"lastPrice={t.get('lastPrice')} pct={t.get('priceChangePercent')} | {ws_part}"
                    )
                break
        except Exception:
            pass
        # "category" tags each mover as gainer/loser at the source (top-10 vs
        # bottom-10 by 24h %) so the Market screen's Gainers/Losers tabs can
        # filter without re-deriving it from change_pct sign (a coin near 0%
        # that's still in the bottom-10 is a "loser" for watchlist purposes
        # even if its % happens to be positive that moment).
        movers = [
            {
                "symbol": d['symbol'],
                "change_pct": d['_day_change_pct'],
                "price": float(d.get('lastPrice', 0.0)),
                "category": "gainer",
            }
            for d in top
        ] + [
            {
                "symbol": d['symbol'],
                "change_pct": d['_day_change_pct'],
                "price": float(d.get('lastPrice', 0.0)),
                "category": "loser",
            }
            for d in bottom
        ]
        return gainers, losers, movers
    except Exception as e:
        # Categorize instead of showing the raw exception - a DNS/no-internet
        # blip used to look identical to a Binance-side error in the log.
        try:
            from . import http_client
            label = http_client.error_label(e)
        except Exception:
            label = str(e)
        status.update(last_error=f"Movers fetch: {label}")
        return [], [], []

def refresh_watchlist():
    global watchlist
    gainers, losers, movers = get_current_movers()
    new_watch = set(gainers + losers)
    with lock:
        old_watch = watchlist
        dropped = old_watch - new_watch
        watchlist = new_watch
        # A symbol that rotates OUT of the top-10 and later rotates back IN
        # (very common - gainers/losers reshuffle every refresh) used to keep
        # its old cached candle history, since the fetch below only ran for
        # symbols with NO cache entry yet. That left a gap in the candle
        # series for however long it was out - the EMA50/SD-200 calculation
        # needs a continuous series, so a gap silently produced wrong
        # Entry/SL/TP numbers (and sometimes no signal at all) once the
        # symbol came back. Clearing the cache on drop forces a full,
        # gap-free re-fetch the next time it re-enters.
        for sym in dropped:
            history_cache.pop(sym, None)
            last_processed_candle.pop(sym, None)
        for sym in watchlist:
            if sym not in history_cache or len(history_cache.get(sym, [])) == 0:
                history_cache[sym] = fetch_initial_history(sym)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [Watchlist] Updated: {len(watchlist)} symbols")
    update_kwargs = dict(status="Running", symbols_tracked=len(watchlist), watchlist=sorted(watchlist))
    if movers:
        # Only refresh market_movers + the "last synced" timestamp on a
        # successful fetch - a transient Binance/network hiccup then leaves
        # the last-good data on screen (with its real age visible) instead
        # of wiping it or silently resetting "Last synced" to "just now".
        update_kwargs["market_movers"] = movers
        update_kwargs["market_synced_at"] = time.time()
    status.update(**update_kwargs)
    return watchlist

def _daily_target_hit() -> bool:
    """True if today's paper-trading P&L has already reached the configured daily target."""
    try:
        target = float(settings_store.get("daily_target_usdt", 0) or 0)
        if target <= 0:
            return False
        today_pnl = trade_store.get_today_stats().get("today_pnl_usdt", 0.0)
        return today_pnl >= target
    except Exception:
        return False

def _daily_loss_limit_hit() -> bool:
    """True if today's paper-trading P&L has already dropped to/past the configured daily loss limit."""
    try:
        limit = float(settings_store.get("daily_loss_limit_usdt", 0) or 0)
        if limit <= 0:
            return False
        today_pnl = trade_store.get_today_stats().get("today_pnl_usdt", 0.0)
        return today_pnl <= -limit
    except Exception:
        return False

def _today_str():
    return datetime.now(timezone.utc).date().isoformat()

def _bump_signal_count():
    # Persisted via trade_store (survives app restarts within the same UTC
    # day) instead of the old plain in-memory counter, which used to reset
    # to 0 on every restart and could hand out a signal_no already used
    # earlier that day by a currently-open or already-closed position.
    count = trade_store.bump_signal_count()
    status.update(signals_today=count)
    return count

def _open_signal(signal, symbol):
    """Shared by the normal watchlist flow and the min-open-positions top-up
    scan: send to Telegram, log it, and (if every gate passes) open the
    paper trade. Returns True if a trade was actually opened."""
    count = _bump_signal_count()
    signal.signal_no = count
    bot.send_signal(signal)
    ts = datetime.now().strftime("%H:%M:%S")
    status.set_last_signal(symbol, signal.action, ts)
    symbol_mode = settings_store.get_symbol_mode(symbol, "BOTH")
    symbol_auto_on = symbol_mode != "OFF"
    direction_ok = symbol_mode == "BOTH" or symbol_mode == signal.action.upper()
    global_auto_on = settings_store.get("auto_execute", True)
    target_hit = _daily_target_hit()
    loss_hit = _daily_loss_limit_hit()
    # Each possible skip reason gets its own distinct log line, so the
    # Dashboard's Recent Signals list can never look identical for a
    # successfully-opened trade and a skipped one.
    if global_auto_on and symbol_auto_on and direction_ok and not target_hit and not loss_hit:
        open_paper_trade(signal)
        status.add_signal(f"#{count} {symbol} @ {ts}")
        signal_log.record(signal, symbol, opened=True)
        return True
    elif not symbol_auto_on:
        status.add_signal(f"#{count} {symbol} @ {ts} - Auto OFF, trade skipped")
        status.add_alert("skip", f"#{count} {symbol} - Auto OFF for this symbol, trade skipped")
        signal_log.record(signal, symbol, opened=False, skip_reason="Auto OFF for this symbol")
    elif not direction_ok:
        status.add_signal(f"#{count} {symbol} @ {ts} - {symbol_mode}-only, {signal.action} signal skipped")
        status.add_alert("skip", f"#{count} {symbol} - set to {symbol_mode}-only, {signal.action} signal skipped")
        signal_log.record(signal, symbol, opened=False, skip_reason=f"{symbol_mode}-only, {signal.action} skipped")
    elif not global_auto_on:
        status.add_signal(f"#{count} {symbol} @ {ts} - Global Auto-Trade OFF, trade skipped")
        status.add_alert("skip", f"#{count} {symbol} - Global Auto-Trade OFF, trade skipped")
        signal_log.record(signal, symbol, opened=False, skip_reason="Global Auto-Trade OFF")
    elif target_hit:
        status.add_signal(f"#{count} {symbol} @ {ts} - Daily target reached, trade skipped")
        status.add_alert("target", f"Daily target reached - #{count} {symbol} skipped")
        signal_log.record(signal, symbol, opened=False, skip_reason="Daily target reached")
    elif loss_hit:
        status.add_signal(f"#{count} {symbol} @ {ts} - Daily loss limit hit, trade skipped")
        status.add_alert("loss", f"Daily loss limit hit - #{count} {symbol} skipped")
        signal_log.record(signal, symbol, opened=False, skip_reason="Daily loss limit hit")
    return False


def process_candle(symbol: str, kline_data: dict):
    global history_cache, sent_signals, last_processed_candle
    close_time = int(kline_data['t'])
    with lock:
        if symbol not in history_cache or len(history_cache[symbol]) == 0:
            history_cache[symbol] = fetch_initial_history(symbol)
        candles = history_cache[symbol]
        if candles and candles[-1]['timestamp'] == close_time:
            candles[-1] = {
                'timestamp': close_time,
                'open': float(kline_data['o']),
                'high': float(kline_data['h']),
                'low': float(kline_data['l']),
                'close': float(kline_data['c']),
                'volume': float(kline_data['v'])
            }
        else:
            new_candle = {
                'timestamp': close_time,
                'open': float(kline_data['o']),
                'high': float(kline_data['h']),
                'low': float(kline_data['l']),
                'close': float(kline_data['c']),
                'volume': float(kline_data['v'])
            }
            candles.append(new_candle)
            if len(candles) > INITIAL_HISTORY_LIMIT:
                candles = candles[-INITIAL_HISTORY_LIMIT:]
            history_cache[symbol] = candles

        # Closed-candle guarantee: this same closed candle can arrive again on
        # every poll cycle (every POLL_INTERVAL seconds) until Binance rolls
        # over to the next 3m candle - only let the strategy evaluate it once
        # per symbol, instead of re-running generate_signal() ~20+ times for
        # the same closed candle.
        already_processed = last_processed_candle.get(symbol) == close_time
        if not already_processed:
            last_processed_candle[symbol] = close_time

    if already_processed:
        return

    signal = strategy.generate_signal(history_cache[symbol], symbol)
    if signal:
        sig_key = f"{symbol}_{signal.signal_time}"
        if sig_key in sent_signals:
            return
        sent_signals.add(sig_key)
        _open_signal(signal, symbol)

def _fetch_kline_for(symbol):
    try:
        url = f"{REST_BASE}/klines"
        params = {"symbol": symbol, "interval": "3m", "limit": 2}
        resp = get_session().get(url, params=params, timeout=10)
        _track_weight(resp)
        data = resp.json()
        if not isinstance(data, list) or len(data) < 2:
            return None
        k = data[-2]
        return symbol, {
            't': k[0],
            'o': k[1],
            'h': k[2],
            'l': k[3],
            'c': k[4],
            'v': k[5]
        }
    except requests.exceptions.RequestException as e:
        # Network-level failure (DNS, connection drop, timeout) - already
        # retried internally by the shared session; just skip this symbol
        # for this cycle instead of taking down the whole poll loop.
        print(f"[Poll] Network error for {symbol}: {e}")
        return None
    except Exception as e:
        print(f"[Poll] Error for {symbol}: {e}")
        return None


def _topup_positions():
    """Minimum-open-positions feature: if fewer than the configured minimum
    positions are open, scan a WIDER gainers/losers band (ranks 11+ by
    absolute 24h % change, not just the top/bottom-10 Market Watch shows)
    for genuine strategy signals to fill the gap. This does NOT relax the
    strategy's entry criteria to force a trade, and it never trades a
    symbol that isn't a genuine mover (high-volume-but-flat blue-chip coins
    like BTC/ETH are excluded by design - see the sort key below). If the
    wider mover list genuinely has no valid signal right now, the minimum
    simply won't be hit that cycle; it never overrides the daily
    target/loss limits or a manual Auto-Trade OFF."""
    global last_topup_scan
    min_n = int(settings_store.get("min_open_positions", 0) or 0)
    if min_n <= 0:
        return
    if time.time() - last_topup_scan < TOPUP_SCAN_INTERVAL:
        return
    last_topup_scan = time.time()

    if not settings_store.get("auto_execute", True) or _daily_target_hit() or _daily_loss_limit_hit():
        return

    open_symbols = get_open_symbols()
    needed = min_n - len(open_symbols)
    if needed <= 0:
        return

    try:
        resp = get_session().get(f"{REST_BASE}/ticker/24hr", timeout=25)
        _track_weight(resp)
        ticker_data = resp.json()
        if not isinstance(ticker_data, list):
            return
        # Same volume-filter removal as get_current_movers() above, so the
        # top-up scan's wider mover band (ranks 11+) is built from the same
        # unfiltered symbol set - keeps both lists agreeing on what counts
        # as a "mover" for this app.
        valid = [t for t in ticker_data if t['symbol'].endswith('USDT')]
        # BUG FIXED: this used to sort by quoteVolume (trading volume) - which
        # ranks BTCUSDT/ETHUSDT/SOLUSDT/ADAUSDT etc at the top every single
        # time, since they're always the highest-volume pairs, even though
        # they're almost never actual top gainers/losers by %. That's exactly
        # why trades were opening on symbols nowhere near the Market Watch
        # top-10 list. Sorting by absolute price-change % instead keeps the
        # top-up pool as a genuine WIDER gainers/losers band (ranks 11+),
        # matching "only ever trade from the movers list", just deeper into it.
        valid.sort(key=lambda x: abs(float(x['priceChangePercent'])), reverse=True)
        with lock:
            current_watch = set(watchlist)
        candidates = [t['symbol'] for t in valid
                      if t['symbol'] not in open_symbols and t['symbol'] not in current_watch]
        candidates = candidates[:TOPUP_CANDIDATE_LIMIT]
    except Exception as e:
        status.update(last_error=f"Top-up scan: {e}")
        return

    opened = 0
    for sym in candidates:
        if opened >= needed:
            break
        candles = fetch_initial_history(sym)
        if len(candles) < 30:
            continue
        signal = strategy.generate_signal(candles, sym)
        if not signal:
            continue
        sig_key = f"{sym}_{signal.signal_time}"
        if sig_key in sent_signals:
            continue
        sent_signals.add(sig_key)
        if _open_signal(signal, sym):
            opened += 1


def poll_klines():
    global watchlist, last_watchlist_refresh
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=POLL_WORKERS)
    while True:
        try:
            if time.time() - last_watchlist_refresh >= WATCHLIST_REFRESH_INTERVAL:
                # This refresh attempt must run BEFORE the empty-watchlist
                # check below, not after it. Bug: if the watchlist ever goes
                # empty (e.g. a DNS/offline blip during refresh_watchlist()),
                # the old order returned via `continue` on the empty-check
                # and never reached this refresh call again - permanently
                # stuck with an empty watchlist even after the network came
                # back, since nothing ever called refresh_watchlist() a
                # second time. Now it always gets attempted every cycle
                # regardless of current watchlist size.
                refresh_watchlist()
                last_watchlist_refresh = time.time()
            current_watch = list(watchlist)
            if not current_watch:
                time.sleep(5)
                continue

            # Fetch all symbols concurrently instead of one-by-one - this was
            # the main source of the "signal/open is late" delay: with ~20
            # symbols polled serially (0.5s sleep each) a symbol near the end
            # of the list could be checked 15-25s after its candle closed.
            for result in executor.map(_fetch_kline_for, current_watch):
                if result:
                    sym, kline_data = result
                    try:
                        process_candle(sym, kline_data)
                    except Exception as e:
                        print(f"[Poll] process_candle error for {sym}: {e}")
            # Stamped after every full cycle across the watchlist completes -
            # lets the UI show "data last captured Xs ago" so the user can
            # see for themselves whether polling is actually keeping up
            # (should never lag far behind POLL_INTERVAL) instead of just
            # trusting it blindly.
            status.update(last_poll_cycle_ts=time.time())

            try:
                _topup_positions()
            except Exception as e:
                print(f"[Poll] Top-up scan error: {e}")

            # Cheap, no network call - just flips the badge to STALE if the
            # WebSocket thread has gone quiet without technically closing.
            # Does not touch candle polling or signal generation at all.
            try:
                market_ws.stale_watchdog_tick()
            except Exception:
                pass

            # Adaptive throttle: Binance's futures weight limit is 2400/min.
            # If we're already using more than 70% of that (tracked from the
            # last response's x-mbx-used-weight-1m header), slow the loop
            # down instead of ploughing ahead into a 429 IP-level ban.
            sleep_for = POLL_INTERVAL
            if _last_used_weight > 1680:
                sleep_for = POLL_INTERVAL * 2
            time.sleep(sleep_for)
        except Exception as e:
            print(f"[Poll] Loop error: {e}")
            status.update(last_error=f"Poll loop: {e}")
            time.sleep(5)

def heartbeat():
    while True:
        time.sleep(60)
        with lock:
            sym_count = len(watchlist)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [Heartbeat] Bot zinda hai! Symbols: {sym_count}")

def main():
    print("Starting TradeExitPanel bot (REST polling, pure Python)...")
    status.update(status="Starting bot...")
    # Must run before any network calls (refresh_watchlist, WS connect,
    # Telegram send, etc.) - see dns_fallback.py for why this exists.
    dns_fallback.ensure_dns_fallback()
    try:
        refresh_watchlist()
    except Exception as e:
        status.update(last_error=f"Startup: {e}")
    t1 = threading.Thread(target=poll_klines, daemon=True)
    t2 = threading.Thread(target=heartbeat, daemon=True)
    t3 = threading.Thread(target=paper_trade_monitor_loop, daemon=True)
    t1.start()
    t2.start()
    t3.start()

    # Market Watch live-ticker WebSocket - started LAST, wrapped so any
    # failure here (missing dependency, connection error, whatever) can
    # never prevent t1/t2/t3 (candle polling, heartbeat, paper trading) from
    # running. Market Watch just falls back to its REST snapshot value if
    # this doesn't come up - nothing else in the app depends on it.
    try:
        market_ws.start()
    except Exception as e:
        status.update(market_data_error=f"WebSocket startup: {e}")

    while True:
        time.sleep(10)

if __name__ == "__main__":
    main()
