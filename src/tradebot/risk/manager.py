"""RiskManager: satu kelas yang dipakai backtest, paper, dan live.

Tahap 5: position sizing berbasis pecahan equity, batas maksimum satu posisi,
cek minimum notional exchange, dan stop loss serta take profit lapis 1 sebagai
pecahan dari harga masuk.

Tahap 6: semua kill switch Aturan Keras 4, di kelas yang sama supaya backtest
dan live memakai kode yang persis sama. Setiap pemicu melempar
KillSwitchTriggered yang membawa keputusan flatten dari config risk.flatten_on:

  daily_loss           rugi hari ini (UTC, termasuk unrealized) melewati batas;
                       equity awal hari dipersist supaya selamat dari restart
  runaway_orders       order dalam satu menit melewati batas: bug loop, jangan
                       tambah order, batalkan semua, berhenti dengan exit bukan nol
  connection_failures  gagal koneksi beruntun mencapai batas: tidak bisa flatten,
                       ditutup stop lapis 2 di exchange (tahap 8)
  stop_file            file STOP di root: pengguna yang intervensi, pengguna yang
                       memutuskan

Pemeriksaan menerima waktu sebagai argumen, bukan membaca jam sendiri, supaya
backtest dan test deterministik. Cek sekali per iterasi: check_stop_file dan
check_daily_loss; per kejadian: before_order, record_connection_failure,
record_connection_success.

Sizing memperhitungkan biaya: budget = pecahan x equity, dan jumlah base yang
dibeli adalah budget / (harga isi x (1 + total fee)), supaya kas tidak pernah
negatif setelah fee. Kalau hasil sizing di bawah batas minimum exchange, bot
BERHENTI dengan pesan jelas (MinimumNotionalError), bukan diam-diam melewatkan
trade; trade yang hilang tanpa jejak membuat backtest dan live diam-diam berbeda.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from tradebot.config import CostConfig, RiskConfig
from tradebot.exchange.base import MarketLimits
from tradebot.risk.state import DailyState, DailyStateStore, utc_day

log = logging.getLogger(__name__)
ORDER_WINDOW = timedelta(minutes=1)


class RiskError(Exception):
    """Aturan risk dilanggar; bot harus berhenti dan menyebut sebabnya."""


class MinimumNotionalError(RiskError):
    """Ukuran posisi hasil sizing di bawah batas minimum exchange."""


class KillSwitch(StrEnum):
    DAILY_LOSS = "daily_loss"
    RUNAWAY_ORDERS = "runaway_orders"
    CONNECTION_FAILURES = "connection_failures"
    STOP_FILE = "stop_file"


class KillSwitchTriggered(RiskError):
    """Bot harus berhenti total. flatten menyatakan apakah posisi ikut dijual dulu."""

    def __init__(self, switch: KillSwitch, flatten: bool, message: str) -> None:
        super().__init__(f"KILL SWITCH {switch.value}: {message} (flatten={flatten})")
        self.switch = switch
        self.flatten = flatten


class ExitReason(StrEnum):
    SIGNAL = "signal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    KILL_SWITCH = "kill_switch"
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
    def __init__(
        self,
        config: RiskConfig,
        costs: CostConfig,
        *,
        stop_file: Path | None = None,
        state_store: DailyStateStore | None = None,
    ) -> None:
        self.config = config
        self.costs = costs
        self.stop_file = stop_file
        self.state_store = state_store
        self.daily: DailyState | None = state_store.load() if state_store else None
        self._order_times: deque[datetime] = deque()
        self.consecutive_failures = 0
        if self.daily is not None:
            log.info(
                "state harian dimuat: hari %s equity awal %.4f",
                self.daily.day,
                self.daily.start_equity,
            )

    def _flatten(self, switch: KillSwitch) -> bool:
        return bool(getattr(self.config.flatten_on, switch.value))

    # ------------------------------------------------------------------ #
    # Kill switch: sekali per iterasi
    # ------------------------------------------------------------------ #

    def check_stop_file(self) -> None:
        if self.stop_file is not None and self.stop_file.exists():
            raise KillSwitchTriggered(
                KillSwitch.STOP_FILE,
                self._flatten(KillSwitch.STOP_FILE),
                f"file {self.stop_file} ada; hapus file itu untuk mengizinkan bot jalan lagi",
            )

    def start_of_day_equity(self, equity: float, now: datetime) -> float:
        """Equity awal hari UTC. Hari baru: dicatat dari equity sekarang dan dipersist."""
        today = utc_day(now).isoformat()
        if self.daily is None or self.daily.day != today:
            self.daily = DailyState.for_day(now, equity)
            if self.state_store is not None:
                self.state_store.save(self.daily)
            log.info("hari UTC %s dimulai dengan equity %.4f", today, equity)
        return self.daily.start_equity

    def daily_loss_fraction(self, equity: float, now: datetime) -> float:
        start = self.start_of_day_equity(equity, now)
        return 1 - equity / start if start > 0 else 0.0

    def check_daily_loss(self, equity: float, now: datetime) -> None:
        """equity harus mark-to-market (termasuk unrealized). Melewati batas = berhenti."""
        loss = self.daily_loss_fraction(equity, now)
        limit = self.config.daily_loss_limit_fraction
        # Toleransi pembulatan float: rugi tepat di batas belum "melewati" batas.
        if loss > limit + 1e-9:
            assert self.daily is not None
            raise KillSwitchTriggered(
                KillSwitch.DAILY_LOSS,
                self._flatten(KillSwitch.DAILY_LOSS),
                f"rugi hari {self.daily.day} {loss:.2%} melewati batas {limit:.2%} "
                f"(equity awal hari {self.daily.start_equity:.4f}, sekarang {equity:.4f})",
            )

    # ------------------------------------------------------------------ #
    # Kill switch: per kejadian
    # ------------------------------------------------------------------ #

    def before_order(self, now: datetime) -> None:
        """Panggil SEBELUM setiap order dikirim. Order ke-(batas+1) dalam satu menit berarti
        loop lepas kendali: jangan kirim, batalkan semua, berhenti."""
        while self._order_times and now - self._order_times[0] >= ORDER_WINDOW:
            self._order_times.popleft()
        if len(self._order_times) >= self.config.max_orders_per_minute:
            raise KillSwitchTriggered(
                KillSwitch.RUNAWAY_ORDERS,
                self._flatten(KillSwitch.RUNAWAY_ORDERS),
                f"sudah {len(self._order_times)} order dalam satu menit terakhir, batas "
                f"{self.config.max_orders_per_minute}; order berikutnya tidak dikirim",
            )
        self._order_times.append(now)

    def record_connection_failure(self, error: str = "") -> None:
        self.consecutive_failures += 1
        limit = self.config.max_consecutive_failures
        log.warning("gagal koneksi beruntun %d/%d: %s", self.consecutive_failures, limit, error)
        if self.consecutive_failures >= limit:
            raise KillSwitchTriggered(
                KillSwitch.CONNECTION_FAILURES,
                self._flatten(KillSwitch.CONNECTION_FAILURES),
                f"{self.consecutive_failures} kegagalan koneksi beruntun mencapai batas {limit}; "
                f"terakhir: {error or 'tidak ada detail'}",
            )

    def record_connection_success(self) -> None:
        self.consecutive_failures = 0

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
