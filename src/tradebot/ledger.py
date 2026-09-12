"""Ledger trade untuk rekonsiliasi dan pajak. Append-only, dua fase.

Fase 1: begitu order terisi, satu baris ditulis segera. Kalau respons order tidak
membawa fee (Tokocrypto tidak pernah membawanya), fee kosong dan fee_status pending.
Fase 2: reconcile() mengambil eksekusi akun lewat fetch_my_trades, menjumlahkan fee
per order, dan MENAMBAHKAN baris baru berstatus reconciled. Baris lama tidak pernah
ditimpa; keadaan terkini sebuah order adalah baris terakhirnya.

Ledger tidak pernah dinyatakan lengkap selama ada order yang baris terakhirnya pending.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from tradebot.exchange.base import Order, Trade

log = logging.getLogger(__name__)

LEDGER_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "pair",
    "side",
    "amount",
    "price",
    "quote_value",
    "fee",
    "fee_currency",
    "order_id",
    "client_order_id",
    "fee_status",
    "recorded_at",
)
PENDING = "pending"
RECONCILED = "reconciled"
# Jendela pencarian trades dimulai sedikit sebelum waktu fill, untuk jam yang tidak persis sama.
RECONCILE_LOOKBACK = timedelta(minutes=5)


class TradeSource(Protocol):
    def fetch_my_trades(
        self, symbol: str, *, since_ms: int | None = None, limit: int | None = None
    ) -> list[Trade]: ...


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ #
    # Tulis
    # ------------------------------------------------------------------ #

    def _append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LEDGER_COLUMNS))
            if is_new:
                writer.writeheader()
            writer.writerow({column: _fmt(row.get(column)) for column in LEDGER_COLUMNS})
            handle.flush()

    def record_fill(self, order: Order, *, recorded_at: datetime | None = None) -> None:
        """Fase 1. Dipanggil segera setelah order terisi (penuh atau sebagian)."""
        if order.filled <= 0:
            raise ValueError(f"order {order.id} belum terisi, tidak ada yang dicatat")
        price = order.average if order.average is not None else order.price
        quote_value = order.cost if order.cost is not None else (order.filled * (price or 0.0))
        status = RECONCILED if order.fee is not None else PENDING
        self._append(
            {
                "timestamp": order.timestamp or datetime.now(tz=UTC),
                "pair": order.symbol,
                "side": order.side.value,
                "amount": order.filled,
                "price": price,
                "quote_value": quote_value,
                "fee": order.fee,
                "fee_currency": order.fee_currency,
                "order_id": order.id,
                "client_order_id": order.client_order_id,
                "fee_status": status,
                "recorded_at": recorded_at or datetime.now(tz=UTC),
            }
        )
        log.info(
            "LEDGER fill order_id=%s %s %s %s @ %s fee=%s (%s)",
            order.id,
            order.side.value,
            order.filled,
            order.symbol,
            price,
            order.fee,
            status,
        )

    # ------------------------------------------------------------------ #
    # Baca
    # ------------------------------------------------------------------ #

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def _latest_by_order(self) -> dict[str, dict[str, str]]:
        latest: dict[str, dict[str, str]] = {}
        for row in self.rows():
            latest[row["order_id"]] = row
        return latest

    def pending_rows(self) -> list[dict[str, str]]:
        return [row for row in self._latest_by_order().values() if row["fee_status"] == PENDING]

    def pending_order_ids(self) -> list[str]:
        return [row["order_id"] for row in self.pending_rows()]

    def is_complete(self) -> bool:
        return not self.pending_rows()

    # ------------------------------------------------------------------ #
    # Fase 2: rekonsiliasi fee
    # ------------------------------------------------------------------ #

    def reconcile(self, source: TradeSource, symbol: str, *, now: datetime | None = None) -> int:
        """Isi fee untuk order pending lewat eksekusi akun. Mengembalikan jumlah yang selesai."""
        pending = [row for row in self.pending_rows() if row["pair"] == symbol]
        if not pending:
            return 0
        earliest = min(datetime.fromisoformat(row["timestamp"]) for row in pending)
        since_ms = int((earliest - RECONCILE_LOOKBACK).timestamp() * 1000)
        trades = source.fetch_my_trades(symbol, since_ms=since_ms)
        by_order: dict[str, list[Trade]] = {}
        for trade in trades:
            if trade.order_id is not None:
                by_order.setdefault(trade.order_id, []).append(trade)

        done = 0
        for row in pending:
            fills = by_order.get(row["order_id"], [])
            fee = _sum_fee(fills)
            if fee is None:
                log.warning(
                    "LEDGER order_id=%s masih pending: %d trade ditemukan, fee belum pasti",
                    row["order_id"],
                    len(fills),
                )
                continue
            total_fee, currency = fee
            amount = sum(t.amount for t in fills)
            cost = sum(t.cost for t in fills)
            self._append(
                {
                    **row,
                    "amount": amount if amount > 0 else row["amount"],
                    "price": (cost / amount) if amount > 0 else row["price"],
                    "quote_value": cost if cost > 0 else row["quote_value"],
                    "fee": total_fee,
                    "fee_currency": currency,
                    "fee_status": RECONCILED,
                    "recorded_at": now or datetime.now(tz=UTC),
                }
            )
            done += 1
            log.info(
                "LEDGER reconciled order_id=%s fee=%s %s", row["order_id"], total_fee, currency
            )
        return done


def _sum_fee(fills: Iterable[Trade]) -> tuple[float, str] | None:
    fills = list(fills)
    if not fills or any(t.fee is None for t in fills):
        return None
    currencies = {t.fee_currency for t in fills}
    if len(currencies) != 1 or None in currencies:
        # Fee dalam lebih dari satu mata uang tidak bisa dijumlahkan jadi satu angka.
        return None
    return sum(float(t.fee) for t in fills), str(currencies.pop())
