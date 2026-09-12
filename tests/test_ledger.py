"""Ledger pajak dua fase: baris pending saat terisi, direkonsiliasi lewat fetch_my_trades."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tradebot.exchange import Order, OrderSide, OrderStatus, OrderType, Trade
from tradebot.ledger import LEDGER_COLUMNS, Ledger

T0 = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)


def filled(order_id: str, fee: float | None) -> Order:
    return Order(
        id=order_id,
        client_order_id=f"c-{order_id}",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.01,
        price=None,
        stop_price=None,
        status=OrderStatus.CLOSED,
        filled=0.01,
        average=50_000.0,
        cost=500.0,
        fee=fee,
        fee_currency="USDT" if fee is not None else None,
        timestamp=T0,
    )


class StubAdapter:
    def __init__(self, trades):
        self.trades = trades
        self.calls = []

    def fetch_my_trades(self, symbol, *, since_ms=None, limit=None):
        self.calls.append((symbol, since_ms, limit))
        return self.trades


def trade(order_id: str, fee: float, currency: str = "USDT") -> Trade:
    return Trade(
        id=f"t-{order_id}",
        order_id=order_id,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        amount=0.01,
        price=50_000.0,
        cost=500.0,
        fee=fee,
        fee_currency=currency,
        timestamp=T0,
    )


def test_fill_without_fee_is_written_immediately_as_pending(tmp_path: Path):
    ledger = Ledger(tmp_path / "trades" / "trades.csv")
    ledger.record_fill(filled("1", None))
    rows = ledger.rows()
    assert len(rows) == 1
    assert rows[0]["fee_status"] == "pending"
    assert rows[0]["fee"] == ""
    assert rows[0]["quote_value"] == "500.0"
    assert list(rows[0].keys()) == list(LEDGER_COLUMNS)
    assert ledger.pending_order_ids() == ["1"]
    assert ledger.is_complete() is False


def test_fill_with_fee_is_reconciled_at_once(tmp_path: Path):
    ledger = Ledger(tmp_path / "trades.csv")
    ledger.record_fill(filled("2", 0.75))
    assert ledger.pending_order_ids() == []
    assert ledger.is_complete() is True


def test_reconcile_appends_row_and_never_rewrites_history(tmp_path: Path):
    path = tmp_path / "trades.csv"
    ledger = Ledger(path)
    ledger.record_fill(filled("1", None))
    before = path.read_text().splitlines()

    adapter = StubAdapter([trade("1", 0.75)])
    done = ledger.reconcile(adapter, "BTC/USDT")
    after = path.read_text().splitlines()

    assert done == 1
    assert after[: len(before)] == before, "baris lama tidak pernah ditimpa"
    assert len(after) == len(before) + 1
    latest = ledger.rows()[-1]
    assert latest["fee_status"] == "reconciled"
    assert latest["fee"] == "0.75"
    assert latest["fee_currency"] == "USDT"
    assert ledger.pending_order_ids() == []
    assert ledger.is_complete() is True
    assert adapter.calls[0][0] == "BTC/USDT"
    assert adapter.calls[0][1] is not None and adapter.calls[0][1] <= int(T0.timestamp() * 1000)


def test_reconcile_sums_partial_fills_and_keeps_pending_when_trade_missing(tmp_path: Path):
    ledger = Ledger(tmp_path / "trades.csv")
    ledger.record_fill(filled("1", None))
    ledger.record_fill(filled("2", None))
    adapter = StubAdapter([trade("1", 0.3), trade("1", 0.45)])
    assert ledger.reconcile(adapter, "BTC/USDT") == 1
    assert ledger.pending_order_ids() == ["2"]
    assert ledger.is_complete() is False
    latest_for_1 = [r for r in ledger.rows() if r["order_id"] == "1"][-1]
    assert latest_for_1["fee"] == "0.75"


def test_mixed_fee_currencies_stay_pending(tmp_path: Path):
    ledger = Ledger(tmp_path / "trades.csv")
    ledger.record_fill(filled("1", None))
    adapter = StubAdapter([trade("1", 0.3, "USDT"), trade("1", 0.0001, "BTC")])
    assert ledger.reconcile(adapter, "BTC/USDT") == 0
    assert ledger.pending_order_ids() == ["1"]


def test_ledger_survives_reopen(tmp_path: Path):
    path = tmp_path / "trades.csv"
    Ledger(path).record_fill(filled("1", None))
    reopened = Ledger(path)
    assert reopened.pending_order_ids() == ["1"]
