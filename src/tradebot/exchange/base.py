"""Interface ExchangeAdapter dan tipe data yang dipertukarkan.

Semua kode lain (strategy, risk, backtest, runner) hanya memakai tipe di file
ini. Tidak ada dict mentah ccxt yang bocor keluar dari lapisan exchange.

Urutan pemakaian: buat adapter, panggil connect() sekali (cek jam, muat
pasar), baru method lain. Method yang butuh kunci gagal lokal tanpa
menyentuh jaringan kalau adapter dibuat tanpa kunci.

Stop loss dua lapis (lihat SPEC.md, Keputusan Desain):
  lapis 1 sisi bot     runner memantau harga, kirim create_order MARKET
  lapis 2 sisi exchange create_order dengan OrderType.STOP_LOSS_LIMIT dan
                        stop_price, dipasang setelah posisi terbentuk. Stop
                        ini mengunci aset, jadi cancel_order pada stop itu
                        WAJIB mendahului order keluar apa pun (tahap 8).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pandas as pd


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS_LIMIT = "stop_loss_limit"


class OrderStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarketLimits:
    """Batas yang dipaksakan exchange. None berarti exchange tidak melaporkannya."""

    symbol: str
    base: str
    quote: str
    min_amount: float | None
    amount_step: float | None
    min_cost: float | None
    price_step: float | None
    # Batas bawah band harga untuk bid (PERCENT_PRICE_BY_SIDE.bidMultiplierDown); None = tanpa band.
    price_band_down: float | None = None


@dataclass(frozen=True)
class Ticker:
    symbol: str
    last: float
    bid: float | None
    ask: float | None
    timestamp: datetime


@dataclass(frozen=True)
class AssetBalance:
    free: float
    used: float
    total: float


@dataclass(frozen=True)
class Balance:
    assets: Mapping[str, AssetBalance]

    def free(self, asset: str) -> float:
        return self.assets[asset].free if asset in self.assets else 0.0

    def used(self, asset: str) -> float:
        return self.assets[asset].used if asset in self.assets else 0.0

    def total(self, asset: str) -> float:
        return self.assets[asset].total if asset in self.assets else 0.0


@dataclass(frozen=True)
class Order:
    id: str | None
    client_order_id: str | None
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float | None
    stop_price: float | None
    status: OrderStatus
    filled: float
    average: float | None
    cost: float | None
    fee: float | None
    fee_currency: str | None
    timestamp: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status is OrderStatus.OPEN

    @property
    def remaining(self) -> float:
        return max(self.amount - self.filled, 0.0)


@dataclass(frozen=True)
class Trade:
    """Satu eksekusi (fill). Sumber fee yang sebenarnya; respons order tidak selalu memuatnya."""

    id: str | None
    order_id: str | None
    symbol: str
    side: OrderSide
    amount: float
    price: float
    cost: float
    fee: float | None
    fee_currency: str | None
    timestamp: datetime | None


class ExchangeAdapter(ABC):
    """Kontrak yang dipenuhi CcxtAdapter (testnet/mainnet) dan PaperAdapter."""

    name: str

    @property
    @abstractmethod
    def can_trade(self) -> bool:
        """True kalau adapter punya kunci dan boleh mengirim order."""

    @property
    def supports_exchange_stops(self) -> bool:
        """True kalau stop order di sisi exchange (lapis 2) bisa dipasang lewat adapter ini."""
        return False

    @abstractmethod
    def connect(self) -> None:
        """Cek jam server dan muat daftar pasar. Wajib sebelum method lain."""

    @abstractmethod
    def fetch_server_time_ms(self) -> int:
        """Waktu server dalam milidetik epoch."""

    @abstractmethod
    def fetch_market_limits(self, symbol: str) -> MarketLimits:
        """Batas minimum dan step size untuk satu pasangan."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        since_ms: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """DataFrame dengan kolom timestamp (UTC), open, high, low, close, volume."""

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    def fetch_balance(self) -> Balance: ...

    @abstractmethod
    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        *,
        price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        """Kirim satu order. Tidak pernah dicoba ulang otomatis: lihat OrderStateUnknownError."""

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> Order: ...

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> list[Order]:
        """Batalkan semua order terbuka pada satu pasangan. Aman dipanggil saat tidak ada order."""

    @abstractmethod
    def fetch_open_orders(self, symbol: str) -> list[Order]: ...

    @abstractmethod
    def fetch_order(
        self,
        symbol: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        """Cari satu order berdasarkan id exchange atau client_order_id. Dipakai rekonsiliasi."""

    @abstractmethod
    def fetch_my_trades(
        self, symbol: str, *, since_ms: int | None = None, limit: int | None = None
    ) -> list[Trade]:
        """Eksekusi akun sendiri, untuk mengisi fee di ledger setelah order terisi."""
