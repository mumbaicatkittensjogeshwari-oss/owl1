import requests
import time
from datetime import datetime, timedelta
from . import status

IST_OFFSET = timedelta(hours=5, minutes=30)


class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.signal_count = 0
        print(f"[Telegram] Initialized with token: {bot_token[:10]}... and chat: {chat_id}")

    def _pct(self, price, entry):
        if entry == 0:
            return 0.0
        return abs(price - entry) / entry * 100

    def _format_signal(self, signal):
        is_long = signal.action.upper() == "BUY"
        direction = "LONG" if is_long else "SHORT"
        emoji = "\U0001F7E2" if is_long else "\U0001F534"

        try:
            utc_dt = datetime.strptime(signal.signal_time, "%Y-%m-%d %H:%M:%S")
            ist_dt = utc_dt + IST_OFFSET
            time_str = ist_dt.strftime("%d %b, %I:%M %p")
            date_str = ist_dt.strftime("%d %b %Y")
        except Exception:
            time_str = signal.signal_time
            date_str = ""

        sl_pct = self._pct(signal.stop_loss, signal.entry_price)
        tp1_pct = self._pct(signal.tp1, signal.entry_price)
        tp2_pct = self._pct(signal.tp2, signal.entry_price)
        tp3_pct = self._pct(signal.tp3, signal.entry_price)
        tp4_pct = self._pct(signal.tp4, signal.entry_price)

        chart_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{signal.symbol}.P"

        text = (
            f"{emoji} {direction} SIGNAL\n"
            f"Signal #{getattr(signal, 'signal_no', '?')} | {date_str}\n"
            f"--------------------------\n"
            f"Symbol    : {signal.symbol}\n"
            f"Direction : {direction}\n"
            f"Timeframe : 3m\n"
            f"--------------------------\n"
            f"Entry      : {signal.entry_price:.5f}\n"
            f"Stop Loss  : {signal.stop_loss:.5f}  ({sl_pct:.2f}%)\n"
            f"TP1        : {signal.tp1:.5f}  ({tp1_pct:.2f}%)\n"
            f"TP2        : {signal.tp2:.5f}  ({tp2_pct:.2f}%)\n"
            f"TP3        : {signal.tp3:.5f}  ({tp3_pct:.2f}%)\n"
            f"TP4        : {signal.tp4:.5f}  ({tp4_pct:.2f}%)\n"
            f"--------------------------\n"
            f"Time (IST): {time_str}\n"
            f"--------------------------\n"
            f"Chart: {chart_url}"
        )
        return text

    def send_signal(self, signal):
        text = self._format_signal(signal)
        print(f"[Telegram] Sending signal: {signal.symbol} - {signal.action} @ {signal.entry_price}")
        # Dashboard's TELEGRAM card reads telegram_last_attempt_ts/_ok/_error
        # from status - these were never being written here, so the card
        # stayed on "Not sent yet" forever even when sends were succeeding.
        status.update(telegram_last_attempt_ts=time.time())
        if not self.bot_token or not self.chat_id:
            status.update(telegram_last_ok=False, telegram_last_error="Bot Token / Chat ID not set in Settings")
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=10)
            if resp.status_code == 200:
                status.update(telegram_last_ok=True, telegram_last_error=None)
            else:
                print(f"[Telegram] Send failed: {resp.status_code} - {resp.text}")
                status.update(telegram_last_ok=False, telegram_last_error=f"HTTP {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            print(f"[Telegram] Send error: {e}")
            status.update(telegram_last_ok=False, telegram_last_error=str(e))
