from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict

from .utils import setup_logger

logger = setup_logger("StrategyEngine")


@dataclass
class Signal:
    symbol: str
    action: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    tp4: float
    signal_time: str
    current_price: float
    candle_index: int = 0


class ABCDStrategy:

    def __init__(
        self,
        lookback_len: int = 200,
        ema_length: int = 50
    ):
        self.lookback_len = lookback_len
        self.ema_length = ema_length

    def _compute_ema(self, closes: List[float]) -> List[float]:
        alpha = 2 / (self.ema_length + 1)
        ema_values = [closes[0]]
        for price in closes[1:]:
            ema_values.append(alpha * price + (1 - alpha) * ema_values[-1])
        return ema_values

    def _rolling_mean_std(self, closes: List[float]) -> Tuple[float, float]:
        window = closes[-self.lookback_len:]
        n = len(window)
        mean = sum(window) / n
        variance = sum((x - mean) ** 2 for x in window) / n
        std = variance ** 0.5
        return mean, std

    def calculate_tp_levels(
        self,
        entry_price: float,
        is_long: bool,
        levels: Dict[str, float]
    ) -> Tuple[float, float, float, float]:

        onesd = levels["onesd"]
        twosd = levels["twosd"]
        threesd = levels["threesd"]
        neg_onesd = levels["neg_onesd"]
        neg_twosd = levels["neg_twosd"]
        neg_threesd = levels["neg_threesd"]
        zero_price = levels["zero_price"]

        if is_long:
            if entry_price < neg_threesd:
                return (neg_threesd, neg_twosd, neg_onesd, zero_price)
            elif entry_price < neg_twosd:
                return (neg_twosd, neg_onesd, zero_price, onesd)
            elif entry_price < neg_onesd:
                return (neg_onesd, zero_price, onesd, twosd)
            elif entry_price < zero_price:
                return (zero_price, onesd, twosd, threesd)
            elif entry_price < onesd:
                return (onesd, twosd, threesd, threesd)
            elif entry_price < twosd:
                return (twosd, threesd, threesd, threesd)
            else:
                return (threesd, threesd, threesd, threesd)
        else:
            if entry_price > threesd:
                return (threesd, twosd, onesd, zero_price)
            elif entry_price > twosd:
                return (twosd, onesd, zero_price, neg_onesd)
            elif entry_price > onesd:
                return (onesd, zero_price, neg_onesd, neg_twosd)
            elif entry_price > zero_price:
                return (zero_price, neg_onesd, neg_twosd, neg_threesd)
            elif entry_price > neg_onesd:
                return (neg_onesd, neg_twosd, neg_threesd, neg_threesd)
            elif entry_price > neg_twosd:
                return (neg_twosd, neg_threesd, neg_threesd, neg_threesd)
            else:
                return (neg_twosd, neg_threesd, neg_threesd, neg_threesd)

    def generate_signal(
        self,
        candles: List[Dict],
        symbol: str
    ) -> Optional[Signal]:

        if len(candles) < self.lookback_len + 1:
            return None

        closes = [float(c["close"]) for c in candles]
        ema_values = self._compute_ema(closes)

        last_close = closes[-1]
        prev_close = closes[-2]
        last_ema = ema_values[-1]
        prev_ema = ema_values[-2]

        cross_up = (prev_close <= prev_ema) and (last_close > last_ema)
        cross_down = (prev_close >= prev_ema) and (last_close < last_ema)

        if not cross_up and not cross_down:
            return None

        mean, std = self._rolling_mean_std(closes)
        levels = {
            "zero_price": mean,
            "onesd": mean + std,
            "twosd": mean + 2 * std,
            "threesd": mean + 3 * std,
            "neg_onesd": mean - std,
            "neg_twosd": mean - 2 * std,
            "neg_threesd": mean - 3 * std,
        }

        last_candle = candles[-1]
        ts = last_candle.get("timestamp")
        if ts is not None:
            signal_time_str = datetime.fromtimestamp(
                ts / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        else:
            signal_time_str = ""

        if cross_up:
            entry_price = last_close
            stop_loss = float(last_candle["low"])
            tp1, tp2, tp3, tp4 = self.calculate_tp_levels(
                entry_price, True, levels
            )
            return Signal(
                symbol=symbol,
                action="BUY",
                entry_price=entry_price,
                stop_loss=stop_loss,
                tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
                signal_time=signal_time_str,
                current_price=entry_price,
                candle_index=len(candles) - 1
            )

        entry_price = last_close
        stop_loss = float(last_candle["high"])
        tp1, tp2, tp3, tp4 = self.calculate_tp_levels(
            entry_price, False, levels
        )
        return Signal(
            symbol=symbol,
            action="SELL",
            entry_price=entry_price,
            stop_loss=stop_loss,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4,
            signal_time=signal_time_str,
            current_price=entry_price,
            candle_index=len(candles) - 1
        )
