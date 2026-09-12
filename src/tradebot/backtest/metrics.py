"""Metrik dari kurva equity dan daftar trade.

Sharpe dianualisasi dari return per bar dengan backtest.bars_per_year, karena
frekuensi keputusan strategi memang per bar. Basis itu selalu disebut di laporan.
Catatan SPEC untuk holding period panjang tetap berlaku: return antar bar dalam
satu posisi saling bergantung, jadi angka ini indikatif, bukan uji statistik.
Kalau deviasi standar nol atau data kurang dari dua return, Sharpe adalah None
dan ditampilkan sebagai n/a, bukan 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from tradebot.backtest.engine import Trade


@dataclass(frozen=True)
class Metrics:
    total_return: float
    max_drawdown: float
    sharpe: float | None
    sharpe_basis_bars_per_year: int
    win_rate: float | None
    profit_factor: float | None
    trade_count: int
    avg_bars_held: float | None


def max_drawdown(equity: pd.Series) -> float:
    """Penurunan terdalam dari puncak sebelumnya, sebagai pecahan positif dari puncak itu."""
    if len(equity) == 0:
        return 0.0
    peaks = equity.cummax()
    drawdowns = (peaks - equity) / peaks
    return float(drawdowns.max())


def sharpe_ratio(equity: pd.Series, bars_per_year: int) -> float | None:
    returns = equity.pct_change().dropna()
    if len(returns) < 2:
        return None
    std = float(returns.std(ddof=1))
    if std == 0 or math.isnan(std):
        return None
    return float(returns.mean() / std * math.sqrt(bars_per_year))


def compute_metrics(equity: pd.Series, trades: Sequence[Trade], bars_per_year: int) -> Metrics:
    if len(equity) == 0:
        raise ValueError("kurva equity kosong")
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    if trades:
        win_rate = len(wins) / len(trades)
        avg_bars = sum(t.bars_held for t in trades) / len(trades)
    else:
        win_rate = None
        avg_bars = None
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return Metrics(
        total_return=total_return,
        max_drawdown=max_drawdown(equity),
        sharpe=sharpe_ratio(equity, bars_per_year),
        sharpe_basis_bars_per_year=bars_per_year,
        win_rate=win_rate,
        profit_factor=profit_factor,
        trade_count=len(trades),
        avg_bars_held=avg_bars,
    )
