"""PaperAdapter: harga asli dari adapter publik venue live, eksekusi disimulasikan.

Dibutuhkan karena venue develop (Binance testnet) dan venue live (Tokocrypto)
berbeda, dan karena testnet tidak menunjukkan perilaku Tokocrypto. Semua data
pasar (jam server, batas pasar, OHLCV, ticker) diteruskan ke adapter publik
Tokocrypto. Akun disimulasikan: saldo, order, dan trade dicatat di memori dan
dipersist atomik ke live.paper_account_path, jadi paper yang berjalan berhari-hari
selamat dari restart.

Aturan eksekusi sama dengan backtest supaya keduanya bisa dibandingkan: market
buy terisi di ask x (1 + slippage), market sell di bid x (1 - slippage), fee
(taker + pajak + bursa) dipotong dari quote per sisi, dan respons order sudah
memuat fee sehingga ledger langsung reconciled. Hanya order MARKET; stop order
di exchange adalah urusan tahap 8 dan tidak disimulasikan di sini.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradebot.config import CostConfig
from tradebot.data.cache import atomic_write
from tradebot.exchange.base import (
    AssetBalance,
    Balance,
    ExchangeAdapter,
    MarketLimits,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Ticker,
    Trade,
)
from tradebot.exchange.errors import (
    FatalExchangeError,
    InsufficientFundsError,
    InvalidOrderError,
    OrderNotFoundError,
)

log = logging.getLogger(__name__)


class PaperAdapter(ExchangeAdapter):
    def __init__(
        self,
        public: ExchangeAdapter,
        costs: CostConfig,
        *,
        symbol: str,
        account_path: str | Path,
        initial_quote: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if initial_quote <= 0:
            raise FatalExchangeError("saldo awal paper harus > 0")
        self.public = public
        self.costs = costs
        self.symbol = symbol
        self.base, self.quote = symbol.split("/", 1)
        self.account_path = Path(account_path)
        self.initial_quote = float(initial_quote)
        self._clock = clock
        self.name = f"paper({public.name})"
        self._account: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Akun
    # ------------------------------------------------------------------ #

    @property
    def account(self) -> dict[str, Any]:
        if self._account is None:
            raise FatalExchangeError(f"{self.name}: panggil connect() dulu")
        return self._account

    def _load_account(self) -> dict[str, Any]:
        if self.account_path.exists():
            try:
                data = json.loads(self.account_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise FatalExchangeError(
                    f"akun paper {self.account_path} rusak: {exc}. Hapus file itu hanya kalau "
                    "Anda memang ingin memulai paper dari saldo awal lagi."
                ) from exc
            if data.get("symbol") != self.symbol:
                raise FatalExchangeError(
                    f"akun paper {self.account_path} untuk {data.get('symbol')!r}, config minta "
                    f"{self.symbol!r}; pakai file akun lain atau hapus yang lama secara sadar"
                )
            log.info(
                "akun paper dimuat dari %s: %s",
                self.account_path,
                {k: v for k, v in data["balances"].items()},
            )
            return data
        data = {
            "symbol": self.symbol,
            "balances": {self.quote: self.initial_quote, self.base: 0.0},
            "orders": [],
            "trades": [],
            "next_id": 1,
            "created_at": self._now().isoformat(),
        }
        log.info("akun paper baru: %s %s", self.initial_quote, self.quote)
        return data

    def _save_account(self) -> None:
        text = json.dumps(self.account, indent=2, ensure_ascii=False) + "\n"
        atomic_write(self.account_path, lambda tmp: tmp.write_text(text, encoding="utf-8"))

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=UTC)

    # ------------------------------------------------------------------ #
    # Data pasar: diteruskan
    # ------------------------------------------------------------------ #

    @property
    def can_trade(self) -> bool:
        return True

    @property
    def server_offset_ms(self) -> float:
        return float(getattr(self.public, "server_offset_ms", 0.0))

    def connect(self) -> None:
        self.public.connect()
        self._account = self._load_account()
        self._save_account()

    def fetch_server_time_ms(self) -> int:
        return self.public.fetch_server_time_ms()

    def fetch_market_limits(self, symbol: str) -> MarketLimits:
        return self.public.fetch_market_limits(symbol)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        since_ms: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        return self.public.fetch_ohlcv(symbol, timeframe, since_ms=since_ms, limit=limit)

    def fetch_ticker(self, symbol: str) -> Ticker:
        return self.public.fetch_ticker(symbol)

    # ------------------------------------------------------------------ #
    # Akun: disimulasikan
    # ------------------------------------------------------------------ #

    def fetch_balance(self) -> Balance:
        balances = self.account["balances"]
        return Balance(
            {asset: AssetBalance(free=amt, used=0.0, total=amt) for asset, amt in balances.items()}
        )

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
        if symbol != self.symbol:
            raise InvalidOrderError(f"akun paper hanya untuk {self.symbol}, dapat {symbol}")
        if order_type is not OrderType.MARKET:
            raise InvalidOrderError(
                f"paper hanya mensimulasikan order MARKET; {order_type.value} tidak didukung "
                "(stop di exchange adalah urusan tahap 8)"
            )
        if amount <= 0:
            raise InvalidOrderError(f"amount harus > 0, dapat {amount}")
        ticker = self.public.fetch_ticker(symbol)
        balances = self.account["balances"]
        now = self._now()
        log.info(
            "PAPER ORDER INTENT %s %s %s client_order_id=%s last=%s",
            side.value,
            amount,
            symbol,
            client_order_id,
            ticker.last,
        )
        if side is OrderSide.BUY:
            reference = ticker.ask or ticker.last
            fill = reference * (1 + self.costs.slippage_rate)
            cost = amount * fill
            fee = cost * self.costs.total_fee_rate
            if balances[self.quote] < cost + fee:
                raise InsufficientFundsError(
                    f"paper: butuh {cost + fee:.4f} {self.quote}, saldo {balances[self.quote]:.4f}"
                )
            balances[self.quote] -= cost + fee
            balances[self.base] += amount
        else:
            reference = ticker.bid or ticker.last
            fill = reference * (1 - self.costs.slippage_rate)
            if balances[self.base] < amount:
                raise InsufficientFundsError(
                    f"paper: jual {amount} {self.base}, saldo {balances[self.base]}"
                )
            cost = amount * fill
            fee = cost * self.costs.total_fee_rate
            balances[self.base] -= amount
            balances[self.quote] += cost - fee
        order_id = f"paper-{self.account['next_id']}"
        self.account["next_id"] += 1
        record = {
            "id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side.value,
            "type": order_type.value,
            "amount": amount,
            "filled": amount,
            "average": fill,
            "cost": cost,
            "fee": fee,
            "fee_currency": self.quote,
            "timestamp": now.isoformat(),
            "reference_price": reference,
        }
        self.account["orders"].append(record)
        self.account["trades"].append({**record, "order_id": order_id, "id": f"t-{order_id}"})
        self._save_account()
        log.info(
            "PAPER ORDER RESULT id=%s %s %s @ %.4f cost=%.4f fee=%.4f saldo=%s",
            order_id,
            side.value,
            amount,
            fill,
            cost,
            fee,
            balances,
        )
        return self._order_from(record)

    def _order_from(self, record: dict[str, Any]) -> Order:
        return Order(
            id=record["id"],
            client_order_id=record.get("client_order_id"),
            symbol=record["symbol"],
            side=OrderSide(record["side"]),
            type=OrderType(record["type"]),
            amount=float(record["amount"]),
            price=None,
            stop_price=None,
            status=OrderStatus.CLOSED,
            filled=float(record["filled"]),
            average=float(record["average"]),
            cost=float(record["cost"]),
            fee=float(record["fee"]),
            fee_currency=record["fee_currency"],
            timestamp=datetime.fromisoformat(record["timestamp"]),
        )

    def cancel_order(self, order_id: str, symbol: str) -> Order:
        raise OrderNotFoundError(
            f"paper: tidak ada order terbuka, order {order_id} terisi seketika"
        )

    def cancel_all_orders(self, symbol: str) -> list[Order]:
        return []

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        return []

    def fetch_order(
        self,
        symbol: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        for record in self.account["orders"]:
            if order_id and record["id"] == order_id:
                return self._order_from(record)
            if client_order_id and record.get("client_order_id") == client_order_id:
                return self._order_from(record)
        raise OrderNotFoundError(
            f"paper: order {order_id or client_order_id} tidak ditemukan di {symbol}"
        )

    def fetch_my_trades(
        self, symbol: str, *, since_ms: int | None = None, limit: int | None = None
    ) -> list[Trade]:
        trades: list[Trade] = []
        for record in self.account["trades"]:
            stamp = datetime.fromisoformat(record["timestamp"])
            if since_ms is not None and stamp.timestamp() * 1000 < since_ms:
                continue
            trades.append(
                Trade(
                    id=record["id"],
                    order_id=record["order_id"],
                    symbol=record["symbol"],
                    side=OrderSide(record["side"]),
                    amount=float(record["filled"]),
                    price=float(record["average"]),
                    cost=float(record["cost"]),
                    fee=float(record["fee"]),
                    fee_currency=record["fee_currency"],
                    timestamp=stamp,
                )
            )
        return trades[-limit:] if limit else trades
