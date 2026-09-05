import threading
import time
import os
import traceback
import requests
from datetime import datetime, timedelta
from kivy.app import App
from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen, NoTransition
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line, Ellipse
from kivy.properties import NumericProperty

BOT_TOKEN = os.getenv("8994572465:AAHyScKAq_V0FzVYnijJvOBuvMws2RbrRck", "")
CHAT_ID = os.getenv("885150597", "")

# ---- Theme (Light/Dark) ----
# Read the saved theme choice once at startup. Wrapped in try/except like the
# other deferred recovered_bot imports elsewhere in this file, so a failure
# here can never crash the UI before it even shows - it just falls back to
# the light theme. Theme changes take effect on next app restart (not live),
# since colors are baked into widgets as they're created.
try:
    from recovered_bot import settings_store as _theme_settings_store
    _THEME = _theme_settings_store.get("theme", "light")
except Exception:
    _THEME = "light"

_LIGHT_PALETTE = {
    "BG": (0.96, 0.96, 0.97, 1),
    "CARD_BG": (1, 1, 1, 1),
    "TEXT": (0.08, 0.08, 0.1, 1),
    "BORDER": (0.88, 0.88, 0.91, 1),
    "TRACK_BG": (0.90, 0.90, 0.93, 1),
    "ACCENT": (0.23, 0.51, 0.96, 1),    # unchanged - neon blue
    "SLATE": (0.392, 0.455, 0.545, 1),  # unchanged - #64748b muted text
}
# "Dark-terminal" redesign: previously a deep-slate/glass dark theme (blue
# accent on navy-slate) - this pass pushes it toward an actual trading-
# terminal look (near-black background, cyan-teal accent instead of blue,
# green-tinted muted text) without touching the LIGHT palette at all, so
# light mode is byte-for-byte the same as before this change.
_DARK_PALETTE = {
    "BG": (0.015, 0.02, 0.02, 1),          # near-pure black, terminal screen
    "CARD_BG": (0.05, 0.065, 0.062, 1),    # dark charcoal panel, faint green undertone
    "TEXT": (0.87, 0.95, 0.92, 1),         # pale mint-white - reads as "phosphor" without
                                             # sacrificing contrast/readability
    "BORDER": (0.0, 0.85, 0.8, 0.16),      # translucent cyan-teal hairline
    "TRACK_BG": (0.09, 0.11, 0.11, 1),
    "ACCENT": (0.0, 0.85, 0.8, 1),         # cyan-teal - distinct from profit-green,
                                             # loss-red and warning-yellow so interactive
                                             # elements never get mistaken for a P&L color
    "SLATE": (0.42, 0.55, 0.52, 1),        # muted green-gray, same role as light's SLATE
}
_PALETTE = _DARK_PALETTE if _THEME == "dark" else _LIGHT_PALETTE

# ---- Palette (Tailwind-style) ----
BG = _PALETTE["BG"]
CARD_BG = _PALETTE["CARD_BG"]
GREEN = (0.0, 0.75, 0.45, 1)       # Emerald - profit/buy accent (your spec)
RED = (0.93, 0.27, 0.27, 1)        # Crimson - loss/sell accent (your spec)
SLATE = _PALETTE["SLATE"]
YELLOW = (0.75, 0.55, 0.0, 1)
TEXT = _PALETTE["TEXT"]
ACCENT = _PALETTE["ACCENT"]
BORDER = _PALETTE["BORDER"]
TRACK_BG = _PALETTE["TRACK_BG"]

# markup=True hex equivalents (Kivy markup wants hex strings, not tuples)
GREEN_HEX = "#00BF73"
RED_HEX = "#ED4545"
SLATE_HEX = "#64748b"
ACCENT_HEX = "#3B82F6"

USDT_TO_INR = 88.0  # approx placeholder rate - update this number whenever you want a fresher rate


def calc_top_pad():
    """Top safe padding as a fraction of the current window height, so it
    stays sensible whether the app is full-screen or in a resized floating
    window, instead of a fixed pixel value tuned for one screen size."""
    try:
        h = Window.height
    except Exception:
        h = 1920
    return max(dp(36), min(dp(80), h * 0.045))


def calc_bottom_pad():
    """Bottom safe padding (keeps the nav bar clear of gesture-nav / floating
    window chrome) - also scales with window height."""
    try:
        h = Window.height
    except Exception:
        h = 1920
    return max(dp(36), min(dp(70), h * 0.04))


def _apply_pad(widget, top=None, bottom=None):
    if widget is None:
        return
    pad = list(widget.padding)
    if top is not None:
        pad[1] = top
    if bottom is not None:
        pad[3] = bottom
    widget.padding = pad


def _get_symbol_mode(symbol):
    """Current per-coin trade-direction mode: OFF/BUY/SELL/BOTH. Defensive:
    if settings_store isn't ready for any reason, default to BOTH (same
    trades-both-ways behavior the old always-on AUTO button had) so this
    never blocks trading by accident."""
    try:
        from recovered_bot import settings_store
        return settings_store.get_symbol_mode(symbol, "BOTH")
    except Exception:
        return "BOTH"


def _cycle_symbol_mode(symbol):
    """Advance this symbol to the next mode in OFF -> BUY -> SELL -> BOTH ->
    OFF and return the new mode."""
    try:
        from recovered_bot import settings_store
        return settings_store.cycle_symbol_mode(symbol)
    except Exception:
        return _get_symbol_mode(symbol)


_MODE_BTN_COLOR = {"OFF": SLATE, "BUY": GREEN, "SELL": RED, "BOTH": ACCENT}


def _get_favorite(symbol):
    """True/False - is this symbol favorited on Market Watch."""
    try:
        from recovered_bot import settings_store
        return settings_store.get_favorite(symbol)
    except Exception:
        return False


def _set_favorite(symbol, enabled):
    try:
        from recovered_bot import settings_store
        settings_store.set_favorite(symbol, enabled)
    except Exception:
        pass


def _get_daily_target():
    try:
        from recovered_bot import settings_store
        return float(settings_store.get("daily_target_usdt", 0) or 0)
    except Exception:
        return 0.0


def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        if len(text) > 3500:
            text = text[-3500:]
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass


class CardLayout(BoxLayout):
    """Reusable rounded-corner card base. Every card widget used to repeat
    the same canvas-rect + border + _update_rect boilerplate; this is that
    logic written once so new cards just extend this class."""
    radius = dp(16)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bg_color = CARD_BG  # instance-level (not class-level) so this always
        # reads the CURRENT global - see the theme re-read in App.build(); a
        # class attribute here would freeze on whatever CARD_BG was at import
        # time (always "light"), same bug as the one just fixed for BG below.
        with self.canvas.before:
            Color(*self.bg_color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
            Color(*BORDER)
            self._border = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, self.radius),
                width=1)
        self.bind(pos=self._update_rect, size=self._update_rect)

    def _update_rect(self, *args):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, self.radius)


class MiniProgressBar(BoxLayout):
    """Small canvas-drawn progress bar (no dependency on Kivy's default
    ProgressBar skin, so the fill color always matches our palette)."""
    def __init__(self, pct=0.0, color=SLATE, **kwargs):
        super().__init__(**kwargs)
        self.pct = max(0.0, min(100.0, pct))
        self.bar_color = color
        with self.canvas.before:
            Color(*TRACK_BG)
            self._track = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(4)])
            Color(*self.bar_color)
            self._fill = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[dp(4)])
        self.bind(pos=self._update, size=self._update)

    def _update(self, *args):
        self._track.pos = self.pos
        self._track.size = self.size
        fill_w = self.width * (self.pct / 100.0)
        self._fill.pos = self.pos
        self._fill.size = (fill_w, self.height)

    def set_pct(self, pct, color=None):
        self.pct = max(0.0, min(100.0, pct))
        if color:
            self.bar_color = color
        self._update()


class RiskRewardBar(BoxLayout):
    """The SL -> Entry -> TP1..TP4 dot-marker line you asked for (matches
    the reference screenshot). Unlike MiniProgressBar (which only shows %
    toward the single next target), this draws the WHOLE trade plan on one
    fixed scale from SL to the far TP: a dot for SL, a dot for Entry, a dot
    for each TP level (green if already booked, grey if still ahead), and a
    filled track showing where the live price currently sits between them."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dots = []  # list of (Color instr, Ellipse instr)
        with self.canvas.before:
            Color(*TRACK_BG)
            self._track = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(3)])
            Color(*SLATE)
            self._fill = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[dp(3)])
        self.bind(pos=self._redraw, size=self._redraw)
        self._plan = None  # (sl, entry, tps, ltp, is_long, target_level)

    def set_plan(self, sl, entry, tps, ltp, is_long, target_level):
        """tps: list of up to 4 TP prices (None entries allowed/skipped).
        target_level (1-4): which TP is the actual full-close target -
        that dot is highlighted; the others are just reference lines."""
        self._plan = (sl, entry, [t for t in tps if t is not None], ltp, is_long, target_level)
        self._redraw()

    def _redraw(self, *args):
        self._track.pos = self.pos
        self._track.size = self.size
        for color_instr, dot_instr in self._dots:
            self.canvas.after.remove(color_instr)
            self.canvas.after.remove(dot_instr)
        self._dots = []
        if not self._plan or self.width <= 0:
            self._fill.size = (0, self.height)
            return
        sl, entry, tps, ltp, is_long, target_level = self._plan
        far_tp = tps[-1] if tps else entry
        # SL is always anchored at x=0 (left) and the far TP at x=1 (right),
        # regardless of BUY/SELL - mapping by raw price value instead (low
        # price = left) used to flip the whole bar left-to-right for SELL
        # trades (since SL > entry > TPs there), which is what made the line
        # look like it randomly reversed direction between positions.
        span = far_tp - sl
        if span == 0:
            self._fill.size = (0, self.height)
            return

        def x_for(price):
            return self.x + self.width * max(0.0, min(1.0, (price - sl) / span))

        # Live-price fill from Entry toward wherever ltp currently is.
        entry_x = x_for(entry)
        ltp_x = x_for(ltp) if ltp is not None else entry_x
        fill_x0, fill_x1 = sorted([entry_x, ltp_x])
        in_profit = (ltp >= entry) if is_long else (ltp <= entry)
        with self.canvas.after:
            c = Color(*(GREEN if in_profit else RED))
            f = RoundedRectangle(pos=(fill_x0, self.y), size=(max(0, fill_x1 - fill_x0), self.height),
                                  radius=[dp(3)])
        self._dots.append((c, f))

        dot_r = dp(4)

        def add_dot(price, color):
            with self.canvas.after:
                c = Color(*color)
                d = Ellipse(pos=(x_for(price) - dot_r, self.center_y - dot_r), size=(dot_r * 2, dot_r * 2))
            self._dots.append((c, d))

        add_dot(sl, RED)
        add_dot(entry, ACCENT)
        for i, tp in enumerate(tps):
            add_dot(tp, ACCENT if (i + 1) == target_level else SLATE)


class LevelBox(BoxLayout):
    """One self-contained row for a SINGLE Entry/SL/TPn level: name, price,
    % and USDT, with a progress fill that is drawn using ONLY this widget's
    own pos/size (self.x to self.x+self.width, exactly like MiniProgressBar
    above) - it is structurally impossible for this fill to render outside
    this box or bleed into a neighbouring level's box, since every other
    level gets its own separate LevelBox instance with its own canvas.
    Replaces the old design where one shared RiskRewardBar drew a single
    line/fill across the whole card while a separate row of TP buttons sat
    below it, unaligned - the two never visually lined up level-by-level."""
    radius = dp(10)
    fill_pct = NumericProperty(0.0)

    def __init__(self, on_tap=None, **kwargs):
        super().__init__(orientation='horizontal', padding=(dp(10), dp(4)),
                          spacing=dp(6), size_hint_y=None, height=dp(34), **kwargs)
        with self.canvas.before:
            Color(*CARD_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
            # Fill is drawn AFTER the background but BEFORE the border below,
            # so the border always renders on top and stays crisp regardless
            # of how wide the fill currently is.
            self._fill_color_instr = Color(0, 0, 0, 0)
            self._fill_rect = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[self.radius])
            Color(*BORDER)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self.radius),
                                 width=1)
        self.bind(pos=self._update_rect, size=self._update_rect, fill_pct=self._update_fill)

        self.name_label = Label(text="", font_size=dp(11), bold=True, color=TEXT,
                                 size_hint_x=0.20, halign='left', valign='middle')
        self.name_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.price_label = Label(text="", font_size=dp(11), color=TEXT,
                                  size_hint_x=0.32, halign='left', valign='middle', shorten=True)
        self.price_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.pct_label = Label(text="", font_size=dp(11), color=SLATE,
                                size_hint_x=0.22, halign='right', valign='middle')
        self.pct_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.usdt_label = Label(text="", font_size=dp(11), bold=True, color=SLATE,
                                 size_hint_x=0.26, halign='right', valign='middle')
        self.usdt_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.add_widget(self.name_label)
        self.add_widget(self.price_label)
        self.add_widget(self.pct_label)
        self.add_widget(self.usdt_label)

        self._on_tap = on_tap
        if on_tap:
            self.bind(on_touch_down=self._handle_touch)

    def _handle_touch(self, instance, touch):
        if self.collide_point(*touch.pos):
            self._on_tap()
            return True
        return False

    def _update_rect(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, self.radius)
        self._update_fill()

    def _update_fill(self, *args):
        fill_w = self.width * max(0.0, min(1.0, self.fill_pct / 100.0))
        self._fill_rect.pos = self.pos
        self._fill_rect.size = (fill_w, self.height)

    def set_level(self, name, price, pct_text, usdt_text, fill_pct, fill_color,
                  name_color=None, highlighted=False, hit=False):
        # "hit" is a ONE-WAY, persisted flag (price reached this level at
        # some point in the trade's life, even if it has since pulled back)
        # - separate from fill_pct, which is a LIVE distance-to-level number
        # that legitimately goes up and down as price moves. Without this,
        # a level the price already touched once could look "not reached"
        # again after a retrace, with no way to tell "still approaching"
        # apart from "already got there and came back".
        self.name_label.text = f"{name} \u2713" if hit else name
        self.name_label.color = name_color or TEXT
        self.price_label.text = f"{price:g}" if price is not None else "--"
        self.pct_label.text = pct_text
        self.usdt_label.text = usdt_text
        self._fill_color_instr.rgba = fill_color
        self._border.width = 1.6 if highlighted else 1
        # Animated instead of snapping straight to the new value - each
        # 0.5s data refresh used to make every fill jump instantly, which
        # read as jittery/flickering rather than a smoothly moving marker.
        Animation.cancel_all(self, 'fill_pct')
        Animation(fill_pct=fill_pct, duration=0.4, t='out_quad').start(self)


class StatCard(CardLayout):
    """Small stat card: muted title + bold value - used for the dashboard grid."""
    def __init__(self, title, value="--", value_color=TEXT, **kwargs):
        super().__init__(orientation='vertical', padding=(dp(14), dp(10)), spacing=dp(4),
                          size_hint_y=None, height=dp(78), **kwargs)
        self.title_label = Label(text=title, font_size=dp(13), color=SLATE,
                                  size_hint_y=None, height=dp(20), halign='left', valign='middle')
        self.title_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.value_label = Label(text=value, font_size=dp(22), bold=True, color=value_color,
                                  size_hint_y=None, height=dp(32), halign='left', valign='middle')
        self.value_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.add_widget(self.title_label)
        self.add_widget(self.value_label)

    def set_value(self, text, color=None):
        self.value_label.text = text
        if color:
            self.value_label.color = color


class HeroPnlCard(CardLayout):
    """Bigger highlighted card just for Today's P&L - separates it visually
    from the rest of the stat grid so it reads as the most important number
    on the screen."""
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', padding=(dp(16), dp(14)), spacing=dp(4),
                          size_hint_y=None, height=dp(104), **kwargs)
        self.title_label = Label(text="TODAY'S P&L", font_size=dp(13), color=SLATE,
                                  size_hint_y=None, height=dp(18), halign='left', valign='middle')
        self.title_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.value_label = Label(text="0.00 USDT", font_size=dp(30), bold=True, color=TEXT,
                                  size_hint_y=None, height=dp(42), halign='left', valign='middle')
        self.value_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.sub_label = Label(text="", font_size=dp(13), color=SLATE,
                                size_hint_y=None, height=dp(18), halign='left', valign='middle')
        self.sub_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.add_widget(self.title_label)
        self.add_widget(self.value_label)
        self.add_widget(self.sub_label)

    def set_value(self, main_text, sub_text, color=TEXT):
        self.value_label.text = main_text
        self.value_label.color = color
        self.sub_label.text = sub_text


_WS_BADGE = {"LIVE": ("LIVE", GREEN), "CONNECTING": ("CONNECTING", SLATE),
             "RECONNECTING": ("RECONNECTING", YELLOW), "STALE": ("STALE", RED),
             "DISCONNECTED": ("REST ONLY", SLATE)}


class HealthStrip(CardLayout):
    """One-line bot-health strip: WS connection state, WS latency (age of
    the last push), and REST 'last synced Xs ago'. Same underlying numbers
    MarketScreen's header already showed, but this class makes them reusable
    on every screen (Dashboard/Positions/Alerts) so the person doesn't have
    to go to Market Watch just to check whether data is actually flowing
    before trusting anything else on screen. Refresh is cheap (label.text
    writes only) - safe to call every ~0.5-2s from each screen's refresh()."""
    def __init__(self, **kwargs):
        super().__init__(orientation='horizontal', padding=(dp(12), dp(6)), spacing=dp(8),
                          size_hint_y=None, height=dp(34), **kwargs)
        # No bullet/dot glyph here on purpose - Kivy's bundled Android font
        # has no glyph for characters like U+25CF (renders as a tofu/box
        # on-device, same bug class already fixed for the LIVE badges and
        # star icons elsewhere in this file). The colored "WS <STATE>" text
        # alone carries the state, same pattern used in MarketScreen already.
        self.ws_label = Label(text="WS --", font_size=dp(12), bold=True, color=SLATE,
                               size_hint_x=0.34, halign='left', valign='middle')
        self.ws_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.latency_label = Label(text="latency --", font_size=dp(11), color=SLATE,
                                    size_hint_x=0.30, halign='left', valign='middle')
        self.latency_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.synced_label = Label(text="synced --", font_size=dp(11), color=SLATE,
                                   size_hint_x=0.36, halign='right', valign='middle')
        self.synced_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.add_widget(self.ws_label)
        self.add_widget(self.latency_label)
        self.add_widget(self.synced_label)

    def refresh(self, data):
        try:
            from recovered_bot import market_ws
            ws_state = market_ws.get_ws_state()
        except Exception:
            ws_state = {"state": "DISCONNECTED", "last_update_age": None}

        label_txt, color = _WS_BADGE.get(ws_state.get("state"), ("--", SLATE))
        self.ws_label.text = f"WS {label_txt}"
        self.ws_label.color = color

        age = ws_state.get("last_update_age")
        if ws_state.get("state") == "LIVE" and age is not None:
            self.latency_label.text = f"latency {age*1000:.0f}ms" if age < 1 else f"latency {age:.1f}s"
            self.latency_label.color = GREEN if age < 3 else YELLOW
        elif age is not None:
            self.latency_label.text = f"no push {int(age)}s"
            self.latency_label.color = RED if age > 15 else YELLOW
        else:
            self.latency_label.text = "latency --"
            self.latency_label.color = SLATE

        synced_at = data.get("market_synced_at")
        if synced_at:
            sage = max(0, time.time() - synced_at)
            self.synced_label.text = f"synced {int(sage)}s ago"
            self.synced_label.color = GREEN if sage < 30 else (YELLOW if sage < 90 else RED)
        else:
            self.synced_label.text = "synced --"
            self.synced_label.color = SLATE


class EquityCurveWidget(BoxLayout):
    """Simple canvas-drawn cumulative-equity line chart - no plotting
    dependency, matches the plain-Kivy stack constraint. Draws the running
    total of daily realized P&L (trade_store.get_equity_curve) as a single
    polyline, green above zero / red below, with a faint zero-line and
    min/max labels. Redraws whenever new curve data is pushed in via
    set_data(); resizes cleanly since points are recomputed from the widget's
    current size every time."""
    def __init__(self, **kwargs):
        super().__init__(size_hint_y=None, height=dp(140), **kwargs)
        self._curve = []
        with self.canvas.before:
            Color(*TRACK_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
        with self.canvas:
            self._zero_color = Color(*SLATE)
            self._zero_line = Line(points=[], width=1)
            self._line_color = Color(*ACCENT)
            self._line = Line(points=[], width=dp(1.6))
        self.bind(pos=self._redraw, size=self._redraw)

    def set_data(self, curve):
        self._curve = curve or []
        self._redraw()

    def _redraw(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        if len(self._curve) < 2:
            self._line.points = []
            self._zero_line.points = []
            return

        values = [c["cumulative"] for c in self._curve]
        vmin, vmax = min(values + [0.0]), max(values + [0.0])
        span = (vmax - vmin) or 1.0
        pad = dp(8)
        x0, y0 = self.x + pad, self.y + pad
        w = max(1.0, self.width - 2 * pad)
        h = max(1.0, self.height - 2 * pad)

        def to_xy(i, v):
            px = x0 + (i / (len(values) - 1)) * w
            py = y0 + ((v - vmin) / span) * h
            return px, py

        pts = []
        for i, v in enumerate(values):
            px, py = to_xy(i, v)
            pts += [px, py]
        self._line.points = pts
        self._line_color.rgba = GREEN if values[-1] >= 0 else RED

        zy = y0 + ((0.0 - vmin) / span) * h
        self._zero_line.points = [x0, zy, x0 + w, zy]


class DashboardScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Whole dashboard is wrapped in an outer ScrollView so nothing ever
        # gets clipped off the bottom of the screen (below the nav bar) -
        # Recent Signals and Log/Error each keep their own fixed-height
        # inner scroll area, but the page as a whole scrolls too so both
        # sections are always fully reachable.
        root = BoxLayout(orientation='vertical', padding=(dp(10), calc_top_pad(), dp(10), dp(10)),
                          spacing=dp(10), size_hint_y=None)
        root.bind(minimum_height=root.setter('height'))
        self.root_layout = root

        self.health_strip = HealthStrip()
        root.add_widget(self.health_strip)

        self.bot_status_card = StatCard("BOT STATUS", "Starting...", YELLOW)
        root.add_widget(self.bot_status_card)

        # Hero row: Open Positions / Open P&L / Win Rate - an at-a-glance
        # summary row above everything else, per the deferred Dashboard
        # redesign spec. Read-only display cards; doesn't change how
        # positions are opened/closed or how win_rate/pnl_usdt are computed
        # elsewhere - just surfaces numbers that already exist in status data.
        hero_row = BoxLayout(size_hint_y=None, height=dp(78), spacing=dp(10))
        self.open_positions_card = StatCard("OPEN POSITIONS", "0", TEXT)
        self.open_pnl_card = StatCard("OPEN P&L", "0.00", TEXT)
        self.win_rate_hero_card = StatCard("WIN RATE", "0.0%", TEXT)
        hero_row.add_widget(self.open_positions_card)
        hero_row.add_widget(self.open_pnl_card)
        hero_row.add_widget(self.win_rate_hero_card)
        root.add_widget(hero_row)

        self.pnl_hero = HeroPnlCard()
        root.add_widget(self.pnl_hero)

        # Equity curve - cumulative realized P&L over recent trading days,
        # from trade_store.get_equity_curve() (trade_store.py already had
        # all the closed-trade data needed; this just aggregates+draws it).
        root.add_widget(Label(text="Equity Curve (last 14 days):", size_hint_y=None, height=dp(24),
                               font_size=dp(13), color=SLATE, halign='left'))
        equity_row = BoxLayout(size_hint_y=None, height=dp(140))
        self.equity_curve = EquityCurveWidget()
        equity_row.add_widget(self.equity_curve)
        root.add_widget(equity_row)
        self.equity_summary_label = Label(text="", font_size=dp(11), color=SLATE,
                                           size_hint_y=None, height=dp(18), halign='left', valign='middle')
        self.equity_summary_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        root.add_widget(self.equity_summary_label)

        stats_row = BoxLayout(size_hint_y=None, height=dp(78), spacing=dp(10))
        self.symbols_card = StatCard("TRACKED", "0", TEXT)
        self.trades_card = StatCard("TRADES / WIN%", "0 | 0.0%", TEXT)
        stats_row.add_widget(self.symbols_card)
        stats_row.add_widget(self.trades_card)
        root.add_widget(stats_row)

        self.signals_today_card = StatCard("SIGNALS TODAY", "0", TEXT)
        root.add_widget(self.signals_today_card)

        # Data-sync check the user asked for directly: lets them see for
        # themselves whether candle data is actually being captured live,
        # instead of just trusting the app. Two numbers: countdown to the
        # next 3-min candle close (pure wall-clock math, same alignment
        # Binance's own klines use - no backend needed for this half), and
        # how long ago the poll loop last finished a full cycle across the
        # watchlist (from the backend - status.last_poll_cycle_ts). If that
        # second number is ever much bigger than ~10-15s, something's stuck
        # (network, DNS, rate-limit backoff etc) even though the countdown
        # keeps ticking regardless (it's just a clock, not proof of data).
        self.sync_check_card = StatCard("NEXT CANDLE / LAST DATA CAPTURED", "-- | --", TEXT)
        self.sync_check_card.value_label.font_size = dp(14)  # this card's value is a status
        # line, not a big number - StatCard's default dp(22) wrapped/clipped
        # a string this long
        self.sync_check_card.height = dp(64)
        root.add_widget(self.sync_check_card)

        # Telegram delivery status - previously a failed send was only ever
        # printed to bot.log (invisible from inside the app), so a dead
        # token or a network drop could silently stop every future signal
        # with nothing on screen to explain why. This shows the outcome of
        # the most recent send attempt.
        self.telegram_card = StatCard("TELEGRAM", "Not sent yet", SLATE)
        root.add_widget(self.telegram_card)

        # Daily target banner - collapsed (height 0) until a target > 0 is
        # configured in settings, so it doesn't show an empty card by default.
        self.target_card = CardLayout(orientation='vertical', padding=(dp(14), dp(10)),
                                       spacing=dp(4), size_hint_y=None, height=0, opacity=0)
        self.target_title = Label(text="DAILY TARGET", font_size=dp(12), color=SLATE,
                                   size_hint_y=None, height=dp(16), halign='left', valign='middle')
        self.target_title.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.target_value = Label(text="", font_size=dp(15), bold=True, color=TEXT,
                                   size_hint_y=None, height=dp(22), halign='left', valign='middle')
        self.target_value.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.target_card.add_widget(self.target_title)
        self.target_card.add_widget(self.target_value)
        root.add_widget(self.target_card)

        # Auto-trading-paused banner - collapsed (height 0) until refresh()
        # detects the daily target or daily loss limit has been hit.
        self.paused_banner = CardLayout(orientation='vertical', padding=(dp(14), dp(10)),
                                         size_hint_y=None, height=0, opacity=0)
        self.paused_label = Label(text="", font_size=dp(14), bold=True, color=RED,
                                   halign='left', valign='middle')
        self.paused_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.paused_banner.add_widget(self.paused_label)
        root.add_widget(self.paused_banner)

        root.add_widget(Label(text="Recent Signals:", size_hint_y=None, height=dp(28),
                               font_size=dp(15), color=SLATE, halign='left'))
        signals_scroll = ScrollView(size_hint=(1, None), height=dp(160))
        self.signals_label = Label(text="No signals yet.", font_size=dp(14), size_hint_y=None,
                                    halign='left', valign='top', color=(0.1, 0.35, 0.6, 1))
        self.signals_label.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        self.signals_label.bind(texture_size=lambda inst, ts: setattr(inst, 'height', ts[1]))
        signals_scroll.add_widget(self.signals_label)
        root.add_widget(signals_scroll)

        root.add_widget(Label(text="Log / Error:", size_hint_y=None, height=dp(28),
                               font_size=dp(15), color=SLATE, halign='left'))
        self.error_scroll = ScrollView(size_hint=(1, None), height=dp(220))
        self.error_label = Label(font_size=dp(13), size_hint_y=None, halign='left', valign='top',
                                  color=RED)
        self.error_label.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        self.error_label.bind(texture_size=lambda inst, ts: setattr(inst, 'height', ts[1]))
        self.error_scroll.add_widget(self.error_label)
        root.add_widget(self.error_scroll)

        outer_scroll = ScrollView(size_hint=(1, 1))
        outer_scroll.add_widget(root)
        self.add_widget(outer_scroll)

    def refresh(self, data):
        self.health_strip.refresh(data)
        status = data.get("status", "Unknown")
        self.bot_status_card.set_value(status, self._status_color(status))
        self.symbols_card.set_value(str(data.get("symbols_tracked", 0)))

        # Equity curve - only re-fetch/redraw every ~10s (not every 0.5s
        # refresh tick) since trade_store.get_equity_curve() re-scans up to
        # 200 closed trades and the underlying data only changes when a
        # trade closes, not continuously like price data does.
        now_ec = time.time()
        if now_ec - getattr(self, "_last_equity_fetch", 0) > 10:
            self._last_equity_fetch = now_ec
            try:
                from recovered_bot import trade_store
                curve = trade_store.get_equity_curve(14)
            except Exception:
                curve = []
            self.equity_curve.set_data(curve)
            if curve:
                best = max(c["cumulative"] for c in curve)
                worst = min(c["cumulative"] for c in curve)
                self.equity_summary_label.text = (
                    f"Current {curve[-1]['cumulative']:+.2f}  |  Best {best:+.2f}  |  Worst {worst:+.2f} USDT")
            else:
                self.equity_summary_label.text = "No closed trades yet."

        pnl = data.get("today_pnl_usdt", 0.0)
        pnl_color = GREEN if pnl > 0 else (RED if pnl < 0 else TEXT)
        from recovered_bot import settings_store as _dash_settings
        inr_rate = float(_dash_settings.get("inr_rate", USDT_TO_INR) or USDT_TO_INR)
        pnl_inr = pnl * inr_rate
        self.pnl_hero.set_value(f"{pnl:+.2f} USDT", f"\u20b9{pnl_inr:+.0f}", pnl_color)

        trades = data.get("total_trades", 0)
        win_rate = data.get("win_rate", 0.0)
        self.trades_card.set_value(f"{trades} | {win_rate:.1f}%")
        self.signals_today_card.set_value(str(data.get("signals_today", 0)))

        # Hero row: open_positions comes straight from paper_trader's live
        # snapshot (already updated every ~1s with current ltp/pnl_usdt in
        # paper_trade_monitor_loop) - summing pnl_usdt here gives unrealized
        # P&L across every currently-open position, distinct from pnl_hero
        # below which is today's REALIZED P&L from closed trades only.
        open_positions = data.get("open_positions", [])
        open_count = len(open_positions)
        open_pnl = sum(float(p.get("pnl_usdt", 0.0) or 0.0) for p in open_positions)
        open_pnl_color = GREEN if open_pnl > 0 else (RED if open_pnl < 0 else TEXT)
        self.open_positions_card.set_value(str(open_count))
        self.open_pnl_card.set_value(f"{open_pnl:+.2f}", open_pnl_color)
        self.win_rate_hero_card.set_value(f"{win_rate:.1f}%")

        # Next 3-min candle close is pure wall-clock math (candles align to
        # UTC :00/:03/:06.../:57 past the hour, same as Binance's klines) -
        # doesn't need any backend data, ticks correctly even if polling has
        # actually stalled (that's the point: compare it against the second
        # number, which DOES come from the backend).
        secs_into_candle = time.time() % 180
        secs_to_close = 180 - secs_into_candle
        mm, ss = divmod(int(secs_to_close), 60)
        candle_txt = f"{mm}:{ss:02d}"

        last_cycle = data.get("last_poll_cycle_ts")
        if last_cycle:
            age = max(0, time.time() - last_cycle)
            sync_color = GREEN if age < 15 else (YELLOW if age < 60 else RED)
            sync_txt = f"Next candle {candle_txt}  |  Data captured {int(age)}s ago"
        else:
            sync_color = SLATE
            sync_txt = f"Next candle {candle_txt}  |  Data: not synced yet"
        self.sync_check_card.set_value(sync_txt, sync_color)

        ts = data.get("telegram_last_attempt_ts")
        if not ts:
            self.telegram_card.set_value("Not sent yet", SLATE)
        elif data.get("telegram_last_ok"):
            age = max(0, time.time() - ts)
            self.telegram_card.set_value(f"OK ({int(age)}s ago)", GREEN)
        else:
            err = str(data.get("telegram_last_error") or "unknown error")
            short = err if len(err) <= 40 else err[:40] + "..."
            self.telegram_card.set_value(f"FAILED - {short}", RED)

        target = _get_daily_target()
        if target > 0:
            hit = pnl >= target
            self.target_card.height = dp(66)
            self.target_card.opacity = 1
            state_txt = "TARGET REACHED" if hit else "In progress"
            self.target_value.text = f"{pnl:+.2f} / {target:.2f} USDT - {state_txt}"
            self.target_value.color = GREEN if hit else TEXT
        else:
            self.target_card.height = 0
            self.target_card.opacity = 0

        from recovered_bot import settings_store
        loss_limit = float(settings_store.get("daily_loss_limit_usdt", 0) or 0)
        paused = (target > 0 and pnl >= target) or (loss_limit > 0 and pnl <= -loss_limit)
        if paused:
            reason = "daily target reached" if (target > 0 and pnl >= target) else "daily loss limit hit"
            self.paused_banner.height = dp(40)
            self.paused_banner.opacity = 1
            self.paused_label.text = f"AUTO-TRADING PAUSED - {reason}"
        else:
            self.paused_banner.height = 0
            self.paused_banner.opacity = 0

        signals = data.get("last_signals", [])
        self.signals_label.text = "\n".join(signals) if signals else "No signals yet."

        err = data.get("last_error")
        if err and str(err) != self.error_label.text:
            self.error_label.text = str(err)
            # Auto-scroll to the bottom so the actual exception message (the
            # most useful line for debugging) is visible immediately, instead
            # of requiring the user to manually scroll past the whole
            # traceback every time a new error appears.
            Clock.schedule_once(lambda dt: setattr(self.error_scroll, 'scroll_y', 0), 0.1)

    def _status_color(self, status):
        s = status.lower()
        if "crash" in s or "fail" in s:
            return RED
        if "running" in s:
            return GREEN
        return YELLOW


class PositionCard(CardLayout):
    def __init__(self, pos, **kwargs):
        super().__init__(orientation='vertical', padding=(dp(14), dp(12)), spacing=dp(6),
                          size_hint_y=None, height=dp(200), **kwargs)

        self.trade_id = pos.get("id")
        self.opened_at_ts = pos.get("opened_at_ts", time.time())
        # +8dp over the old fixed height: the "Target: TPn (full close)"
        # line (added when a manual TP target is set) makes levels_row's
        # text taller than before, and Kivy doesn't clip Label overflow -
        # a too-short fixed-height row lets a tall label's texture spill
        # into the row below it (same class of bug fixed on the Market
        # screen's status badge earlier).
        self.height += dp(8)

        top = BoxLayout(size_hint_y=None, height=dp(30))
        self.title_label = Label(text=f"#{pos.get('signal_no','-')}  {pos.get('symbol','')}  [{pos.get('action','')}]",
                      font_size=dp(14), bold=True, color=TEXT, halign='left', valign='middle',
                      size_hint_x=0.7, shorten=True, shorten_from='right')
        # BUG FIXED: title_label and roi_label previously had no size_hint_x
        # set, so they split the row 50/50 by default. At font 16sp that left
        # too little width for longer symbols ("1000PEPEUSDT", "MSTRUSDT"), so
        # the text wrapped to a 2nd line inside a fixed single-line-height
        # box - which clipped the tops/bottoms of characters (this is exactly
        # what made "1000PEPEUSDT" render looking like "1UUUPEPEUSDT" in your
        # screenshot). Now: more width for the name (70%), smaller font so
        # normal-length symbols fit on one line, and shorten=True as a safety
        # net so an unusually long symbol truncates cleanly with "..." instead
        # of ever wrapping/clipping again.
        self.title_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        roi = pos.get("roi_pct", 0.0)
        self.roi_label = Label(text=f"{roi:+.2f}%", font_size=dp(15), bold=True,
                           color=(GREEN if roi >= 0 else RED), halign='right', valign='middle',
                           size_hint_x=0.3)
        self.roi_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        top.add_widget(self.title_label)
        top.add_widget(self.roi_label)

        mid = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(6))
        entry = pos.get("entry_price", 0.0)
        ltp = pos.get("ltp", 0.0)
        pnl = pos.get("pnl_usdt", 0.0)
        invest = pos.get("invest_usdt", 0.0)

        self.left_info = Label(text=f"[color={ACCENT_HEX}]Entry {entry:g}[/color]   LTP {ltp:g}",
                           font_size=dp(12), color=SLATE, markup=True,
                           halign='left', valign='middle', size_hint_x=0.5)
        self.left_info.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.pnl_info = Label(text=f"P&L {pnl:+.2f} USDT", font_size=dp(12), bold=True,
                          color=(GREEN if pnl >= 0 else RED), halign='left', valign='middle',
                          size_hint_x=0.3)
        self.pnl_info.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.invest_info = Label(text=f"Invest {invest:g}", font_size=dp(12), color=SLATE,
                             halign='right', valign='middle', size_hint_x=0.2)
        self.invest_info.bind(size=lambda i, s: setattr(i, 'text_size', s))

        mid.add_widget(self.left_info)
        mid.add_widget(self.pnl_info)
        mid.add_widget(self.invest_info)

        self._tp_value = float(pos.get("tp_value", 0) or 0)
        self._sl_value = float(pos.get("sl_value", 0) or 0)
        self._entry = entry
        self._action = pos.get("action", "")
        self._signal_sl = pos.get("signal_stop_loss")
        self._signal_tp1 = pos.get("signal_tp1")
        self._signal_tp2 = pos.get("signal_tp2")
        self._signal_tp3 = pos.get("signal_tp3")
        self._signal_tp4 = pos.get("signal_tp4")
        self._target_level = pos.get("user_target_tp") or 4
        self._current_sl = pos.get("trailed_sl") or self._signal_sl
        self._user_target_tp = pos.get("user_target_tp")
        self._tp_hit_levels = pos.get("tp_hit_levels", [])
        # Fixed at card-creation time: positions opened with full signal
        # levels (the normal case) get one self-contained box per Entry/SL/
        # TPn level (below); legacy percent-only positions (opened before
        # signal-level tracking existed) keep the old simple single-target
        # % bar since they have no SL/TP prices to build boxes for.
        self._uses_rr_bar = self._signal_sl is not None and self._signal_tp1 is not None
        self.level_boxes = {}   # "tp1".."tp4"/"entry"/"sl" -> LevelBox, only when _uses_rr_bar
        self.tp_buttons = {}    # kept only for _style_tp_buttons()/_on_tp_target_tap() compatibility

        if self._uses_rr_bar:
            # One row per level that actually exists, farthest TP at the top
            # down to SL at the bottom - each row is its own LevelBox, so its
            # name/price/%/USDT text AND its live progress fill all live
            # inside that one row and can never mix with the row above/below.
            # When the strategy's SD bands run out (entry already far beyond
            # 1SD/2SD), calculate_tp_levels() reuses the same outer price for
            # the remaining TP slots (e.g. TP2=TP3=TP4) - those aren't extra
            # real targets, so they collapse into ONE box instead of showing
            # 2-3 identical-looking rows.
            levels_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(4))
            tp_levels = [(4, self._signal_tp4), (3, self._signal_tp3),
                         (2, self._signal_tp2), (1, self._signal_tp1)]
            last_price, last_box, last_levels = None, None, []
            for level, price in tp_levels:
                if price is None:
                    continue
                if last_box is not None and last_price is not None and abs(price - last_price) < 1e-12:
                    # Same price as the level just above - merge into that
                    # box instead of adding a duplicate-looking row.
                    last_levels.append(level)
                    self.level_boxes[f"tp{level}"] = last_box
                    self.tp_buttons[level] = last_box
                    last_box.name_label.text = f"TP{min(last_levels)}-{max(last_levels)}"
                    continue
                box = LevelBox(on_tap=lambda lv=level: self._on_tp_target_tap(lv))
                self.level_boxes[f"tp{level}"] = box
                self.tp_buttons[level] = box  # so _style_tp_buttons/_on_tp_target_tap keep working
                levels_box.add_widget(box)
                last_price, last_box, last_levels = price, box, [level]
            entry_box = LevelBox()
            self.level_boxes["entry"] = entry_box
            levels_box.add_widget(entry_box)
            sl_box = LevelBox()
            self.level_boxes["sl"] = sl_box
            levels_box.add_widget(sl_box)
            row_count = len(levels_box.children)
            levels_box.height = row_count * dp(34) + max(0, row_count - 1) * dp(4)
            self.levels_box_stack = levels_box
            # self.height currently assumes the legacy layout's levels_row
            # (52dp) + prog_row (16dp) + the spacing gap between them (one
            # extra row here means one fewer gap overall) - swap that
            # reserved space out for however tall the actual box stack is.
            self.height += levels_box.height - dp(68) - dp(6)
            self._style_tp_buttons()
        else:
            # --- unchanged legacy path (no signal SL/TP prices available) ---
            levels_row = BoxLayout(size_hint_y=None, height=dp(52))
            self.levels_label = Label(text=self._levels_text(pos), font_size=dp(11), color=SLATE,
                                       halign='left', valign='top', markup=True)
            self.levels_label.bind(width=lambda i, w: setattr(i, 'text_size', (w, None)))
            levels_row.add_widget(self.levels_label)
            self.levels_box_stack = levels_row

            prog_row = BoxLayout(size_hint_y=None, height=dp(16), spacing=dp(8))
            pct, bar_color, prog_text = self._calc_progress(roi, ltp)
            self.progress = MiniProgressBar(pct=pct, color=bar_color, size_hint_x=0.75)
            self.prog_label = Label(text=prog_text, font_size=dp(11), color=SLATE, size_hint_x=0.25,
                                halign='right', valign='middle')
            self.prog_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
            prog_row.add_widget(self.progress)
            prog_row.add_widget(self.prog_label)
            self.prog_row = prog_row

        action_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        elapsed = max(0.0, time.time() - self.opened_at_ts)
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        self.duration_label = Label(text=f"Open {mins}m {secs}s",
                                     font_size=dp(12), color=SLATE,
                                     halign='left', valign='middle', size_hint_x=0.6)
        self.duration_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.close_btn = Button(text="Close", font_size=dp(12), bold=True, size_hint_x=0.4,
                                 background_normal='', background_color=RED, color=(1, 1, 1, 1))
        self.close_btn.bind(on_release=self.confirm_close)
        action_row.add_widget(self.duration_label)
        action_row.add_widget(self.close_btn)

        self.add_widget(top)
        self.add_widget(mid)
        self.add_widget(self.levels_box_stack)
        if not self._uses_rr_bar:
            self.add_widget(self.prog_row)
        self.add_widget(action_row)

        if self._uses_rr_bar:
            self._update_level_boxes(entry, ltp, invest, pos.get("leverage", 1))

    def _update_level_boxes(self, entry, ltp, invest, leverage):
        """Recompute every level box's price/%/USDT/fill from the current
        live price - called at card creation AND on every update_data() so
        each box's contained progress fill tracks price live, same as the
        old shared bar used to, just per-box now instead of one shared bar."""
        if not entry:
            return
        self._ltp = ltp
        leverage = float(leverage or 1)
        is_long = self._action == "BUY"
        in_profit = (ltp >= entry) if is_long else (ltp <= entry)
        target_level = max(1, min(self._target_level, 4))

        for level in (4, 3, 2, 1):
            box = self.level_boxes.get(f"tp{level}")
            if box is None:
                continue
            price = {1: self._signal_tp1, 2: self._signal_tp2,
                     3: self._signal_tp3, 4: self._signal_tp4}[level]
            raw_pct = abs(price - entry) / entry * 100 if entry else 0.0
            usdt_at_level = invest * (raw_pct * leverage / 100.0)
            dist_to_level = abs(price - entry)
            fill_pct = 0.0
            if in_profit and dist_to_level > 0:
                fill_pct = max(0.0, min(100.0, abs(ltp - entry) / dist_to_level * 100.0))
            is_target = (level == target_level)
            level_hit = level in (self._tp_hit_levels or [])
            box.set_level(
                name=f"TP{level}", price=price,
                pct_text=f"+{raw_pct:.2f}%", usdt_text=f"+{usdt_at_level:.2f}",
                fill_pct=(100.0 if level_hit else fill_pct), fill_color=GREEN,
                name_color=ACCENT if is_target else TEXT,
                highlighted=is_target, hit=level_hit,
            )

        entry_box = self.level_boxes.get("entry")
        if entry_box is not None:
            entry_box.set_level(
                name="ENTRY", price=entry, pct_text="0.00%", usdt_text="--",
                fill_pct=0.0, fill_color=TRACK_BG, name_color=TEXT,
            )

        sl_box = self.level_boxes.get("sl")
        if sl_box is not None and self._current_sl is not None:
            sl = self._current_sl
            is_trailed = self._signal_sl is not None and abs(sl - self._signal_sl) > 1e-12
            # A trailed SL can sit on the PROFIT side of entry (that's the
            # whole point - locking in gains) - so the sign has to be
            # direction-aware now, not hardcoded negative like a fixed
            # original SL always was.
            sl_in_profit = (sl >= entry) if is_long else (sl <= entry)
            raw_pct = abs(entry - sl) / entry * 100 if entry else 0.0
            usdt_at_sl = invest * (raw_pct * leverage / 100.0)
            sign = "+" if sl_in_profit else "-"
            dist_to_sl = abs(entry - sl)
            fill_pct = 0.0
            if (not in_profit) and dist_to_sl > 0:
                fill_pct = max(0.0, min(100.0, abs(entry - ltp) / dist_to_sl * 100.0))
            sl_box.set_level(
                name="SL (trail)" if is_trailed else "SL", price=sl,
                pct_text=f"{sign}{raw_pct:.2f}%", usdt_text=f"{sign}{usdt_at_sl:.2f}",
                fill_pct=fill_pct, fill_color=(GREEN if sl_in_profit else RED),
                name_color=(GREEN if sl_in_profit else RED),
            )

    def _style_tp_buttons(self):
        # Highlights the box for whichever TP level is the actual full-close
        # target - _update_level_boxes() also re-applies this highlight
        # every refresh, this just handles the moment right after a tap.
        selected = getattr(self, "_user_target_tp", None)
        for level, box in self.tp_buttons.items():
            box.name_label.color = ACCENT if level == selected else TEXT
            box._border.width = 1.6 if level == selected else 1

    def _on_tp_target_tap(self, level):
        from recovered_bot import paper_trader
        price = {1: self._signal_tp1, 2: self._signal_tp2,
                 3: self._signal_tp3, 4: self._signal_tp4}.get(level)
        ltp = getattr(self, "_ltp", None)
        is_long = self._action == "BUY"
        already_passed = (
            price is not None and ltp is not None and
            ((ltp >= price) if is_long else (ltp <= price))
        )
        if already_passed:
            # Price has already moved beyond this level - tapping it here
            # means "lock my stop loss in at this level", NOT "close the
            # trade right here" (that used to happen because the monitor
            # loop saw the tapped target as already-satisfied and closed on
            # the very next tick - "TP1 hit ho gayi thi, tap kiya to trade
            # close ho gaya" from your message). The full-close target
            # (TP4 by default, or whatever was already picked) is untouched
            # - the trade keeps running toward it with a tighter SL now.
            paper_trader.trail_sl_to_level(self.trade_id, level)
            return
        # Not yet reached - unchanged behaviour: pin the FULL close here.
        # Tapping the already-selected level clears it back to auto mode.
        new_target = None if getattr(self, "_user_target_tp", None) == level else level
        paper_trader.set_target_tp(self.trade_id, new_target)
        self._user_target_tp = new_target
        self._style_tp_buttons()

    @staticmethod
    def _levels_text(pos):
        sl = pos.get("signal_stop_loss")
        tp1 = pos.get("signal_tp1")
        tp2 = pos.get("signal_tp2")
        tp3 = pos.get("signal_tp3")
        tp4 = pos.get("signal_tp4")
        if sl is None or tp1 is None:
            return "SL/TP: percent-based (opened before signal-level tracking)"
        target = pos.get("user_target_tp") or 4
        tps = [("TP1", tp1), ("TP2", tp2), ("TP3", tp3), ("TP4", tp4)]
        # SL is fixed for the life of the trade (strategy's own level, no
        # trailing). The target TP (user-picked, or TP4 by default) is
        # highlighted - that's the only level that actually closes the
        # trade; the others are just reference lines, never "achieved".
        parts = [f"[color={RED_HEX}]SL {sl:g}[/color]"]
        for i, (label, val) in enumerate(tps):
            if val is None:
                continue
            if (i + 1) == target:
                parts.append(f"[color={ACCENT_HEX}]{label} {val:g} (close here)[/color]")
            else:
                parts.append(f"[color={SLATE_HEX}]{label} {val:g}[/color]")
        line = "  ".join(parts)
        auto_or_manual = "Target: TP4 (default)" if not pos.get("user_target_tp") else f"Target: TP{target} (tapped)"
        line = f"[color={ACCENT_HEX}]{auto_or_manual}[/color]  |  " + line
        return line

    def _calc_progress(self, roi, ltp=None):
        if self._signal_sl is not None and self._signal_tp1 is not None and ltp is not None and self._entry:
            # Progress toward the ACTUAL full-close target (user-picked, or
            # TP4 by default) and the fixed strategy SL - there's no more
            # partial-booking stage to track, so this is a straight two-way
            # gauge: distance covered toward the target vs toward the SL.
            tp_targets = [self._signal_tp1, self._signal_tp2, self._signal_tp3, self._signal_tp4]
            level = max(1, min(self._target_level, 4))
            next_tp = tp_targets[level - 1]
            current_sl = self._current_sl if self._current_sl is not None else self._signal_sl
            tp_label = f"TP{level}"
            is_long = self._action == "BUY"
            if next_tp is None:
                pass
            elif (is_long and ltp >= self._entry) or (not is_long and ltp <= self._entry):
                # moving toward the next TP
                total = abs(next_tp - self._entry)
                moved = abs(ltp - self._entry)
                pct = max(0.0, min(100.0, (moved / total) * 100)) if total > 0 else 0.0
                return pct, GREEN, f"{pct:.0f}% -> {tp_label}"
            else:
                # moving toward the current (possibly trailed) SL
                total = abs(self._entry - current_sl)
                moved = abs(self._entry - ltp)
                pct = max(0.0, min(100.0, (moved / total) * 100)) if total > 0 else 0.0
                return pct, RED, f"{pct:.0f}% -> SL"
        if roi >= 0 and self._tp_value > 0:
            pct = max(0.0, min(100.0, (roi / self._tp_value) * 100))
            return pct, GREEN, f"{pct:.0f}% -> TP"
        elif roi < 0 and self._sl_value > 0:
            pct = max(0.0, min(100.0, (-roi / self._sl_value) * 100))
            return pct, RED, f"{pct:.0f}% -> SL"
        return 0.0, SLATE, "--"

    def update_data(self, pos):
        self.trade_id = pos.get("id")
        roi = pos.get("roi_pct", 0.0)
        entry = pos.get("entry_price", 0.0)
        ltp = pos.get("ltp", 0.0)
        pnl = pos.get("pnl_usdt", 0.0)
        invest = pos.get("invest_usdt", 0.0)
        self._tp_value = float(pos.get("tp_value", 0) or 0)
        self._sl_value = float(pos.get("sl_value", 0) or 0)
        self._entry = entry
        self._action = pos.get("action", "")
        self._signal_sl = pos.get("signal_stop_loss")
        self._signal_tp1 = pos.get("signal_tp1")
        self._signal_tp2 = pos.get("signal_tp2")
        self._signal_tp3 = pos.get("signal_tp3")
        self._signal_tp4 = pos.get("signal_tp4")
        self._target_level = pos.get("user_target_tp") or 4
        self._current_sl = pos.get("trailed_sl") or self._signal_sl
        self._tp_hit_levels = pos.get("tp_hit_levels", [])

        self.title_label.text = f"#{pos.get('signal_no','-')}  {pos.get('symbol','')}  [{pos.get('action','')}]"
        self.roi_label.text = f"{roi:+.2f}%"
        self.roi_label.color = GREEN if roi >= 0 else RED
        self.left_info.text = f"[color={ACCENT_HEX}]Entry {entry:g}[/color]   LTP {ltp:g}"
        self.pnl_info.text = f"P&L {pnl:+.2f} USDT"
        self.pnl_info.color = GREEN if pnl >= 0 else RED
        self.invest_info.text = f"Invest {invest:g}"

        new_target = pos.get("user_target_tp")
        target_changed = self.tp_buttons and new_target != getattr(self, "_user_target_tp", None)
        if target_changed:
            self._user_target_tp = new_target

        if self._uses_rr_bar:
            # Every box's price/%/USDT/fill is recomputed from the current
            # live price here - each box's fill lives entirely inside that
            # one box's own canvas (see LevelBox), so it can never mix with
            # the row above/below it the way the old single shared bar could
            # visually appear to.
            self._update_level_boxes(entry, ltp, invest, pos.get("leverage", 1))
        else:
            # --- unchanged legacy path (no signal SL/TP prices available) ---
            # This was previously never refreshed after the card was first
            # created, so the SL/TP1-4 line stayed frozen at its opening
            # values forever - the trailing SL and "Booked" progress never
            # showed up.
            self.levels_label.text = self._levels_text(pos)
            pct, bar_color, prog_text = self._calc_progress(roi, ltp)
            self.progress.set_pct(pct, bar_color)
            self.prog_label.text = prog_text

        elapsed = max(0.0, time.time() - self.opened_at_ts)
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        self.duration_label.text = f"Open {mins}m {secs}s"

    def confirm_close(self, *args):
        box = BoxLayout(orientation='vertical', padding=16, spacing=14)
        box.add_widget(Label(text=f"Close {self.title_label.text} now at current market price?",
                              font_size=dp(14), color=TEXT))
        btn_box = BoxLayout(spacing=10, size_hint_y=None, height=44)
        btn_yes = Button(text="Yes, Close It", bold=True, background_normal='',
                          background_color=RED, color=(1, 1, 1, 1))
        btn_no = Button(text="Keep Open", bold=True, background_normal='',
                         background_color=(0.16, 0.16, 0.20, 1), color=(1, 1, 1, 1))
        btn_box.add_widget(btn_yes)
        btn_box.add_widget(btn_no)
        box.add_widget(btn_box)
        popup = Popup(title="Confirm Close Position", content=box, size_hint=(0.85, 0.35),
                      auto_dismiss=False, title_color=TEXT, separator_color=RED)

        def on_yes(instance):
            from recovered_bot import paper_trader
            paper_trader.close_position_manual(self.trade_id)
            popup.dismiss()

        btn_yes.bind(on_release=on_yes)
        btn_no.bind(on_release=popup.dismiss)
        popup.open()


class HistoryCard(CardLayout):
    def __init__(self, trade, **kwargs):
        super().__init__(orientation='vertical', padding=(dp(14), dp(12)), spacing=dp(6),
                          size_hint_y=None, height=dp(118), **kwargs)

        top = BoxLayout(size_hint_y=None, height=dp(26))
        status_text = trade.get("status", "CLOSED").replace("CLOSED_", "")
        # NOTE: the 25%-per-TP partial-booking system was removed (each trade
        # now fully closes at whichever single TP level is the active target,
        # see paper_trader.py's monitor loop) - close reasons are now plain
        # "TP1".."TP4" / "TP1_TARGET" (user-picked target) / "SL" /
        # "REVERSED" / "MANUAL", never a "..._PARTIAL" suffix. The old
        # "(25%)" suffix here never matched anything anymore and was dead
        # code left over from that system - removed.
        display_status = status_text
        badge_hex = GREEN_HEX if (status_text.startswith("TP") or status_text.startswith("SCALP")) else (RED_HEX if status_text.startswith("SL") else SLATE_HEX)
        # Very old trades saved before symbol tracking existed in trade_store
        # can have an empty symbol - show that plainly instead of leaving a
        # blank gap that looks like a rendering bug. New trades always carry
        # symbol through cleanly (set at open_paper_trade time).
        symbol_display = trade.get('symbol') or "(old trade - no symbol saved)"
        title = Label(text=f"#{trade.get('signal_no','-')}  {symbol_display}  [{trade.get('action','')}]  "
                            f"[color={badge_hex}][b]{display_status}[/b][/color]",
                      font_size=dp(15), bold=True, color=TEXT, halign='left', valign='middle',
                      markup=True)
        title.bind(size=lambda i, s: setattr(i, 'text_size', s))
        pnl = trade.get("pnl_usdt") or 0.0
        pnl_label = Label(text=f"{pnl:+.2f} USDT", font_size=dp(14), bold=True,
                           color=(GREEN if pnl >= 0 else RED), halign='right', valign='middle')
        pnl_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        top.add_widget(title)
        top.add_widget(pnl_label)

        mid = BoxLayout(size_hint_y=None, height=dp(20))
        # .get(key, 0.0) only falls back when the key is MISSING - a trade
        # record saved with the key present but the value explicitly None
        # (e.g. a very old/legacy trade, or one saved mid-crash before entry
        # price was known) still comes back as None here, and f"{None:g}"
        # below raises. That unhandled exception used to abort this card's
        # __init__ partway through, which (since the caller loop in
        # _render_history had no per-item try/except) stopped the WHOLE
        # History tab render right there - screen looked frozen/stuck on
        # whatever was already drawn. Coercing explicit Nones to 0.0 here
        # closes that hole at the source.
        roi = trade.get("roi_pct") or 0.0
        entry = trade.get("entry_price") or 0.0
        close_p = trade.get("ltp") or 0.0
        info = Label(text=f"ROI {roi:+.2f}%   Entry {entry:g}   Close {close_p:g}",
                     font_size=dp(12), color=SLATE, halign='left', valign='middle')
        info.bind(size=lambda i, s: setattr(i, 'text_size', s))
        mid.add_widget(info)

        bottom = BoxLayout(size_hint_y=None, height=dp(20))
        opened = trade.get("opened_at", "--")
        closed = trade.get("closed_at", "--")
        dur = self._duration(trade)
        times = Label(text=f"Opened {opened}   Closed {closed}   ({dur})",
                      font_size=dp(11), color=SLATE, halign='left', valign='middle')
        times.bind(size=lambda i, s: setattr(i, 'text_size', s))
        bottom.add_widget(times)

        self.add_widget(top)
        self.add_widget(mid)
        self.add_widget(bottom)

    def _duration(self, trade):
        ots = trade.get("opened_at_ts")
        cts = trade.get("closed_at_ts")
        if not ots or not cts:
            return "--"
        secs = max(0, int(cts - ots))
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m"
        hrs = mins // 60
        return f"{hrs}h {mins % 60}m"


class PositionsScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mode = "open"
        self.sort_mode = "newest"
        self._last_open = []
        self._last_history = []

        self.root_box = BoxLayout(orientation='vertical', padding=(dp(10), calc_top_pad(), dp(10), dp(10)),
                                   spacing=dp(10))
        self.add_widget(self.root_box)

        self.health_strip = HealthStrip()
        self.root_box.add_widget(self.health_strip)

        header_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.open_btn = Button(text="Open", font_size=dp(15), bold=True,
                                background_normal='', background_color=ACCENT, color=(1, 1, 1, 1))
        self.history_btn = Button(text="History", font_size=dp(15), bold=True,
                                   background_normal='', background_color=BORDER, color=TEXT)
        self.open_btn.bind(on_release=lambda i: self._set_mode("open"))
        self.history_btn.bind(on_release=lambda i: self._set_mode("history"))
        header_row.add_widget(self.open_btn)
        header_row.add_widget(self.history_btn)
        self.root_box.add_widget(header_row)

        self.sort_row = BoxLayout(size_hint_y=None, height=0, opacity=0, spacing=dp(4))
        self.btn_sort_newest = Button(text="Newest", font_size=dp(11), bold=True, background_normal='', background_color=ACCENT, color=(1,1,1,1))
        self.btn_sort_oldest = Button(text="Oldest", font_size=dp(11), bold=True, background_normal='', background_color=BORDER, color=TEXT)
        self.btn_sort_high = Button(text="P&L High-Low", font_size=dp(11), bold=True, background_normal='', background_color=BORDER, color=TEXT)
        self.btn_sort_low = Button(text="P&L Low-High", font_size=dp(11), bold=True, background_normal='', background_color=BORDER, color=TEXT)

        self.btn_sort_newest.bind(on_release=lambda i: self._set_sort_mode("newest"))
        self.btn_sort_oldest.bind(on_release=lambda i: self._set_sort_mode("oldest"))
        self.btn_sort_high.bind(on_release=lambda i: self._set_sort_mode("high"))
        self.btn_sort_low.bind(on_release=lambda i: self._set_sort_mode("low"))

        self.sort_row.add_widget(self.btn_sort_newest)
        self.sort_row.add_widget(self.btn_sort_oldest)
        self.sort_row.add_widget(self.btn_sort_high)
        self.sort_row.add_widget(self.btn_sort_low)
        self.root_box.add_widget(self.sort_row)

        # Trade journal filters (History tab only) - symbol search + win/loss
        # + date range. Purely client-side filtering of trade_store's already-
        # fetched closed_trades list, no backend change needed. Collapsed
        # (height 0) on the Open tab, same pattern as sort_row above.
        self.symbol_filter = ""
        self.result_filter = "all"     # all / win / loss
        self.date_filter = "all"       # all / today / 7d / 30d

        self.journal_row = BoxLayout(orientation='vertical', size_hint_y=None, height=0, opacity=0,
                                      spacing=dp(4))
        search_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        self.symbol_search = TextInput(hint_text="Filter by symbol...", multiline=False,
                                        font_size=dp(13), background_color=TRACK_BG,
                                        foreground_color=TEXT, cursor_color=ACCENT,
                                        padding=(dp(10), dp(8)))
        self.symbol_search.bind(text=self._on_symbol_filter_change)
        search_row.add_widget(self.symbol_search)
        self.journal_row.add_widget(search_row)

        result_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(4))
        self.btn_result_all = Button(text="All", font_size=dp(11), bold=True, background_normal='',
                                      background_color=ACCENT, color=(1, 1, 1, 1))
        self.btn_result_win = Button(text="Wins", font_size=dp(11), bold=True, background_normal='',
                                      background_color=BORDER, color=TEXT)
        self.btn_result_loss = Button(text="Losses", font_size=dp(11), bold=True, background_normal='',
                                       background_color=BORDER, color=TEXT)
        self.btn_result_all.bind(on_release=lambda i: self._set_result_filter("all"))
        self.btn_result_win.bind(on_release=lambda i: self._set_result_filter("win"))
        self.btn_result_loss.bind(on_release=lambda i: self._set_result_filter("loss"))
        result_row.add_widget(self.btn_result_all)
        result_row.add_widget(self.btn_result_win)
        result_row.add_widget(self.btn_result_loss)
        self.journal_row.add_widget(result_row)

        date_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(4))
        self.btn_date_all = Button(text="All Time", font_size=dp(11), bold=True, background_normal='',
                                    background_color=ACCENT, color=(1, 1, 1, 1))
        self.btn_date_today = Button(text="Today", font_size=dp(11), bold=True, background_normal='',
                                      background_color=BORDER, color=TEXT)
        self.btn_date_7d = Button(text="7D", font_size=dp(11), bold=True, background_normal='',
                                   background_color=BORDER, color=TEXT)
        self.btn_date_30d = Button(text="30D", font_size=dp(11), bold=True, background_normal='',
                                    background_color=BORDER, color=TEXT)
        self.btn_date_all.bind(on_release=lambda i: self._set_date_filter("all"))
        self.btn_date_today.bind(on_release=lambda i: self._set_date_filter("today"))
        self.btn_date_7d.bind(on_release=lambda i: self._set_date_filter("7d"))
        self.btn_date_30d.bind(on_release=lambda i: self._set_date_filter("30d"))
        date_row.add_widget(self.btn_date_all)
        date_row.add_widget(self.btn_date_today)
        date_row.add_widget(self.btn_date_7d)
        date_row.add_widget(self.btn_date_30d)
        self.journal_row.add_widget(date_row)

        self.root_box.add_widget(self.journal_row)

        self.scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation='vertical', spacing=dp(10), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        self.scroll.add_widget(self.list_box)
        self.root_box.add_widget(self.scroll)

        self.empty_label_open = Label(text="No open positions yet.", font_size=dp(15),
                                       color=SLATE, size_hint_y=None, height=dp(44))
        self.empty_label_history = Label(text="No closed trades yet.", font_size=dp(15),
                                          color=SLATE, size_hint_y=None, height=dp(44))
        self.list_box.add_widget(self.empty_label_open)

        self.close_all_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.btn_close_all_profit = Button(text="Close All Profit", font_size=dp(12), bold=True,
                                            background_normal='', background_color=GREEN,
                                            color=(1, 1, 1, 1))
        self.btn_close_all_loss = Button(text="Close All Loss", font_size=dp(12), bold=True,
                                          background_normal='', background_color=RED,
                                          color=(1, 1, 1, 1))
        self.btn_close_all_profit.bind(on_release=lambda i: self._confirm_close_all("profit"))
        self.btn_close_all_loss.bind(on_release=lambda i: self._confirm_close_all("loss"))
        self.close_all_row.add_widget(self.btn_close_all_profit)
        self.close_all_row.add_widget(self.btn_close_all_loss)
        self.root_box.add_widget(self.close_all_row)

        total_row = BoxLayout(size_hint_y=None, height=dp(40), padding=(dp(4), dp(4)))
        with total_row.canvas.before:
            Color(*BORDER)
            self._total_top_line = Line(points=[0, 0, 0, 0], width=1)
        total_row.bind(pos=self._update_total_line, size=self._update_total_line)
        self.total_label = Label(text="Total P&L: 0.00 USDT", font_size=dp(16), bold=True, color=TEXT,
                                  halign='right', valign='middle')
        self.total_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        total_row.add_widget(self.total_label)
        self.root_box.add_widget(total_row)

    def _update_total_line(self, widget, *args):
        self._total_top_line.points = [widget.x, widget.top, widget.right, widget.top]

    def _confirm_close_all(self, which):
        label_txt = "profitable" if which == "profit" else "losing"
        if which == "profit":
            matched = [p for p in self._last_open if float(p.get("pnl_usdt", 0.0)) >= 0]
        else:
            matched = [p for p in self._last_open if float(p.get("pnl_usdt", 0.0)) < 0]
        count = len(matched)
        total = sum(float(p.get("pnl_usdt", 0.0)) for p in matched)

        box = BoxLayout(orientation='vertical', padding=16, spacing=14)
        if count == 0:
            box.add_widget(Label(text=f"No {label_txt} open positions right now.",
                                  font_size=dp(14), color=TEXT))
        else:
            box.add_widget(Label(text=f"Close {count} {label_txt} position{'s' if count != 1 else ''}?",
                                  font_size=dp(15), bold=True, color=TEXT))
            box.add_widget(Label(text=f"Total P&L: {total:+.2f} USDT",
                                  font_size=dp(16), bold=True,
                                  color=(GREEN if total >= 0 else RED)))
        btn_box = BoxLayout(spacing=10, size_hint_y=None, height=44)
        btn_yes = Button(text="Yes, Close All", bold=True, background_normal='',
                          background_color=(GREEN if which == "profit" else RED), color=(1, 1, 1, 1),
                          disabled=(count == 0))
        btn_no = Button(text="Cancel", bold=True, background_normal='',
                         background_color=(0.16, 0.16, 0.20, 1), color=(1, 1, 1, 1))
        btn_box.add_widget(btn_yes)
        btn_box.add_widget(btn_no)
        box.add_widget(btn_box)
        popup = Popup(title="Confirm Close All", content=box, size_hint=(0.85, 0.4),
                      auto_dismiss=False, title_color=TEXT)

        def on_yes(instance):
            from recovered_bot import paper_trader
            if which == "profit":
                paper_trader.close_all_profit()
            else:
                paper_trader.close_all_loss()
            popup.dismiss()

        btn_yes.bind(on_release=on_yes)
        btn_no.bind(on_release=popup.dismiss)
        popup.open()

    def _set_mode(self, mode):
        self.mode = mode
        self.open_btn.background_color = ACCENT if mode == "open" else BORDER
        self.history_btn.background_color = ACCENT if mode == "history" else BORDER
        self.open_btn.color = (1, 1, 1, 1) if mode == "open" else TEXT
        self.history_btn.color = (1, 1, 1, 1) if mode == "history" else TEXT

        if mode == "history":
            self.sort_row.height = dp(32)
            self.sort_row.opacity = 1
            self.journal_row.height = dp(106)
            self.journal_row.opacity = 1
        else:
            self.sort_row.height = 0
            self.sort_row.opacity = 0
            self.journal_row.height = 0
            self.journal_row.opacity = 0

        self._render()

    def _on_symbol_filter_change(self, instance, value):
        self.symbol_filter = value.strip().upper()
        self._history_render_key = None  # force a re-render even if the trade set itself hasn't changed
        self._render()

    def _set_result_filter(self, which):
        self.result_filter = which
        for name, btn in (("all", self.btn_result_all), ("win", self.btn_result_win), ("loss", self.btn_result_loss)):
            btn.background_color = ACCENT if name == which else BORDER
            btn.color = (1, 1, 1, 1) if name == which else TEXT
        self._history_render_key = None
        self._render()

    def _set_date_filter(self, which):
        self.date_filter = which
        for name, btn in (("all", self.btn_date_all), ("today", self.btn_date_today),
                           ("7d", self.btn_date_7d), ("30d", self.btn_date_30d)):
            btn.background_color = ACCENT if name == which else BORDER
            btn.color = (1, 1, 1, 1) if name == which else TEXT
        self._history_render_key = None
        self._render()

    def _set_sort_mode(self, sort_mode):
        self.sort_mode = sort_mode
        self.btn_sort_newest.background_color = ACCENT if sort_mode == "newest" else BORDER
        self.btn_sort_oldest.background_color = ACCENT if sort_mode == "oldest" else BORDER
        self.btn_sort_high.background_color = ACCENT if sort_mode == "high" else BORDER
        self.btn_sort_low.background_color = ACCENT if sort_mode == "low" else BORDER

        self.btn_sort_newest.color = (1, 1, 1, 1) if sort_mode == "newest" else TEXT
        self.btn_sort_oldest.color = (1, 1, 1, 1) if sort_mode == "oldest" else TEXT
        self.btn_sort_high.color = (1, 1, 1, 1) if sort_mode == "high" else TEXT
        self.btn_sort_low.color = (1, 1, 1, 1) if sort_mode == "low" else TEXT

        self._render()

    def refresh(self, data, history):
        self.health_strip.refresh(data)
        self._last_open = data.get("open_positions", [])
        self._last_history = history
        self._render()

    def _render(self):
        if self.mode == "open":
            source = self._last_open
        else:
            source = self._last_history

        label_prefix = "Total Open P&L" if self.mode == "open" else "Total Closed P&L"

        if self.mode == "open":
            self._render_open()
            # Sum from exactly the positions that got a real PositionCard
            # built, not from the raw list - if a position's card build
            # throws (caught below, shown as a red "couldn't display this
            # position" line instead of a normal card), the old code still
            # counted that position's pnl_usdt in the header total even
            # though no visible card carried that number, so the header
            # could show a total with no matching card sum on screen. Now
            # the header can never include a number the person can't also
            # find on a card.
            total = sum(float(pos.get("pnl_usdt", 0.0)) for pos in self._last_open
                        if pos.get("id") in self._position_cards)
            count = len(self._position_cards)
        else:
            filtered = self._filtered_history()
            total = sum(float(item.get("pnl_usdt", 0.0)) for item in filtered)
            count = len(filtered)
            self._render_history()

        self.total_label.text = f"{label_prefix}: {total:+.2f} USDT  |  Trades: {count}"
        self.total_label.color = GREEN if total >= 0 else RED

    def _render_open(self):
        if not hasattr(self, "_position_cards"):
            self._position_cards = {}

        if not self._last_open:
            self.list_box.clear_widgets()
            self._position_cards = {}
            self.list_box.add_widget(self.empty_label_open)
            return

        current_ids = [pos.get("id") for pos in self._last_open]
        existing_order = [c.trade_id for c in self.list_box.children[::-1] if hasattr(c, "trade_id")]

        if existing_order == current_ids:
            for pos in self._last_open:
                card = self._position_cards.get(pos.get("id"))
                if card:
                    card.update_data(pos)
            return

        new_ids = set(current_ids)
        for old_id in list(self._position_cards.keys()):
            if old_id not in new_ids:
                del self._position_cards[old_id]

        if self.empty_label_open.parent:
            self.list_box.remove_widget(self.empty_label_open)

        self.list_box.clear_widgets()
        for pos in self._last_open:
            pid = pos.get("id")
            card = self._position_cards.get(pid)
            try:
                if card is None:
                    card = PositionCard(pos)
                    self._position_cards[pid] = card
                else:
                    card.update_data(pos)
            except Exception as e:
                # A single position with unexpected/legacy data must never
                # blank out the WHOLE Open tab - previously an exception
                # here left list_box empty (already cleared above) for every
                # position after the bad one, forever, with no sign of it
                # anywhere on this screen (only visible as a traceback on a
                # different tab, if the user happened to switch to it).
                from recovered_bot import status as _status_mod
                _status_mod.add_alert("error", f"Position card render failed for "
                                                f"{pos.get('symbol', '?')} (#{pos.get('id', '?')}): {e}")
                if pid in self._position_cards:
                    del self._position_cards[pid]
                err_card = Label(text=f"{pos.get('symbol', '?')}: couldn't display this position "
                                       f"(see Alerts tab)", font_size=dp(12), color=RED,
                                  size_hint_y=None, height=dp(40))
                self.list_box.add_widget(err_card)
                continue
            self.list_box.add_widget(card)

    def _filtered_history(self):
        """Trade journal filters (symbol / win-loss / date range), applied
        client-side to the already-fetched closed_trades list - purely a
        display filter, doesn't touch trade_store's data or the Open tab."""
        items = list(self._last_history)

        if self.symbol_filter:
            items = [t for t in items if self.symbol_filter in str(t.get("symbol", "")).upper()]

        if self.result_filter == "win":
            items = [t for t in items if float(t.get("pnl_usdt", 0.0)) >= 0]
        elif self.result_filter == "loss":
            items = [t for t in items if float(t.get("pnl_usdt", 0.0)) < 0]

        if self.date_filter != "all":
            cutoff_days = {"today": 1, "7d": 7, "30d": 30}[self.date_filter]
            cutoff_ts = time.time() - cutoff_days * 86400
            items = [t for t in items if (t.get("closed_at_ts") or 0) >= cutoff_ts]

        return items

    def _render_history(self):
        # BUG FIXED: this used to clear_widgets() + rebuild every single
        # HistoryCard on every refresh tick (every ~2s, since refresh_ui
        # calls _render() unconditionally) - even though closed trades never
        # change after they're written. With a lot of history (each TP1-4
        # partial booking is its own row) that's a full widget-tree rebuild
        # every 2 seconds, which is exactly the "chipak ke atak" stutter when
        # switching to/staying on the History tab. Now it only rebuilds when
        # what should actually be on screen changes (new trade closed, sort
        # mode changed, or a journal filter changed) - same pattern as
        # _render_open already used.
        history_render = self._filtered_history()
        if self.sort_mode == "oldest":
            history_render = list(reversed(history_render))
        elif self.sort_mode == "high":
            history_render = sorted(history_render, key=lambda x: float(x.get('pnl_usdt', 0.0)), reverse=True)
        elif self.sort_mode == "low":
            history_render = sorted(history_render, key=lambda x: float(x.get('pnl_usdt', 0.0)))

        render_key = (self.sort_mode, tuple(id(t) for t in history_render), len(history_render))
        if getattr(self, "_history_render_key", None) == render_key and self.list_box.children:
            return
        self._history_render_key = render_key

        # NOTE: deliberately NOT touching self._position_cards here anymore -
        # that dict belongs to the Open tab's card cache (_render_open). It
        # used to get wiped on every History render, so switching back to
        # Open afterwards lost track of already-built cards for no reason.
        self.list_box.clear_widgets()
        if not history_render:
            self.list_box.add_widget(self.empty_label_history)
            return

        for trade in history_render:
            try:
                self.list_box.add_widget(HistoryCard(trade))
            except Exception as e:
                # Same protection _render_open() already has for the Open
                # tab: one malformed/legacy record must never blank out
                # every OTHER trade below it in the list, and must never
                # leave the tab looking frozen with no visible sign of why.
                from recovered_bot import status as _status_mod
                _status_mod.add_alert("error", f"History card render failed for "
                                                f"{trade.get('symbol', '?')} "
                                                f"(#{trade.get('signal_no', '?')}): {e}")
                err_card = Label(text=f"{trade.get('symbol', '?')}: couldn't display this trade "
                                       f"(see Alerts tab)", font_size=dp(12), color=RED,
                                  size_hint_y=None, height=dp(40))
                self.list_box.add_widget(err_card)


class PlaceholderScreen(Screen):
    def __init__(self, message, **kwargs):
        super().__init__(**kwargs)
        box = BoxLayout(orientation='vertical', padding=(dp(20), calc_top_pad(), dp(20), dp(20)))
        self.root_layout = box
        box.add_widget(Label(text=message, font_size=dp(17), color=SLATE))
        self.add_widget(box)


_ALERT_COLORS = {
    "open": ACCENT,
    "tp": GREEN,
    "sl": RED,
    "reversed": YELLOW,
    "manual": SLATE,
    "target": GREEN,
    "loss": RED,
    "skip": SLATE,
    "error": RED,
    "diag": ACCENT,
}
_ALERT_LABELS = {
    "open": "OPEN",
    "tp": "TP",
    "sl": "SL",
    "reversed": "REVERSED",
    "manual": "MANUAL",
    "target": "TARGET",
    "loss": "LOSS LIMIT",
    "skip": "SKIPPED",
    "error": "ERROR",
    "diag": "DIAG",
}
# Filter-chip groupings for the Alerts screen: which alert `type`s each chip
# shows. "trades" is the set a person actually cares about day-to-day; "diag"
# is temporary investigation logging (24h%-mismatch probes etc, see app.py)
# that used to be tagged "error" and flooded the top of this feed, burying
# real trade alerts - it now has its own type and its own chip.
_ALERT_FILTER_GROUPS = {
    "All": None,
    "Trades": {"open", "tp", "sl", "reversed", "manual", "target", "loss", "skip"},
    "Errors": {"error"},
    "Diag": {"diag"},
}


class AlertRow(CardLayout):
    """One row in the Alerts feed - a colored type badge, the message, and
    a timestamp. Alerts come from status['alerts'] (see recovered_bot/status.py
    add_alert()), populated at every signal open/close/partial-TP/SL/skip event."""
    def __init__(self, alert, **kwargs):
        super().__init__(orientation='horizontal', padding=(dp(12), dp(8)), spacing=dp(8),
                          size_hint_y=None, height=dp(52), **kwargs)
        atype = alert.get("type", "manual")
        color = _ALERT_COLORS.get(atype, SLATE)
        label_text = _ALERT_LABELS.get(atype, atype.upper())

        # Left accent bar - colored by alert type, gives each row a quick
        # at-a-glance signal even before reading the badge text (modernized
        # Alerts UI - previously just three plain Label columns).
        accent = BoxLayout(size_hint_x=None, width=dp(4))
        with accent.canvas.before:
            Color(*color)
            accent._rect = Rectangle(pos=accent.pos, size=accent.size)
        accent.bind(pos=lambda i, v: setattr(i._rect, 'pos', v),
                    size=lambda i, v: setattr(i._rect, 'size', v))
        self.add_widget(accent)

        badge = Label(text=label_text, font_size=dp(11), bold=True, color=color,
                      size_hint_x=0.22, halign='left', valign='middle')
        badge.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.add_widget(badge)

        msg = Label(text=alert.get("text", ""), font_size=dp(12), color=TEXT,
                    size_hint_x=0.6, halign='left', valign='middle', shorten=True, shorten_from='right')
        msg.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.add_widget(msg)

        ts = Label(text=alert.get("time", ""), font_size=dp(11), color=SLATE,
                   size_hint_x=0.18, halign='right', valign='middle')
        ts.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.add_widget(ts)


def _pct_move(price, entry):
    if not entry:
        return 0.0
    return abs(price - entry) / entry * 100.0


class SignalCard(CardLayout):
    """One signal in the Signals feed - same numbers Telegram shows
    (Entry/SL/TP1-4 with %, IST time), plus whether it actually opened a
    trade (or why not), and this symbol's current auto-trade mode so you
    can flip it right from here without hunting for it on Market Watch."""
    def __init__(self, sig, is_live=False, **kwargs):
        super().__init__(orientation='vertical', padding=(dp(12), dp(10)), spacing=dp(4),
                          size_hint_y=None, height=dp(128), **kwargs)
        self.symbol = sig.get("symbol", "")
        is_long = str(sig.get("action", "")).upper() == "BUY"
        direction = "LONG" if is_long else "SHORT"
        dcolor = GREEN if is_long else RED

        top = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(6))
        title = f"#{sig.get('signal_no', '?')}  {self.symbol}  {direction}"
        if is_live:
            # Was a green-circle emoji (U+1F7E2) - buildozer/p4a's bundled
            # Android font has no emoji glyph coverage, so this rendered as
            # a tofu/box character in front of the coin name on-device (the
            # ACCENT text color already carries the "this is live" meaning,
            # so the plain "LIVE " tag is enough without needing a glyph).
            title = "LIVE  " + title
        title_lbl = Label(text=title, font_size=dp(13), bold=True, color=(dcolor if not is_live else ACCENT),
                           halign='left', valign='middle', size_hint_x=0.62, shorten=True, shorten_from='right')
        title_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
        time_lbl = Label(text=sig.get("ist_time_str", ""), font_size=dp(11), color=SLATE,
                          halign='right', valign='middle', size_hint_x=0.38)
        time_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
        top.add_widget(title_lbl)
        top.add_widget(time_lbl)
        self.add_widget(top)

        entry = sig.get("entry_price", 0.0)
        sl = sig.get("stop_loss", 0.0)
        levels = (f"[color={ACCENT_HEX}]Entry {entry:g}[/color]   "
                  f"[color={RED_HEX}]SL {sl:g} ({_pct_move(sl, entry):.2f}%)[/color]")
        lvl_lbl = Label(text=levels, font_size=dp(11), color=SLATE, markup=True,
                        halign='left', valign='middle', size_hint_y=None, height=dp(18))
        lvl_lbl.bind(size=lambda i, s: setattr(i, 'text_size', (i.width, None)))
        self.add_widget(lvl_lbl)

        tp_parts = []
        for label, key in (("TP1", "tp1"), ("TP2", "tp2"), ("TP3", "tp3"), ("TP4", "tp4")):
            val = sig.get(key)
            if val is None:
                continue
            tp_parts.append(f"{label} {val:g} ({_pct_move(val, entry):.2f}%)")
        tp_lbl = Label(text="  ".join(tp_parts), font_size=dp(11), color=SLATE,
                       halign='left', valign='middle', size_hint_y=None, height=dp(18))
        tp_lbl.bind(size=lambda i, s: setattr(i, 'text_size', (i.width, None)))
        self.add_widget(tp_lbl)

        status_row = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(6))
        if sig.get("opened"):
            outcome = "[color=" + GREEN_HEX + "]Trade opened[/color]"
        else:
            outcome = f"[color={SLATE_HEX}]Skipped - {sig.get('skip_reason') or 'not opened'}[/color]"
        outcome_lbl = Label(text=outcome, font_size=dp(11), markup=True, color=SLATE,
                             halign='left', valign='middle', size_hint_x=0.66)
        outcome_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
        status_row.add_widget(outcome_lbl)

        mode = _get_symbol_mode(self.symbol)
        self.mode_btn = Button(text=mode, font_size=dp(11), bold=True, size_hint_x=0.34,
                                background_normal='', background_color=_MODE_BTN_COLOR.get(mode, SLATE),
                                color=(1, 1, 1, 1))
        self.mode_btn.bind(on_release=self._toggle_mode)
        status_row.add_widget(self.mode_btn)
        self.add_widget(status_row)

    def _toggle_mode(self, *args):
        new_mode = _cycle_symbol_mode(self.symbol)
        self.mode_btn.text = new_mode
        self.mode_btn.background_color = _MODE_BTN_COLOR.get(new_mode, SLATE)


class SignalsScreen(Screen):
    """Full signal history grouped by IST date, most-recent-first within
    each group - matches the Telegram alert format so you don't have to
    scroll Telegram to see what a past signal's levels were. A signal
    recorded in the last 10 minutes gets a LIVE badge and sits in its own
    group at the top regardless of date."""
    LIVE_WINDOW_SECONDS = 600

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.root_layout = BoxLayout(orientation='vertical', padding=(dp(14), calc_top_pad(), dp(14), dp(10)),
                                      spacing=dp(8))
        self.add_widget(self.root_layout)

        header = Label(text="Signals", font_size=dp(20), bold=True, color=TEXT,
                       size_hint_y=None, height=dp(32), halign='left', valign='middle')
        header.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.root_layout.add_widget(header)

        self.scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation='vertical', spacing=dp(10), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        self.scroll.add_widget(self.list_box)
        self.root_layout.add_widget(self.scroll)

        self.empty_label = Label(text="No signals yet.", font_size=dp(15), color=SLATE,
                                  size_hint_y=None, height=dp(44))
        self.list_box.add_widget(self.empty_label)
        self._render_key = None

    def refresh(self, data, signals):
        # Cheap diff: (count, most-recent signal_no) is enough to know
        # nothing new arrived - avoids rebuilding every card on every 2s
        # tick when the list hasn't actually changed.
        top_id = signals[0].get("signal_no") if signals else None
        render_key = (len(signals), top_id)
        if render_key == self._render_key and self.list_box.children:
            return
        self._render_key = render_key

        self.list_box.clear_widgets()
        if not signals:
            self.list_box.add_widget(self.empty_label)
            return

        now = time.time()
        live = [s for s in signals if now - s.get("recorded_at", 0) < self.LIVE_WINDOW_SECONDS]
        rest = [s for s in signals if now - s.get("recorded_at", 0) >= self.LIVE_WINDOW_SECONDS]

        if live:
            self.list_box.add_widget(Label(text="Live Signal", font_size=dp(13), bold=True, color=ACCENT,
                                            size_hint_y=None, height=dp(22), halign='left', valign='middle',
                                            text_size=(Window.width - dp(28), None)))
            for sig in live:
                self.list_box.add_widget(SignalCard(sig, is_live=True))

        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        current_group = None
        for sig in rest:
            grp = sig.get("date_str", "")
            if grp != current_group:
                current_group = grp
                label_txt = "Today" if grp == today_str else ("Yesterday" if grp == yesterday_str else grp)
                self.list_box.add_widget(Label(text=label_txt, font_size=dp(13), bold=True, color=SLATE,
                                                size_hint_y=None, height=dp(22), halign='left', valign='middle',
                                                text_size=(Window.width - dp(28), None)))
            self.list_box.add_widget(SignalCard(sig, is_live=False))


class AlertsScreen(Screen):
    """Alerts feed - signal opens/closes/partial-TP bookings/SL hits/reversals
    and daily-target/loss skip events, most-recent-first. Backed by
    status['alerts'] (capped 100 server-side); this screen renders the most
    recent 50 to keep the rebuild-every-refresh cheap."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.root_layout = BoxLayout(orientation='vertical', padding=(dp(14), calc_top_pad(), dp(14), dp(10)),
                                     spacing=dp(10))
        self.add_widget(self.root_layout)

        header = Label(text="Alerts", font_size=dp(20), bold=True, color=TEXT,
                       size_hint_y=None, height=dp(32), halign='left', valign='middle')
        header.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.root_layout.add_widget(header)

        self.health_strip = HealthStrip()
        self.root_layout.add_widget(self.health_strip)

        self.filter_mode = "All"
        filter_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(4))
        self.filter_buttons = {}
        for name in _ALERT_FILTER_GROUPS:
            btn = Button(text=name, font_size=dp(12), bold=True, background_normal='',
                         background_color=(ACCENT if name == "All" else BORDER),
                         color=((1, 1, 1, 1) if name == "All" else TEXT))
            btn.bind(on_release=lambda i, n=name: self._set_filter(n))
            self.filter_buttons[name] = btn
            filter_row.add_widget(btn)
        self.root_layout.add_widget(filter_row)

        self.scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation='vertical', spacing=dp(6), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        self.scroll.add_widget(self.list_box)
        self.root_layout.add_widget(self.scroll)

        self.empty_label = Label(text="No alerts yet.", font_size=dp(15), color=SLATE,
                                  size_hint_y=None, height=dp(44))
        self.list_box.add_widget(self.empty_label)
        self._last_count = 0
        self._last_alerts = []

    def _set_filter(self, name):
        self.filter_mode = name
        for n, btn in self.filter_buttons.items():
            btn.background_color = ACCENT if n == name else BORDER
            btn.color = (1, 1, 1, 1) if n == name else TEXT
        self._last_count = -1  # force rebuild even if the underlying alert count hasn't changed
        self._render(self._last_alerts)

    def refresh(self, data):
        self.health_strip.refresh(data)
        alerts = data.get("alerts", [])[:100]
        self._last_alerts = alerts
        # Alerts are prepended server-side (most-recent-first) and never
        # edited in place, so a changed raw length is enough to know
        # something new arrived - avoids rebuilding widgets every ~2s for
        # nothing when nothing changed (filter changes force it separately
        # via _set_filter above).
        if len(alerts) == self._last_count and self.list_box.children:
            return
        self._last_count = len(alerts)
        self._render(alerts)

    def _render(self, alerts):
        allowed = _ALERT_FILTER_GROUPS.get(self.filter_mode)
        shown = alerts if allowed is None else [a for a in alerts if a.get("type") in allowed]
        shown = shown[:50]

        self.list_box.clear_widgets()
        if not shown:
            self.empty_label.text = "No alerts yet." if not alerts else f"No {self.filter_mode.lower()} alerts."
            self.list_box.add_widget(self.empty_label)
            return
        for alert in shown:
            self.list_box.add_widget(AlertRow(alert))


class MarketRow(CardLayout):
    """Market Watch row - shows price/change plus, on a second line, the
    last signal for this symbol, how many open positions it currently has,
    and a per-coin AUTO/OFF toggle. Modernized: colored left accent bar
    (green/red by 24h direction) and the change% rendered as a rounded pill
    instead of plain colored text, replacing the old flat white-box look."""
    def __init__(self, item, data=None, **kwargs):
        super().__init__(orientation='horizontal', padding=(0, 0), spacing=0,
                          size_hint_y=None, height=dp(80), **kwargs)
        self.symbol = item.get("symbol", "")
        data = data or {}
        chg0 = item.get("change_pct", 0.0)

        self.accent = BoxLayout(size_hint_x=None, width=dp(4))
        with self.accent.canvas.before:
            self._accent_color = Color(*(GREEN if chg0 >= 0 else RED))
            self._accent_rect = Rectangle(pos=self.accent.pos, size=self.accent.size)
        self.accent.bind(pos=lambda i, v: setattr(self._accent_rect, 'pos', v),
                          size=lambda i, v: setattr(self._accent_rect, 'size', v))
        self.add_widget(self.accent)

        body = BoxLayout(orientation='vertical', padding=(dp(12), dp(10)), spacing=dp(6))

        top = BoxLayout(size_hint_y=None, height=dp(26))
        fav_on = _get_favorite(self.symbol)
        self.fav_btn = Button(text=("\u2605" if fav_on else "\u2606"), font_size=dp(16), bold=True,
                               size_hint_x=0.12, background_normal='', background_color=(0, 0, 0, 0),
                               color=(YELLOW if fav_on else SLATE))
        self.fav_btn.bind(on_release=self.toggle_favorite)
        top.add_widget(self.fav_btn)
        self.sym_label = Label(text=self.symbol, font_size=dp(16), bold=True, color=TEXT,
                    halign='left', valign='middle', size_hint_x=0.32)
        self.sym_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        self.price_label = Label(text=f"{item.get('price', 0.0):g}", font_size=dp(13), color=SLATE,
                      halign='center', valign='middle', size_hint_x=0.28)
        self.price_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        # Change% as a rounded pill (own background) instead of plain
        # colored text sitting on the card background - reads more like a
        # modern ticker badge, same green/red meaning as before.
        self.chg_pill = BoxLayout(size_hint_x=0.28, size_hint_y=None, height=dp(24))
        with self.chg_pill.canvas.before:
            self._pill_color = Color(*self._pill_bg(chg0))
            self._pill_rect = RoundedRectangle(pos=self.chg_pill.pos, size=self.chg_pill.size, radius=[dp(12)])
        self.chg_pill.bind(pos=lambda i, v: setattr(self._pill_rect, 'pos', v),
                           size=lambda i, v: setattr(self._pill_rect, 'size', v))
        self.chg_label = Label(text=f"{chg0:+.2f}%", font_size=dp(13), bold=True,
                           color=(1, 1, 1, 1), halign='center', valign='middle')
        self.chg_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.chg_pill.add_widget(self.chg_label)

        top.add_widget(self.sym_label)
        top.add_widget(self.price_label)
        top.add_widget(self.chg_pill)

        bottom = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(6))

        sig_info = data.get("last_signal_by_symbol", {}).get(self.symbol)
        sig_action = sig_info.get("action") if sig_info else None
        sig_color = GREEN if sig_action == "BUY" else (RED if sig_action == "SELL" else SLATE)
        self.sig_label = Label(text=f"Signal: {sig_action or '--'}", font_size=dp(12), color=sig_color,
                           halign='left', valign='middle', size_hint_x=0.38)
        self.sig_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        pos_count = sum(1 for p in data.get("open_positions", []) if p.get("symbol") == self.symbol)
        self.pos_label = Label(text=f"Pos: {pos_count}", font_size=dp(12), color=SLATE,
                           halign='left', valign='middle', size_hint_x=0.24)
        self.pos_label.bind(size=lambda i, s: setattr(i, 'text_size', s))

        mode = _get_symbol_mode(self.symbol)
        self.auto_btn = Button(text=mode, font_size=dp(11), bold=True,
                                size_hint_x=0.38,
                                background_normal='',
                                background_color=_MODE_BTN_COLOR[mode],
                                color=(1, 1, 1, 1))
        self.auto_btn.bind(on_release=self._toggle_auto)

        bottom.add_widget(self.sig_label)
        bottom.add_widget(self.pos_label)
        bottom.add_widget(self.auto_btn)

        body.add_widget(top)
        body.add_widget(bottom)
        self.add_widget(body)

    @staticmethod
    def _pill_bg(chg):
        # Slightly muted fill (not full-saturation GREEN/RED) so white pill
        # text stays readable in both light and dark themes.
        return (0.0, 0.55, 0.33, 1) if chg >= 0 else (0.70, 0.20, 0.20, 1)

    def update_data(self, item, data=None):
        data = data or {}
        self.price_label.text = f"{item.get('price', 0.0):g}"
        chg = item.get("change_pct", 0.0)
        self.chg_label.text = f"{chg:+.2f}%"
        self._pill_color.rgba = self._pill_bg(chg)
        self._accent_color.rgba = GREEN if chg >= 0 else RED

        sig_info = data.get("last_signal_by_symbol", {}).get(self.symbol)
        sig_action = sig_info.get("action") if sig_info else None
        sig_color = GREEN if sig_action == "BUY" else (RED if sig_action == "SELL" else SLATE)
        self.sig_label.text = f"Signal: {sig_action or '--'}"
        self.sig_label.color = sig_color

        pos_count = sum(1 for p in data.get("open_positions", []) if p.get("symbol") == self.symbol)
        self.pos_label.text = f"Pos: {pos_count}"

        mode = _get_symbol_mode(self.symbol)
        self.auto_btn.text = mode
        self.auto_btn.background_color = _MODE_BTN_COLOR[mode]

    def _toggle_auto(self, *args):
        # Cycles OFF -> BUY -> SELL -> BOTH -> OFF on each tap, replacing the
        # old plain AUTO/OFF switch with a per-coin trade-direction filter.
        new_mode = _cycle_symbol_mode(self.symbol)
        self.auto_btn.text = new_mode
        self.auto_btn.background_color = _MODE_BTN_COLOR[new_mode]

    def toggle_favorite(self, *args):
        new_state = not _get_favorite(self.symbol)
        _set_favorite(self.symbol, new_state)
        self.fav_btn.text = "\u2605" if new_state else "\u2606"
        self.fav_btn.color = YELLOW if new_state else SLATE

class MarketScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.root_layout = BoxLayout(orientation='vertical', padding=(dp(10), calc_top_pad(), dp(10), dp(10)),
                                      spacing=dp(8))
        self.add_widget(self.root_layout)

        # Favorites / All / Gainers / Losers tabs - filters which subset of
        # the watchlist's movers list is shown, without touching WHICH
        # symbols are tracked (that's still get_current_movers()' top/bottom
        # 10 - this only changes what's displayed). Same ACCENT/BORDER
        # toggle-button pattern already used for Open/History and the sort
        # buttons elsewhere in this file.
        self.selected_tab = "All"
        self._last_market_data = None
        tabs_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
        self.tab_buttons = {}
        for tab_name in ("Favorites", "All", "Gainers", "Losers"):
            btn = Button(text=tab_name, font_size=dp(12), bold=True,
                         background_normal='', background_color=BORDER, color=TEXT)
            btn.bind(on_press=lambda inst, t=tab_name: self._select_tab(t))
            self.tab_buttons[tab_name] = btn
            tabs_row.add_widget(btn)
        self.tab_buttons["All"].background_color = ACCENT
        self.tab_buttons["All"].color = (1, 1, 1, 1)
        self.root_layout.add_widget(tabs_row)

        header_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        # shorten=True + a fixed short vocabulary (no full words like
        # "RECONNECTING") is deliberate: an unclipped Kivy Label that wraps
        # to 2 lines renders taller than this row's height and spills its
        # texture over the row above/below it since Label draws don't clip
        # by default - that's what caused the badge to visually overlap
        # "Top Gainers / Losers" on-device. Short one-line text avoids the
        # wrap entirely; shorten=True is just a safety net if it ever grows.
        self.ws_badge_label = Label(text="", font_size=dp(12), bold=True, color=SLATE,
                                     halign='left', valign='middle', size_hint_x=0.26,
                                     shorten=True, shorten_from='right')
        self.ws_badge_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        header_row.add_widget(self.ws_badge_label)
        self.header_title_label = Label(text="Top Gainers / Losers (24h, from your watchlist)",
                                         font_size=dp(12), color=SLATE, halign='left', valign='middle',
                                         size_hint_x=0.46, shorten=True, shorten_from='right')
        header_row.add_widget(self.header_title_label)
        self.sync_label = Label(text="Not synced yet...", font_size=dp(11), color=SLATE,
                                 halign='right', valign='middle', size_hint_x=0.28,
                                 shorten=True, shorten_from='left')
        self.sync_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        header_row.add_widget(self.sync_label)
        self.root_layout.add_widget(header_row)

        # Second row: only populated (and only takes visible height) when
        # WebSocket isn't LIVE and we actually have an error string to show -
        # this is the on-device diagnostic the architecture doc asked for,
        # so a stuck badge is debuggable from the phone alone instead of
        # needing adb logcat.
        self.ws_error_label = Label(text="", font_size=dp(10), color=RED,
                                     halign='left', valign='top', size_hint_y=None, height=0,
                                     shorten=True, shorten_from='right')
        self.ws_error_label.bind(size=lambda i, s: setattr(i, 'text_size', s))
        self.root_layout.add_widget(self.ws_error_label)

        self.scroll = ScrollView(size_hint=(1, 1))
        self.list_box = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
        self.list_box.bind(minimum_height=self.list_box.setter('height'))
        self.scroll.add_widget(self.list_box)
        self.root_layout.add_widget(self.scroll)

        self.empty_label = Label(text="Loading market data...", font_size=dp(15), color=SLATE,
                                  size_hint_y=None, height=dp(44))
        self.list_box.add_widget(self.empty_label)

    _TAB_HEADER_TEXT = {
        "Favorites": "Favorites (24h, from your watchlist)",
        "All": "Top Gainers / Losers (24h, from your watchlist)",
        "Gainers": "Top Gainers (24h, from your watchlist)",
        "Losers": "Top Losers (24h, from your watchlist)",
    }

    def _select_tab(self, tab_name):
        self.selected_tab = tab_name
        self.header_title_label.text = self._TAB_HEADER_TEXT[tab_name]
        for name, btn in self.tab_buttons.items():
            if name == tab_name:
                btn.background_color = ACCENT
                btn.color = (1, 1, 1, 1)
            else:
                btn.background_color = BORDER
                btn.color = TEXT
        if self._last_market_data is not None:
            self.refresh(self._last_market_data)

    def refresh(self, data):
        self._last_market_data = data
        from recovered_bot import market_ws
        ws_state = market_ws.get_ws_state()
        # Was prefixed with a filled/hollow bullet (U+25CF/U+25CB) - same
        # font-coverage gap as the LIVE-badge emoji above, rendered as a
        # tofu box before the text on-device. The color already encodes
        # LIVE (green) vs degraded (yellow/red/grey) states, so plain text
        # carries the same information without the glyph.
        badge = {"LIVE": ("LIVE", GREEN), "CONNECTING": ("CONN", SLATE),
                 "RECONNECTING": ("RECONN", YELLOW), "STALE": ("STALE", RED),
                 "DISCONNECTED": ("REST", SLATE)}.get(ws_state["state"], ("", SLATE))
        self.ws_badge_label.text = badge[0]
        self.ws_badge_label.color = badge[1]

        if ws_state["state"] != "LIVE" and ws_state.get("error"):
            self.ws_error_label.text = f"WS: {ws_state['error']}"[:120]
            self.ws_error_label.height = dp(16)
        else:
            self.ws_error_label.text = ""
            self.ws_error_label.height = 0

        synced_at = data.get("market_synced_at")
        if synced_at:
            age = max(0, time.time() - synced_at)
            if age < 90:
                self.sync_label.text = f"Last synced {int(age)}s ago"
                self.sync_label.color = SLATE
            else:
                mins = int(age // 60)
                self.sync_label.text = f"Last synced {mins}m ago \u2013 check connection"
                self.sync_label.color = RED
        else:
            self.sync_label.text = "Not synced yet..."
            self.sync_label.color = SLATE

        movers = data.get("market_movers", [])
        # Overlay a fresher price/% from the WebSocket live cache when we
        # have one for this symbol - REST (market_movers, refreshed every
        # WATCHLIST_REFRESH_INTERVAL) still decides WHICH symbols are shown
        # (membership); this only replaces the displayed number for symbols
        # already in that list. Falls back to the REST value untouched if
        # the WS cache has nothing fresh for a symbol (not connected,
        # symbol not pushed yet, or the entry aged past STALE_AFTER_SECONDS).
        # Both REST (app.py's get_current_movers()) and this WS overlay now
        # use Binance's own rolling 24h % - they used to disagree (REST used
        # a custom "since 5:30 AM IST" snapshot, WS used Binance's real 24h),
        # which is exactly why the app's number didn't match Binance's own
        # app for a coin that had already moved before the day-open snapshot
        # was taken (e.g. right after installing, or opening the app late in
        # the day) - see the long comment in get_current_movers().
        # Overlay a fresher LIVE PRICE from the WebSocket cache when we have
        # one for this symbol - REST (market_movers, refreshed every
        # WATCHLIST_REFRESH_INTERVAL) decides BOTH which symbols are shown
        # (membership) AND the % shown next to them. The % used to also get
        # overlaid from the WS mini-ticker's own change_pct - that's what
        # caused the app's Gainers/Losers list to visibly disagree with
        # Binance's own Ranking screen for several coins at once (not just
        # new listings): membership was decided by REST's number, but the
        # SAME symbol's displayed % came from a second, independent
        # computation (WS's own open/close) that can drift from REST's by
        # more than a couple of points for reasons that have nothing to do
        # with new listings - at that point the screen is showing two
        # different numbers for "why this coin is even in this list", which
        # reads as "app selected a random/wrong coin" even though the
        # underlying watchlist membership was correct the whole time. Now
        # only the price is taken from WS (harmless - it's the same 24h%
        # denominator either way); % always comes from the same REST call
        # that decided the list, so what's on screen always matches why the
        # coin is on screen.
        overlaid = []
        for item in movers:
            live = market_ws.get_live(item.get("symbol", ""))
            if live:
                merged = dict(item)
                merged["price"] = live["price"]
                overlaid.append(merged)
                # DIAGNOSTIC (temporary): both sources are supposed to be the
                # same Binance 24h % - if the WS value and the REST value
                # disagree by more than a couple of points for the SAME coin
                # at the SAME moment, that's a real pipeline bug (not just
                # "new listing" or normal price drift) and worth flagging
                # separately from the app.py DIAG alert.
                try:
                    sym = item.get("symbol", "?")
                    # Throttled to once per symbol per 5 minutes - refresh()
                    # runs every 0.5s while this tab is open, so without a
                    # throttle a persistent mismatch would flood the Alerts
                    # tab and bury real trade alerts within seconds.
                    last = self._diag_last_logged.get(sym, 0) if hasattr(self, "_diag_last_logged") else 0
                    if abs(live["change_pct"] - item.get("change_pct", 0.0)) > 3.0 and (time.time() - last) > 300:
                        if not hasattr(self, "_diag_last_logged"):
                            self._diag_last_logged = {}
                        self._diag_last_logged[sym] = time.time()
                        from recovered_bot import status as _status_mod
                        _status_mod.add_alert(
                            "error",
                            f"DIAG {sym}: REST%={item.get('change_pct',0):+.2f} "
                            f"vs WS%={live['change_pct']:+.2f} - same coin, same moment, "
                            f"real mismatch between our two data sources"
                        )
                except Exception:
                    pass
            else:
                overlaid.append(item)
        movers = overlaid
        had_any_movers = len(movers) > 0

        # Apply the selected tab. "All" and "Favorites" keep the existing
        # favorites-first sort; Gainers/Losers show only that category (still
        # favorites-first within it) so a favorited loser doesn't show up
        # under the Gainers tab.
        if self.selected_tab == "Favorites":
            movers = [m for m in movers if _get_favorite(m.get("symbol", ""))]
        elif self.selected_tab == "Gainers":
            movers = [m for m in movers if m.get("category") == "gainer"]
        elif self.selected_tab == "Losers":
            movers = [m for m in movers if m.get("category") == "loser"]

        movers = sorted(movers, key=lambda item: not _get_favorite(item.get("symbol", "")))
        if not hasattr(self, "_market_rows"):
            self._market_rows = {}

        if not movers:
            # Distinguish "no data at all yet" (real loading/connection
            # state) from "data loaded fine, this tab just has nothing in
            # it" (e.g. no favorites tapped yet) - the old single "Loading
            # market data..." text was misleading in the second case.
            if not had_any_movers:
                self.empty_label.text = "Loading market data..."
            elif self.selected_tab == "Favorites":
                self.empty_label.text = "No favorites yet - tap \u2606 on a coin to add one."
            elif self.selected_tab == "Gainers":
                self.empty_label.text = "No gainers in the current watchlist."
            elif self.selected_tab == "Losers":
                self.empty_label.text = "No losers in the current watchlist."
            self.list_box.clear_widgets()
            self._market_rows = {}
            self.list_box.add_widget(self.empty_label)
            return

        current_symbols = [item.get("symbol") for item in movers]
        existing_order = [r.symbol for r in self.list_box.children[::-1] if hasattr(r, "symbol")]

        if existing_order == current_symbols:
            for item in movers:
                row = self._market_rows.get(item.get("symbol"))
                if row:
                    row.update_data(item, data=data)
            return

        new_symbols = set(current_symbols)
        for old_sym in list(self._market_rows.keys()):
            if old_sym not in new_symbols:
                del self._market_rows[old_sym]

        if self.empty_label.parent:
            self.list_box.remove_widget(self.empty_label)

        self.list_box.clear_widgets()
        for item in movers:
            sym = item.get("symbol")
            row = self._market_rows.get(sym)
            if row is None:
                row = MarketRow(item, data=data)
                self._market_rows[sym] = row
            else:
                row.update_data(item, data=data)
            self.list_box.add_widget(row)


class SettingsScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from recovered_bot import settings_store

        self.root_layout = BoxLayout(orientation='vertical', padding=(dp(10), calc_top_pad(), dp(10), dp(10)),
                                     spacing=dp(10))
        self.add_widget(self.root_layout)

        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation='vertical', spacing=dp(10), size_hint_y=None, padding=(0, 0, 0, dp(20)))
        content.bind(minimum_height=content.setter('height'))

        card = CardLayout(orientation='vertical', padding=dp(14), spacing=dp(12), size_hint_y=None)
        card.bind(minimum_height=card.setter('height'))

        self.inputs = {}
        self.text_inputs = {}

        def add_row(label_text, field_name, is_toggle=False, is_text=False):
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
            lbl = Label(text=label_text, font_size=dp(15), color=TEXT, halign='left', valign='middle', size_hint_x=0.5)
            lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
            row.add_widget(lbl)

            if is_toggle:
                pass
            elif is_text:
                inp = TextInput(text=str(settings_store.get(field_name, "") or ""),
                                font_size=dp(14), multiline=False, size_hint_x=0.5)
                self.text_inputs[field_name] = inp
                row.add_widget(inp)
            else:
                inp = TextInput(text=str(settings_store.get(field_name, "")),
                                font_size=dp(15), multiline=False, input_filter='float', size_hint_x=0.5)
                self.inputs[field_name] = inp
                row.add_widget(inp)
            card.add_widget(row)
            return row

        add_row("Invest Amount (USDT):", "invest_amount")

        tp_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        tp_lbl = Label(text="TP Mode:", font_size=dp(15), color=TEXT, halign='left', valign='middle', size_hint_x=0.5)
        tp_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
        tp_row.add_widget(tp_lbl)

        self.tp_mode = settings_store.get("tp_mode", "percent")
        tp_box = BoxLayout(spacing=dp(4), size_hint_x=0.5)
        self.btn_pct = Button(text="Percent", font_size=dp(13), bold=True,
                              background_normal='', background_color=ACCENT if self.tp_mode == "percent" else BORDER,
                              color=(1,1,1,1) if self.tp_mode == "percent" else TEXT)
        self.btn_usdt = Button(text="USDT", font_size=dp(13), bold=True,
                               background_normal='', background_color=ACCENT if self.tp_mode == "usdt" else BORDER,
                               color=(1,1,1,1) if self.tp_mode == "usdt" else TEXT)

        def set_tp_mode(m, *args):
            self.tp_mode = m
            self.btn_pct.background_color = ACCENT if m == "percent" else BORDER
            self.btn_pct.color = (1,1,1,1) if m == "percent" else TEXT
            self.btn_usdt.background_color = ACCENT if m == "usdt" else BORDER
            self.btn_usdt.color = (1,1,1,1) if m == "usdt" else TEXT

        self.btn_pct.bind(on_release=lambda x: set_tp_mode("percent"))
        self.btn_usdt.bind(on_release=lambda x: set_tp_mode("usdt"))
        tp_box.add_widget(self.btn_pct)
        tp_box.add_widget(self.btn_usdt)
        tp_row.add_widget(tp_box)
        card.add_widget(tp_row)

        add_row("TP Value:", "tp_value")
        add_row("SL Value:", "sl_value")
        # This note exists because it kept confusing people: every NEW trade
        # follows the strategy's own indicator-calculated SL/TP1-4 (the exact
        # numbers Telegram sends), coin-by-coin - it does NOT use this
        # percent/USDT value at all. This field only matters for very old
        # positions opened before signal-level tracking existed.
        tp_note = Label(
            text="Note: new trades always use the indicator's own SL/TP1-TP4\n"
                 "per coin (same as Telegram). TP/SL Value above only applies\n"
                 "to old positions opened before that tracking existed.",
            font_size=dp(11), color=SLATE, halign='left', valign='top',
            size_hint_y=None, height=dp(48))
        tp_note.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(tp_note)
        add_row("Leverage:", "leverage")

        auto_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        auto_lbl = Label(text="Global Auto-Trade:", font_size=dp(15), color=TEXT, halign='left', valign='middle', size_hint_x=0.5)
        auto_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
        auto_row.add_widget(auto_lbl)

        self.auto_exec = settings_store.get("auto_execute", True)
        self.btn_auto = Button(text=("ON" if self.auto_exec else "OFF"), font_size=dp(13), bold=True,
                               size_hint_x=0.5, background_normal='',
                               background_color=GREEN if self.auto_exec else (0.55, 0.55, 0.58, 1),
                               color=(1,1,1,1))

        def toggle_auto(*args):
            self.auto_exec = not self.auto_exec
            self.btn_auto.text = "ON" if self.auto_exec else "OFF"
            self.btn_auto.background_color = GREEN if self.auto_exec else (0.55, 0.55, 0.58, 1)

        self.btn_auto.bind(on_release=toggle_auto)
        auto_row.add_widget(self.btn_auto)
        card.add_widget(auto_row)

        theme_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        theme_lbl = Label(text="Theme:", font_size=dp(15), color=TEXT, halign='left', valign='middle', size_hint_x=0.5)
        theme_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))
        theme_row.add_widget(theme_lbl)

        self.theme_choice = settings_store.get("theme", "light")
        self.btn_theme = Button(text=("Dark" if self.theme_choice == "dark" else "Light"), font_size=dp(13), bold=True,
                                size_hint_x=0.5, background_normal='',
                                background_color=ACCENT,
                                color=(1, 1, 1, 1))

        def toggle_theme(*args):
            self.theme_choice = "light" if self.theme_choice == "dark" else "dark"
            self.btn_theme.text = "Dark" if self.theme_choice == "dark" else "Light"

        self.btn_theme.bind(on_release=toggle_theme)
        theme_row.add_widget(self.btn_theme)
        card.add_widget(theme_row)

        add_row("INR Rate:", "inr_rate")
        add_row("Daily Target USDT:", "daily_target_usdt")
        add_row("Daily Loss Limit USDT:", "daily_loss_limit_usdt")
        add_row("Min Open Positions (0=off):", "min_open_positions")

        telegram_header = Label(text="Telegram", font_size=dp(14), bold=True, color=SLATE,
                                halign='left', valign='middle', size_hint_y=None, height=dp(24))
        telegram_header.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(telegram_header)
        add_row("Bot Token:", "telegram_bot_token", is_text=True)
        add_row("Chat ID:", "telegram_chat_id", is_text=True)
        telegram_note = Label(
            text="Paste your BotFather token here - never hardcode it in\n"
                 "source. Restart the app after saving for it to take effect.",
            font_size=dp(11), color=SLATE, halign='left', valign='top',
            size_hint_y=None, height=dp(32))
        telegram_note.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(telegram_note)

        strategy_header = Label(text="Strategy Settings", font_size=dp(14), bold=True, color=SLATE,
                                halign='left', valign='middle', size_hint_y=None, height=dp(24))
        strategy_header.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(strategy_header)

        add_row("EMA Length:", "ema_length")
        add_row("Lookback Length:", "lookback_len")
        strategy_note = Label(
            text="Note: strategy changes only take effect after the app is\n"
                 "fully closed and reopened (bot restart), not live.",
            font_size=dp(11), color=SLATE, halign='left', valign='top',
            size_hint_y=None, height=dp(32))
        strategy_note.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(strategy_note)

        scalp_header = Label(text="Scalping", font_size=dp(14), bold=True, color=SLATE,
                             halign='left', valign='middle', size_hint_y=None, height=dp(24))
        scalp_header.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(scalp_header)
        add_row("Auto-Close ROI % (0=off):", "scalp_close_roi_pct")
        scalp_note = Label(
            text="Every open position closes itself the instant ITS OWN\n"
                 "ROI touches this value - independent of its TP1-4/SL and\n"
                 "of every other open position. Applies live (no restart\n"
                 "needed) and to already-open positions too. 0 = disabled.",
            font_size=dp(11), color=SLATE, halign='left', valign='top',
            size_hint_y=None, height=dp(44))
        scalp_note.bind(size=lambda i, s: setattr(i, 'text_size', s))
        card.add_widget(scalp_note)

        save_box = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        save_btn = Button(text="Save", font_size=dp(15), bold=True, background_normal='',
                          background_color=ACCENT, color=(1,1,1,1), size_hint_x=0.5)
        save_btn.bind(on_release=self.save_settings)
        self.status_lbl = Label(text="", font_size=dp(14), color=TEXT, halign='left', valign='middle', size_hint_x=0.5)
        self.status_lbl.bind(size=lambda i, s: setattr(i, 'text_size', s))

        save_box.add_widget(save_btn)
        save_box.add_widget(self.status_lbl)
        card.add_widget(save_box)

        content.add_widget(card)

        reset_btn = Button(text="Reset Paper-Trade Data", font_size=dp(15), bold=True, background_normal='',
                           background_color=RED, color=(1,1,1,1), size_hint_y=None, height=dp(44))
        reset_btn.bind(on_release=self.confirm_reset)
        content.add_widget(reset_btn)

        footer = CardLayout(orientation='vertical', padding=dp(14), spacing=dp(8), size_hint_y=None)
        footer.bind(minimum_height=footer.setter('height'))

        self.bot_status_lbl = Label(text="Bot Status: --", font_size=dp(14), color=SLATE, halign='center')
        footer.add_widget(self.bot_status_lbl)

        footer.add_widget(Label(text="Telegram: Configured", font_size=dp(14), color=SLATE, halign='center', size_hint_y=None, height=dp(20)))
        footer.add_widget(Label(text="v0.1", font_size=dp(12), color=SLATE, halign='center', size_hint_y=None, height=dp(20)))
        content.add_widget(footer)

        scroll.add_widget(content)
        self.root_layout.add_widget(scroll)

    def save_settings(self, *args):
        from recovered_bot import settings_store

        kwargs = {
            "tp_mode": self.tp_mode,
            "auto_execute": self.auto_exec,
            "theme": self.theme_choice
        }

        saved_count = 0
        for field, inp in self.inputs.items():
            val_str = inp.text.strip()
            if not val_str:
                continue
            try:
                val = float(val_str)
                kwargs[field] = val
                saved_count += 1
            except ValueError:
                pass

        for field, inp in self.text_inputs.items():
            kwargs[field] = inp.text.strip()
            saved_count += 1

        old_theme = settings_store.get("theme", "light")
        settings_store.update(**kwargs)
        if old_theme != self.theme_choice:
            self.status_lbl.text = "Saved. Restart app to apply new theme."
        else:
            self.status_lbl.text = "Saved."
        from kivy.clock import Clock
        Clock.schedule_once(lambda dt: setattr(self.status_lbl, 'text', ''), 2)

    def confirm_reset(self, *args):
        box = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        box.add_widget(Label(text="Are you sure?\nThis clears all trade history.", halign='center'))

        btn_box = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(40))
        btn_yes = Button(text="Yes", background_normal='', background_color=RED)
        btn_no = Button(text="Cancel", background_normal='', background_color=BORDER, color=TEXT)

        btn_box.add_widget(btn_yes)
        btn_box.add_widget(btn_no)
        box.add_widget(btn_box)

        popup = Popup(title="Confirm Reset", content=box, size_hint=(0.8, 0.4), auto_dismiss=False)

        def on_yes(instance):
            from recovered_bot import trade_store, paper_trader
            trade_store.reset()
            paper_trader.reset()
            popup.dismiss()

        btn_yes.bind(on_release=on_yes)
        btn_no.bind(on_release=popup.dismiss)
        popup.open()

    def refresh(self, data):
        if data:
            self.bot_status_lbl.text = f"Bot Status: {data.get('bot_status', 'Unknown')}"


class NavButton(Button):
    def __init__(self, text, **kwargs):
        super().__init__(text=text, font_size=dp(12), bold=True,
                          background_normal='', background_color=BORDER,
                          color=TEXT, **kwargs)


class TradeExitPanelApp(App):
    def build(self):
        from recovered_bot import settings_store, trade_store, status, paper_trader, signal_log
        settings_store.init(os.path.join(self.user_data_dir, "settings.json"))
        trade_store.init(os.path.join(self.user_data_dir, "trades.json"))
        paper_trader.init(os.path.join(self.user_data_dir, "positions.json"))
        signal_log.init(os.path.join(self.user_data_dir, "signal_log.json"))
        status.update(**trade_store.get_today_stats())

        # Re-read the saved theme now that settings_store has the REAL
        # on-device settings.json path (settings_store.init() just ran,
        # above). The module-level read at the top of this file always saw
        # the "light" default, because it runs at import time - before
        # init() has pointed settings_store at a real file. Every screen/
        # widget is constructed AFTER this point (below), so they all read
        # the corrected values.
        global BG, CARD_BG, TEXT, BORDER, TRACK_BG, ACCENT, SLATE
        _theme = settings_store.get("theme", "light")
        _palette = _DARK_PALETTE if _theme == "dark" else _LIGHT_PALETTE
        BG = _palette["BG"]
        CARD_BG = _palette["CARD_BG"]
        TEXT = _palette["TEXT"]
        BORDER = _palette["BORDER"]
        TRACK_BG = _palette["TRACK_BG"]
        ACCENT = _palette["ACCENT"]
        SLATE = _palette["SLATE"]

        self.status_module = None
        self.trade_store_module = None
        self.signal_log_module = None
        self.title = "TradeExitPanel"

        root = BoxLayout(orientation='vertical', padding=(0, 0, 0, calc_bottom_pad()))
        self.app_root = root
        with root.canvas.before:
            Color(*BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        self.sm = ScreenManager(transition=NoTransition())
        self.dashboard_screen = DashboardScreen(name="dashboard")
        self.positions_screen = PositionsScreen(name="positions")
        self.market_screen = MarketScreen(name="market")
        self.sm.add_widget(self.dashboard_screen)
        self.sm.add_widget(self.positions_screen)
        self.sm.add_widget(self.market_screen)

        self.settings_screen = SettingsScreen(name="settings")
        self.sm.add_widget(self.settings_screen)

        self.alerts_screen = AlertsScreen(name="alerts")
        self.sm.add_widget(self.alerts_screen)

        self.signals_screen = SignalsScreen(name="signals")
        self.sm.add_widget(self.signals_screen)

        self.placeholder_screens = []
        for scr in self.placeholder_screens:
            self.sm.add_widget(scr)

        nav = BoxLayout(size_hint_y=None, height=dp(66), spacing=dp(2), padding=(dp(2), dp(4)))
        for label, screen_name in [("Dash", "dashboard"), ("Pos", "positions"),
                                    ("Market", "market"), ("Signals", "signals"),
                                    ("Alerts", "alerts"), ("Settings", "settings")]:
            btn = NavButton(label)
            btn.bind(on_release=lambda inst, sn=screen_name: self._switch(sn))
            nav.add_widget(btn)

        root.add_widget(self.sm)
        root.add_widget(nav)

        Window.bind(size=self._rescale_padding)

        send_telegram("App build() started - UI created OK")
        Clock.schedule_once(self.safe_start, 0.3)
        return root

    def on_pause(self):
        # Without this override, some Android/Kivy combinations default to
        # killing the process the moment you switch away (not swipe away,
        # just tap another app / go home) instead of pausing it. Returning
        # True tells Android "let this keep running in the background."
        #
        # Honest limits: this does NOT survive the user swiping the app
        # away from Recent Apps (Android always fully kills the process
        # then - nothing an app can do about that from the inside), and it
        # does NOT stop an aggressive OEM battery-saver (Xiaomi/Oppo/Vivo
        # etc.) from killing it later anyway unless you manually exempt the
        # app in that phone's battery settings. A full Android Foreground
        # Service (persistent notification + FOREGROUND_SERVICE permission)
        # is the real, complete fix for that - deferred for now since it's a
        # bigger, riskier change than "just add this."
        return True

    def on_resume(self):
        # Force every screen to fully rebuild its widget tree on the next
        # refresh tick instead of trusting its "did anything change since
        # last render" cache - after a real pause (minutes/hours, screen
        # off, Android may have reclaimed GL resources) that cache can be
        # comparing against a widget tree that no longer reflects what's
        # actually on screen, which is how a tab can come back looking
        # empty/stuck even though the underlying data is fine.
        # ROOT CAUSE OF "black screen -> Trades: 0 / P&L 0.00 for a while
        # after switching back" (found by tracing _render_open()/MarketScreen
        # .refresh(), not guessed): this used to clear ONLY the lookup dict
        # (_position_cards / _market_rows), not the actual Kivy widgets still
        # sitting in list_box. _render_open()'s "did anything change since
        # last render" check reads list_box.children (the real widgets),
        # NOT the dict - so right after resume it still saw the SAME old
        # widgets as before (untouched) and concluded "nothing changed,
        # take the fast/incremental path" -> that fast path then looks up
        # each position in the now-EMPTY _position_cards dict, finds
        # nothing, and updates NOTHING. The header total/count (computed
        # from _position_cards) then reads 0 even though the backend still
        # has all your real open positions - purely a stale-cache-vs-real-
        # widget mismatch, not lost data and not a crash. Clearing the
        # widgets too (not just the dict) forces the real full rebuild path,
        # which repopulates both in sync.
        if hasattr(self.positions_screen, "_position_cards"):
            self.positions_screen._position_cards = {}
            self.positions_screen.list_box.clear_widgets()
        if hasattr(self.positions_screen, "_history_render_key"):
            self.positions_screen._history_render_key = None
        if hasattr(self.market_screen, "_market_rows"):
            self.market_screen._market_rows = {}
            self.market_screen.list_box.clear_widgets()
        if hasattr(self.signals_screen, "_render_key"):
            self.signals_screen._render_key = None
        # Don't wait up to 2s for the next scheduled tick - refresh right
        # away so the screen the user lands back on is current immediately.
        Clock.schedule_once(lambda dt: self.refresh_ui(0), 0)

    def on_stop(self):
        wl = getattr(self, "_wake_lock", None)
        if wl is not None:
            try:
                wl.release()
            except Exception:
                pass

    def _try_acquire_wake_lock(self):
        """Best-effort only: ask Android to keep the CPU awake so the bot's
        polling/monitor threads keep getting scheduled while the screen is
        off. Fully guarded - on desktop, or if anything about the Android
        API call fails, this silently does nothing and the app runs exactly
        as it did before. This does NOT prevent a swipe-away kill; only a
        real Foreground Service does that (not added here - see on_pause)."""
        try:
            from kivy.utils import platform
            if platform != "android":
                return
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            PowerManager = autoclass('android.os.PowerManager')
            Context = autoclass('android.content.Context')
            activity = PythonActivity.mActivity
            power_manager = activity.getSystemService(Context.POWER_SERVICE)
            self._wake_lock = power_manager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK, "TradeExitPanel:BotWakeLock")
            self._wake_lock.acquire()
            print("[WakeLock] Acquired - bot should keep polling while screen is off.")
        except Exception as e:
            # Never let this break app startup - worst case, no wake lock,
            # app behaves exactly as it did before this was added.
            print(f"[WakeLock] Could not acquire (non-fatal, app continues normally): {e}")

    def _rescale_padding(self, *args):
        """Re-apply top/bottom safe padding whenever the window is resized -
        covers floating window / split-screen resizing and rotation, so the
        layout keeps adapting instead of staying fixed to the launch size."""
        top = calc_top_pad()
        bottom = calc_bottom_pad()
        _apply_pad(getattr(self.dashboard_screen, 'root_layout', None), top=top)
        _apply_pad(getattr(self.positions_screen, 'root_box', None), top=top)
        _apply_pad(getattr(self.market_screen, 'root_layout', None), top=top)
        _apply_pad(getattr(self.settings_screen, 'root_layout', None), top=top)
        for scr in getattr(self, 'placeholder_screens', []):
            _apply_pad(getattr(scr, 'root_layout', None), top=top)
        _apply_pad(self.app_root, bottom=bottom)

    def _update_bg(self, instance, value):
        self._bg_rect.pos = instance.pos
        self._bg_rect.size = instance.size

    def _switch(self, screen_name):
        self.sm.current = screen_name

    def safe_start(self, dt):
        try:
            send_telegram("Trying to import bot modules...")
            from recovered_bot.app import main as bot_main
            from recovered_bot import status, trade_store, signal_log
            self.status_module = status
            self.trade_store_module = trade_store
            self.signal_log_module = signal_log
            send_telegram("Bot modules imported OK. Starting bot thread...")
            threading.Thread(target=self.run_bot, args=(bot_main,), daemon=True).start()
            self._try_acquire_wake_lock()
            # Was 2s. The backend (market_ws + paper_trader's 1s monitor
            # loop) already computes fresh LTP/PNL/ROI on its own, completely
            # independently of this UI tick - that part was already correct.
            # But the SCREEN only redrew every 2s, so even a perfectly live
            # backend looked laggy/"not really live" to the user. 0.5s keeps
            # the screen close to the backend's own ~1s cadence without
            # redrawing on every single raw WebSocket packet. update_data()
            # on PositionCard/MarketRow only updates existing widget labels
            # (no rebuild), so this is cheap even at this rate.
            Clock.schedule_interval(self.refresh_ui, 0.5)
            self.dashboard_screen.bot_status_card.set_value("Bot starting...", YELLOW)
        except Exception:
            tb = traceback.format_exc()
            self.dashboard_screen.bot_status_card.set_value("IMPORT FAILED", RED)
            self.dashboard_screen.error_label.text = tb
            send_telegram(f"IMPORT FAILED:\n{tb}")

    def run_bot(self, bot_main):
        try:
            bot_main()
        except Exception:
            tb = traceback.format_exc()
            send_telegram(f"BOT CRASHED:\n{tb}")
            Clock.schedule_once(lambda dt: self.show_crash(tb), 0)

    def show_crash(self, tb):
        self.dashboard_screen.bot_status_card.set_value("BOT CRASHED", RED)
        self.dashboard_screen.error_label.text = tb

    def refresh_ui(self, dt):
        if not self.status_module:
            return
        try:
            current = self.sm.current
            data = self.status_module.get()
            if current == "dashboard":
                self.dashboard_screen.refresh(data)
            elif current == "positions":
                history = self.trade_store_module.get_recent_closed(50) if self.trade_store_module else []
                self.positions_screen.refresh(data, history)
            elif current == "market":
                self.market_screen.refresh(data)
            elif current == "settings":
                self.settings_screen.refresh(data)
            elif current == "alerts":
                self.alerts_screen.refresh(data)
            elif current == "signals":
                sigs = self.signal_log_module.get_recent(100) if self.signal_log_module else []
                self.signals_screen.refresh(data, sigs)
        except Exception:
            self.dashboard_screen.error_label.text = traceback.format_exc()


if __name__ == "__main__":
    TradeExitPanelApp().run()
