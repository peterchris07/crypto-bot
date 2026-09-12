"""EMA crossover: EMA cepat di atas EMA lambat berarti LONG, selain itu FLAT.

EMA memakai definisi rekursif baku, alpha = 2 / (period + 1), dimulai dari close
pertama di jendela (pandas ewm dengan adjust=False). Karena EMA bergantung pada
seluruh histori, nilainya hanya identik antara backtest dan live kalau keduanya
menghitung dari jendela yang sama panjangnya. Jendela itu lookback_bars =
slow_period x lookback_multiplier. Bobot bar di luar jendela adalah
(1 - alpha) ^ lookback_bars, sekitar e^(-2 x multiplier), jadi multiplier 5
menyisakan bobot sekitar 5e-5, dan yang penting: sama persis di kedua sisi.

Periode dari config, tidak ada optimasi, tidak ada indikator lain. Sama tinggi
(termasuk saat harga datar) berarti FLAT, bukan LONG.
"""

from __future__ import annotations

import pandas as pd

from tradebot.data.ohlcv import validate_frame
from tradebot.strategy.base import Signal, Strategy


def ema(closes: pd.Series, period: int) -> pd.Series:
    """EMA rekursif: nilai pertama = close pertama, lalu alpha*close + (1-alpha)*ema_sebelumnya."""
    return closes.ewm(span=period, adjust=False).mean()


class EmaCross(Strategy):
    name = "ema_cross"

    def __init__(self, fast_period: int, slow_period: int, lookback_multiplier: int) -> None:
        if fast_period < 1 or slow_period <= fast_period:
            raise ValueError(
                f"butuh 1 <= fast_period < slow_period, dapat {fast_period} dan {slow_period}"
            )
        if lookback_multiplier < 1:
            raise ValueError(f"lookback_multiplier harus >= 1, dapat {lookback_multiplier}")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.lookback_multiplier = lookback_multiplier

    @property
    def lookback_bars(self) -> int:
        return self.slow_period * self.lookback_multiplier

    def signal(self, bars: pd.DataFrame) -> Signal:
        validate_frame(bars)
        if len(bars) < self.lookback_bars:
            return Signal.FLAT
        closes = bars["close"].iloc[-self.lookback_bars :].reset_index(drop=True)
        fast = ema(closes, self.fast_period).iloc[-1]
        slow = ema(closes, self.slow_period).iloc[-1]
        return Signal.LONG if fast > slow else Signal.FLAT

    def __repr__(self) -> str:
        return (
            f"EmaCross(fast={self.fast_period}, slow={self.slow_period}, "
            f"lookback_bars={self.lookback_bars})"
        )
