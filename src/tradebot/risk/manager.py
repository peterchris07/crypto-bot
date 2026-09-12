"""RiskManager: satu kelas yang dipakai backtest, paper, dan live.

Tahap 5 mengisi bagian yang dibutuhkan backtest dan interface-nya sudah final:
position sizing berbasis pecahan equity, batas maksimum satu posisi, cek
minimum notional exchange, dan stop loss serta take profit lapis 1 sebagai
pecahan dari harga masuk. Tahap 6 menambahkan batas rugi harian dan kill switch
di kelas yang sama, bukan di kelas lain, supaya backtest dan live tetap memakai
kode yang persis sama.

Sizing memperhitungkan biaya: budget = pecahan x equity, dan jumlah base yang
dibeli adalah budget / (harga isi x (1 + total fee)), supaya kas tidak pernah
negatif setelah fee. Kalau hasil sizing di bawah batas minimum exchange, bot
BERHENTI dengan pesan jelas (MinimumNotionalError), bukan diam-diam melewatkan
trade; trade yang hilang tanpa jejak membuat backtest dan live diam-diam berbeda.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from tradebot.config import CostConfig, RiskConfig
from tradebot.exchange.base import MarketLimits


class RiskError(Exception):
    """Aturan risk dilanggar; bot harus berhenti dan menyebut sebabnya."""


class MinimumNotionalError(RiskError):
    """Ukuran posisi hasil sizing di bawah batas minimum exchange."""


class ExitReason(StrEnum):
    SIGNAL = "signal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    END_OF_DATA = "end_of_data"


@dataclass(frozen=True)
class StopLevels:
    """Lapis 1: harga stop loss dan take profit untuk satu posisi long."""

    stop_loss: float
    take_profit: float


def quantize_down(value: float, step: float | None) -> float:
    if not step or step <= 0:
        return value
    units = math.floor(value / step + 1e-12)
    return round(units * step, 12)


class RiskManager:
    def __init__(self, config: RiskConfig, costs: CostConfig) -> None:
        self.config = config
        self.costs = costs

    # ------------------------------------------------------------------ #
    # Ukuran posisi
    # ------------------------------------------------------------------ #

    @property
    def position_fraction(self) -> float:
        return min(self.config.position_fraction, self.config.max_position_fraction)

    def size_position(
        self, equity: float, fill_price: float, limits: MarketLimits | None = None
    ) -> float:
        """Jumlah base yang dibeli untuk satu posisi baru, sudah dikurangi ruang untuk fee.

        fill_price adalah harga isi yang diharapkan (sudah termasuk slippage). limits, kalau
        ada, dipakai untuk membulatkan ke step exchange dan mengecek minimum; tanpa limits
        (backtest tanpa data pasar) tidak ada pembulatan dan tidak ada cek minimum.
        """
        if equity <= 0 or fill_price <= 0:
            raise RiskError(f"equity {equity} dan harga {fill_price} harus positif")
        budget = equity * self.position_fraction
        amount = budget / (fill_price * (1 + self.costs.total_fee_rate))
        if limits is None:
            return amount
        amount = quantize_down(amount, limits.amount_step)
        notional = amount * fill_price
        problems = []
        if amount <= 0:
            problems.append("jumlah menjadi 0 setelah pembulatan ke step")
        if limits.min_amount is not None and amount < limits.min_amount:
            problems.append(f"jumlah {amount} < min_amount {limits.min_amount} {limits.base}")
        if limits.min_cost is not None and notional < limits.min_cost:
            problems.append(f"nilai {notional:.4f} < min_cost {limits.min_cost} {limits.quote}")
        if problems:
            raise MinimumNotionalError(
                f"ukuran posisi di bawah batas minimum {limits.symbol}: {'; '.join(problems)}. "
                f"Equity {equity:.4f} x pecahan {self.position_fraction} = budget {budget:.4f} "
                f"pada harga {fill_price}. Naikkan equity atau risk.position_fraction; "
                "bot berhenti."
            )
        return amount

    # ------------------------------------------------------------------ #
    # Stop lapis 1
    # ------------------------------------------------------------------ #

    def stop_levels(self, entry_price: float) -> StopLevels:
        return StopLevels(
            stop_loss=entry_price * (1 - self.config.stop_loss_fraction),
            take_profit=entry_price * (1 + self.config.take_profit_fraction),
        )

    def exit_reason_for_bar(self, levels: StopLevels, high: float, low: float) -> ExitReason | None:
        """Backtest: tembus dideteksi dari high dan low bar. Kalau keduanya tembus dalam satu
        bar, urutannya tidak diketahui, jadi diambil yang pesimistis: stop loss."""
        if low <= levels.stop_loss:
            return ExitReason.STOP_LOSS
        if high >= levels.take_profit:
            return ExitReason.TAKE_PROFIT
        return None

    def exit_reason_for_price(self, levels: StopLevels, price: float) -> ExitReason | None:
        """Live dan paper: satu harga terakhir per iterasi loop."""
        if price <= levels.stop_loss:
            return ExitReason.STOP_LOSS
        if price >= levels.take_profit:
            return ExitReason.TAKE_PROFIT
        return None
