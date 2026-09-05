import json
import os
import threading

_lock = threading.Lock()
_settings_file = None
_defaults = {
    "invest_amount": 10.0,
    "tp_mode": "percent",      # "percent" or "usdt"
    "tp_value": 3.0,
    "sl_value": 1.5,
    "leverage": 1,
    "auto_execute": True,
    "inr_rate": 91.5,
    "daily_target_usdt": 3.0,
    "daily_loss_limit_usdt": 20.0,
    "min_open_positions": 0,  # 0 = disabled. If >0, bot scans beyond the top/bottom-10
                                # watchlist for extra genuine strategy signals to keep at
                                # least this many positions open (never overrides daily
                                # target/loss limits or the Auto-Trade off switch).
    "symbol_auto_override": {},  # symbol -> bool; per-coin auto-trade on/off (missing = default True)
    "symbol_trade_mode": {},  # symbol -> "OFF"/"BUY"/"SELL"/"BOTH"; per-coin direction filter
                                # (missing = default "BOTH"). Replaces the old single AUTO/OFF
                                # button on Market Watch rows with a 4-way cycle.
    "favorites": [],  # list of favorited symbols for Market Watch
    "theme": "light",  # "light" or "dark" - applied on next app start
    "telegram_bot_token": "",  # SECURITY: never hardcode this in source - paste your
                                 # BotFather token here via Settings, after rotating any
                                 # previously-exposed one.
    "telegram_chat_id": "",
    # day_open_prices / day_open_key used to cache a "since 5:30 AM IST"
    # price snapshot for Market Watch's gainers/losers - removed, that list
    # now uses Binance's own rolling 24h % directly (matches the Binance
    # app, and matches what _topup_positions() already used). Old values
    # left harmless if present in an existing settings file on someone's
    # device; nothing reads these keys anymore.
    "scalp_close_roi_pct": 0.0,  # 0 = disabled. If >0, ANY open position closes itself the
                                   # instant ITS OWN roi_pct reaches this value - independent
                                   # of every other open position and independent of the
                                   # signal's own TP1-4/SL levels. For fast small-target
                                   # scalping (e.g. 10%) instead of waiting for TP4. Read
                                   # live every monitor tick, not frozen at position-open
                                   # time, so raising/lowering it applies to already-open
                                   # positions immediately too.
}
_settings = dict(_defaults)


def init(path):
    global _settings_file
    _settings_file = path
    _load()


def _load():
    if _settings_file and os.path.exists(_settings_file):
        try:
            with open(_settings_file, "r") as f:
                data = json.load(f)
            with _lock:
                _settings.update(data)
        except Exception:
            pass


def _save():
    if not _settings_file:
        return
    try:
        with open(_settings_file, "w") as f:
            json.dump(_settings, f)
    except Exception:
        pass


def get_all():
    with _lock:
        return dict(_settings)


def get(key, default=None):
    with _lock:
        return _settings.get(key, default)


def update(**kwargs):
    with _lock:
        _settings.update(kwargs)
    _save()


def get_symbol_auto(symbol, default=True):
    """Per-coin auto-trade override. Returns True/False; if the symbol has no
    override saved yet, returns `default` (does NOT write anything)."""
    with _lock:
        overrides = _settings.get("symbol_auto_override", {})
        if symbol not in overrides:
            return default
        return bool(overrides[symbol])


def set_symbol_auto(symbol, enabled):
    """Turn auto-trade on/off for exactly one symbol, leaving every other
    symbol and the global auto_execute setting untouched."""
    with _lock:
        overrides = dict(_settings.get("symbol_auto_override", {}))
        overrides[symbol] = bool(enabled)
        _settings["symbol_auto_override"] = overrides
    _save()


_MODE_CYCLE = ["OFF", "BUY", "SELL", "BOTH"]


def get_symbol_mode(symbol, default="BOTH"):
    """Per-coin trade-direction filter: OFF (never auto-trade this symbol),
    BUY (only take BUY signals), SELL (only take SELL signals), or BOTH
    (take either direction - old default AUTO behavior). If nothing has been
    saved for this symbol yet, falls back to the legacy symbol_auto_override
    bool (False -> OFF, True -> BOTH) so upgrades from the old single
    AUTO/OFF button don't silently reset anyone's per-coin choice, then to
    `default`."""
    with _lock:
        modes = _settings.get("symbol_trade_mode", {})
        if symbol in modes and modes[symbol] in _MODE_CYCLE:
            return modes[symbol]
        legacy = _settings.get("symbol_auto_override", {})
        if symbol in legacy:
            return "BOTH" if legacy[symbol] else "OFF"
        return default


def set_symbol_mode(symbol, mode):
    """Set the trade-direction filter for exactly one symbol. mode must be
    one of OFF/BUY/SELL/BOTH."""
    if mode not in _MODE_CYCLE:
        return
    with _lock:
        modes = dict(_settings.get("symbol_trade_mode", {}))
        modes[symbol] = mode
        _settings["symbol_trade_mode"] = modes
    _save()


def cycle_symbol_mode(symbol):
    """Advance one symbol's mode to the next in OFF -> BUY -> SELL -> BOTH ->
    OFF and return the new mode - used by the Market Watch row tap handler."""
    current = get_symbol_mode(symbol)
    next_mode = _MODE_CYCLE[(_MODE_CYCLE.index(current) + 1) % len(_MODE_CYCLE)]
    set_symbol_mode(symbol, next_mode)
    return next_mode


def get_favorite(symbol):
    """Returns True if symbol is favorited on Market Watch, else False."""
    with _lock:
        favs = _settings.get("favorites", [])
        return symbol in favs


def set_favorite(symbol, enabled):
    """Add or remove a symbol from the favorites list."""
    with _lock:
        favs = list(_settings.get("favorites", []))
        if enabled and symbol not in favs:
            favs.append(symbol)
        elif not enabled and symbol in favs:
            favs.remove(symbol)
        _settings["favorites"] = favs
    _save()
