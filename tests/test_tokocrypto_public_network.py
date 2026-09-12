"""Venue live: endpoint publik Tokocrypto sungguhan, tanpa kunci, tanpa order.

Ditandai network. Objek yang diuji adalah adapter PUBLIK (build_public_adapter),
bukan PaperAdapter yang dikembalikan build_adapter di mode paper sejak tahap 7:
PaperAdapter membungkus adapter publik ini untuk data, dan mensimulasikan akun,
jadi sifat "panggilan privat gagal tanpa kunci" hanya berlaku pada adapter
publiknya. Kalau host tidak terjangkau, conftest menandai test ini GAGAL
DIJALANKAN, terpisah dari yang dilewati karena kunci.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from tradebot.config import load_settings
from tradebot.data.ohlcv import timeframe_to_ms
from tradebot.exchange import FatalExchangeError
from tradebot.exchange.factory import build_public_adapter

pytestmark = pytest.mark.network

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("tradebot.tests.tokocrypto")


@pytest.fixture(scope="module")
def public():
    settings = load_settings(
        ROOT / "config" / "default.yaml", environ={**os.environ, "TRADING_MODE": "paper"}
    )
    adapter = build_public_adapter(settings)
    adapter.connect()
    return adapter, settings


def test_connect_validates_pair_and_clock(public):
    adapter, settings = public
    assert adapter.name == "tokocrypto-mainnet-public"
    assert abs(adapter.server_offset_ms) <= settings.exchange.max_time_drift_ms


def test_market_limits_include_min_notional(public):
    adapter, settings = public
    limits = adapter.fetch_market_limits(settings.exchange.symbol)
    assert limits.min_cost is not None and limits.min_cost > 0
    assert limits.amount_step is not None and limits.amount_step > 0
    assert limits.price_step is not None and limits.price_step > 0
    log.info("tokocrypto limits: %s", limits)


def test_ticker_from_official_market_data_host(public):
    adapter, settings = public
    ticker = adapter.fetch_ticker(settings.exchange.symbol)
    assert ticker.last > 0
    if ticker.bid is not None and ticker.ask is not None:
        assert ticker.bid <= ticker.ask
    log.info("tokocrypto ticker: last=%s bid=%s ask=%s", ticker.last, ticker.bid, ticker.ask)


def test_recent_hourly_bars_are_contiguous(public):
    adapter, settings = public
    symbol, timeframe = settings.exchange.symbol, settings.exchange.timeframe
    frame = adapter.fetch_ohlcv(symbol, timeframe, limit=24)
    assert len(frame) == 24
    step = pd.Timedelta(timeframe_to_ms(timeframe), unit="ms")
    assert list(frame["timestamp"].diff().dropna().unique()) == [step]
    age = datetime.now(tz=UTC) - frame["timestamp"].iloc[-1].to_pydatetime()
    assert age < 2 * step


def test_one_year_of_history_is_available(public):
    adapter, settings = public
    since = int((datetime.now(tz=UTC) - timedelta(days=366)).timestamp() * 1000)
    frame = adapter.fetch_ohlcv(settings.exchange.symbol, "1h", since_ms=since, limit=5)
    assert len(frame) == 5
    assert frame["timestamp"].iloc[0] < pd.Timestamp.now(tz="UTC") - pd.Timedelta(360, unit="D")


def test_private_calls_fail_locally_without_keys(public):
    """Sifat yang dijaga: adapter publik menolak semua panggilan privat SEBELUM menyentuh
    jaringan. Diuji juga offline di test_tokocrypto_adapter dan test_ccxt_adapter."""
    adapter, _ = public
    with pytest.raises(FatalExchangeError, match="kunci"):
        adapter.fetch_balance()
    with pytest.raises(FatalExchangeError, match="kunci"):
        adapter.fetch_open_orders(adapter.symbol)
    with pytest.raises(FatalExchangeError, match="kunci"):
        adapter.fetch_my_trades(adapter.symbol)


def test_paper_adapter_wraps_the_public_one(public):
    """build_adapter di mode paper membungkus adapter publik yang sama untuk data."""
    from tradebot.exchange.factory import build_adapter
    from tradebot.exchange.paper import PaperAdapter

    _, settings = public
    paper = build_adapter(settings)
    assert isinstance(paper, PaperAdapter)
    assert paper.public.name == "tokocrypto-mainnet-public"
    assert paper.public.can_trade is False
