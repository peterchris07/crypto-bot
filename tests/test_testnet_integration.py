"""Tahap 2: integrasi ke Binance Spot Testnet. Butuh kunci testnet di .env.

Tanpa kunci, seluruh modul dilewati dan muncul di ringkasan skip. Yang diuji:
jam, pasar, OHLCV, ticker, saldo, order terbuka, cancel_all_orders saat kosong,
dan satu putaran order sungguhan: limit order jauh dari harga pasar, muncul di
open orders dengan client_order_id yang benar, dibatalkan, lalu hilang.
Testnet memakai uang palsu; aturan keras 4 melindungi uang asli, dan justru
testnet ada supaya jalur order bisa diuji tanpa risiko.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from tests.helpers import ROUND_UP, price_far_below_market, quantize
from tradebot.config import Settings, load_settings
from tradebot.data.ohlcv import timeframe_to_ms
from tradebot.exchange import OrderNotFoundError, OrderSide, OrderStatus, OrderType
from tradebot.exchange.ccxt_adapter import CcxtAdapter

pytestmark = pytest.mark.testnet

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("tradebot.tests.testnet")


@pytest.fixture(scope="module")
def testnet() -> tuple[CcxtAdapter, Settings]:
    settings = load_settings(
        ROOT / "config" / "default.yaml",
        environ={**os.environ, "TRADING_MODE": "testnet"},
    )
    adapter = CcxtAdapter(
        settings.exchange, settings.exchange.testnet, settings.credentials, sandbox=True
    )
    adapter.connect()
    return adapter, settings


def test_connect_reports_clock_within_limit(testnet):
    adapter, settings = testnet
    assert adapter.is_sandbox is True
    assert adapter.can_trade is True
    assert abs(adapter.server_offset_ms) <= settings.exchange.max_time_drift_ms


def test_fetch_ohlcv_returns_recent_contiguous_bars(testnet):
    adapter, settings = testnet
    symbol, timeframe = settings.exchange.symbol, settings.exchange.timeframe
    frame = adapter.fetch_ohlcv(symbol, timeframe, limit=48)
    assert len(frame) == 48
    step = pd.Timedelta(timeframe_to_ms(timeframe), unit="ms")
    diffs = frame["timestamp"].diff().dropna().unique()
    assert list(diffs) == [step], f"jarak antar bar tidak seragam: {diffs}"
    assert (frame["close"] > 0).all()
    age = datetime.now(tz=UTC) - frame["timestamp"].iloc[-1].to_pydatetime()
    assert age < 2 * step, f"bar terakhir terlalu tua: {age}"
    log.info(
        "testnet OHLCV %s %s: bar terakhir %s close=%s",
        symbol,
        timeframe,
        frame["timestamp"].iloc[-1].isoformat(),
        frame["close"].iloc[-1],
    )


def test_fetch_ticker_has_positive_price(testnet):
    adapter, settings = testnet
    ticker = adapter.fetch_ticker(settings.exchange.symbol)
    assert ticker.last > 0
    if ticker.bid is not None and ticker.ask is not None:
        assert ticker.bid <= ticker.ask


def test_fetch_market_limits_reports_minimums(testnet):
    adapter, settings = testnet
    limits = adapter.fetch_market_limits(settings.exchange.symbol)
    assert limits.base == "BTC"
    assert limits.quote == "USDT"
    assert limits.amount_step is not None and limits.amount_step > 0
    assert limits.min_cost is not None and limits.min_cost > 0
    log.info("testnet limits: %s", limits)


def test_fetch_balance_reports_testnet_funds(testnet):
    adapter, _ = testnet
    balance = adapter.fetch_balance()
    assert balance.assets, "testnet harus mengembalikan daftar aset"
    held = {asset: b.total for asset, b in balance.assets.items() if b.total > 0}
    log.info("testnet saldo (total > 0): %s", held)
    assert held, "akun testnet kosong; minta dana palsu di testnet.binance.vision"


def test_fetch_open_orders_returns_list(testnet):
    adapter, settings = testnet
    orders = adapter.fetch_open_orders(settings.exchange.symbol)
    assert isinstance(orders, list)
    log.info("testnet order terbuka %s: %d", settings.exchange.symbol, len(orders))


def test_cancel_all_orders_is_safe_when_nothing_is_open(testnet):
    adapter, settings = testnet
    symbol = settings.exchange.symbol
    if adapter.fetch_open_orders(symbol):
        pytest.skip("ada order terbuka di testnet yang bukan milik test ini; tidak disentuh")
    assert adapter.cancel_all_orders(symbol) == []


def test_limit_order_roundtrip_far_from_market(testnet):
    """Utang tahap 2: jalur order diuji terhadap API sungguhan dengan uang palsu."""
    adapter, settings = testnet
    symbol = settings.exchange.symbol
    limits = adapter.fetch_market_limits(symbol)
    ticker = adapter.fetch_ticker(symbol)

    # Jauh di bawah harga pasar tapi masih di dalam band PERCENT_PRICE_BY_SIDE exchange,
    # dihitung dari filter yang dilaporkan exchange, bukan faktor tetap.
    price = price_far_below_market(ticker.last, limits)
    min_cost = (limits.min_cost or 5.0) * 1.5
    amount = quantize(min_cost / price, limits.amount_step, ROUND_UP)
    if limits.min_amount:
        amount = max(amount, limits.min_amount)
    client_order_id = f"tb-test-{uuid4().hex[:16]}"
    log.info(
        "testnet order uji: %s LIMIT BUY %s @ %s client_order_id=%s",
        symbol,
        amount,
        price,
        client_order_id,
    )

    placed = None
    try:
        placed = adapter.create_order(
            symbol,
            OrderSide.BUY,
            OrderType.LIMIT,
            amount,
            price=price,
            client_order_id=client_order_id,
        )
        assert placed.client_order_id == client_order_id
        assert placed.is_open, placed

        matches = [
            o for o in adapter.fetch_open_orders(symbol) if o.client_order_id == client_order_id
        ]
        assert len(matches) == 1, matches
        assert matches[0].id == placed.id
        assert matches[0].price == pytest.approx(price)

        canceled = adapter.cancel_order(placed.id, symbol)
        assert canceled.status is OrderStatus.CANCELED
        remaining = [
            o for o in adapter.fetch_open_orders(symbol) if o.client_order_id == client_order_id
        ]
        assert remaining == []
        placed = None
    finally:
        # Kalau assert gagal di tengah, order uji tidak boleh tertinggal di testnet.
        if placed is not None and placed.id:
            try:
                adapter.cancel_order(placed.id, symbol)
            except OrderNotFoundError:
                pass
