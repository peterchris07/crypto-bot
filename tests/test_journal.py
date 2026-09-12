"""Tahap 7: jurnal order write-ahead."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradebot.exchange import Order, OrderSide, OrderStatus, OrderType
from tradebot.live.journal import OrderJournal

T = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def order(cid: str) -> Order:
    return Order(
        id="paper-1",
        client_order_id=cid,
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
        fee=2.7,
        fee_currency="USDT",
        timestamp=T,
    )


def test_intent_is_written_before_result_and_each_line_is_fsynced(tmp_path: Path, monkeypatch):
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    journal = OrderJournal(tmp_path / "state" / "orders.jsonl")
    journal.record_intent(
        "c1",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        amount=0.01,
        reason="signal",
        time=T,
    )
    assert journal.pending_intents() and journal.pending_intents()[0]["client_order_id"] == "c1"
    journal.record_result("c1", order("c1"), T)
    assert journal.pending_intents() == []
    lines = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [line["event"] for line in lines] == ["intent", "result"]
    assert lines[0]["reason"] == "signal" and lines[1]["order_id"] == "paper-1"
    assert len(synced) == 2


def test_unknown_then_reconciled_closes_the_intent(tmp_path: Path):
    journal = OrderJournal(tmp_path / "orders.jsonl")
    journal.record_intent(
        "c2",
        symbol="BTC/USDT",
        side="sell",
        order_type="market",
        amount=0.01,
        reason="stop_loss",
        time=T,
    )
    journal.record_unknown("c2", "timeout", T)
    assert [e["client_order_id"] for e in journal.pending_intents()] == ["c2"]
    journal.record_reconciled("c2", "not_found", T)
    assert journal.pending_intents() == []
    journal.record_intent(
        "c3",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        amount=0.01,
        reason="signal",
        time=T,
    )
    journal.record_reconciled("c3", "found", T, order("c3"))
    assert journal.pending_intents() == []
    assert journal.entries()[-1]["filled"] == 0.01


def test_corrupt_line_is_an_error(tmp_path: Path):
    path = tmp_path / "orders.jsonl"
    path.write_text('{"event": "intent", "client_order_id": "c1"}\n{rusak\n', encoding="utf-8")
    with pytest.raises(ValueError, match="baris 2"):
        OrderJournal(path).entries()
