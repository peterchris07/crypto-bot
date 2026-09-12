"""Tahap 7: PaperAdapter. Harga dari adapter publik palsu, eksekusi simulasi, akun dipersist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot.config import load_settings
from tradebot.exchange import (
    InsufficientFundsError,
    InvalidOrderError,
    OrderNotFoundError,
    OrderSide,
    OrderStatus,
    OrderType,
)
from tradebot.exchange.factory import build_adapter, build_public_adapter
from tradebot.exchange.paper import PaperAdapter

LOCAL_CLOCK_S = BASE_MS / 1000


@pytest.fixture
def settings(config_path):
    return load_settings(config_path, environ={})


def make(settings, project_dir: Path, **kwargs) -> tuple[PaperAdapter, FakeTokocryptoClient]:
    holder = {}

    def factory(params):
        client = FakeTokocryptoClient(params)
        holder["client"] = client
        return client

    public = build_public_adapter(settings, client_factory=factory, clock=lambda: LOCAL_CLOCK_S)
    adapter = PaperAdapter(
        public,
        settings.costs,
        symbol="BTC/USDT",
        account_path=project_dir / settings.live.paper_account_path,
        initial_quote=settings.backtest.initial_equity,
        clock=lambda: LOCAL_CLOCK_S,
        **kwargs,
    )
    adapter.connect()
    return adapter, holder["client"]


def test_market_buy_fills_at_ask_plus_slippage_and_charges_fees(settings, project_dir):
    adapter, client = make(settings, project_dir)
    c = settings.costs
    order = adapter.create_order(
        "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="c1"
    )
    fill = 50_001.0 * (1 + c.slippage_rate)
    assert order.status is OrderStatus.CLOSED and order.filled == 0.01
    assert order.average == pytest.approx(fill)
    assert order.cost == pytest.approx(0.01 * fill)
    assert order.fee == pytest.approx(0.01 * fill * c.total_fee_rate)
    assert order.fee_currency == "USDT" and order.client_order_id == "c1"
    balance = adapter.fetch_balance()
    assert balance.total("BTC") == pytest.approx(0.01)
    assert balance.total("USDT") == pytest.approx(1000.0 - order.cost - order.fee)
    # create_order paper tidak pernah menyentuh endpoint order publik
    assert client.count("create_order") == 0


def test_market_sell_fills_at_bid_minus_slippage(settings, project_dir):
    adapter, _ = make(settings, project_dir)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01)
    order = adapter.create_order("BTC/USDT", OrderSide.SELL, OrderType.MARKET, 0.01)
    fill = 49_999.0 * (1 - settings.costs.slippage_rate)
    assert order.average == pytest.approx(fill)
    balance = adapter.fetch_balance()
    assert balance.total("BTC") == pytest.approx(0.0)
    assert balance.total("USDT") < 1000.0, "dua sisi biaya terpotong"


def test_account_persists_and_reloads(settings, project_dir):
    adapter, _ = make(settings, project_dir)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="c9")
    on_disk = json.loads((project_dir / "state" / "paper_account.json").read_text())
    assert on_disk["balances"]["BTC"] == pytest.approx(0.01)
    again, _ = make(settings, project_dir)
    assert again.fetch_balance().total("BTC") == pytest.approx(0.01)
    found = again.fetch_order("BTC/USDT", client_order_id="c9")
    assert found.id == "paper-1" and found.filled == 0.01
    assert again.fetch_order("BTC/USDT", order_id="paper-1").client_order_id == "c9"
    with pytest.raises(OrderNotFoundError):
        again.fetch_order("BTC/USDT", client_order_id="tidak-ada")


def test_insufficient_funds_and_unsupported_orders_are_refused(settings, project_dir):
    adapter, _ = make(settings, project_dir)
    with pytest.raises(InsufficientFundsError, match="USDT"):
        adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 1.0)  # 50k USDT
    with pytest.raises(InsufficientFundsError, match="BTC"):
        adapter.create_order("BTC/USDT", OrderSide.SELL, OrderType.MARKET, 0.01)
    with pytest.raises(InvalidOrderError, match="MARKET"):
        adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=1.0)
    with pytest.raises(InvalidOrderError, match="ETH/USDT"):
        adapter.create_order("ETH/USDT", OrderSide.BUY, OrderType.MARKET, 0.01)
    assert adapter.fetch_balance().total("USDT") == 1000.0


def test_trades_carry_fees_so_ledger_reconciles_immediately(settings, project_dir):
    adapter, _ = make(settings, project_dir)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01)
    trades = adapter.fetch_my_trades("BTC/USDT")
    assert len(trades) == 1 and trades[0].order_id == "paper-1" and trades[0].fee is not None
    assert adapter.fetch_my_trades("BTC/USDT", since_ms=BASE_MS + 1) == []
    assert (
        adapter.fetch_open_orders("BTC/USDT") == [] and adapter.cancel_all_orders("BTC/USDT") == []
    )
    with pytest.raises(OrderNotFoundError):
        adapter.cancel_order("paper-1", "BTC/USDT")


def test_market_data_is_delegated_to_public_adapter(settings, project_dir):
    adapter, client = make(settings, project_dir)
    assert adapter.fetch_ticker("BTC/USDT").last == 50_000.0
    assert len(adapter.fetch_ohlcv("BTC/USDT", "1h", limit=3)) == 3
    assert adapter.fetch_market_limits("BTC/USDT").min_cost == 5.0
    assert adapter.fetch_server_time_ms() == BASE_MS
    assert client.count("load_markets") == 1


def test_account_for_other_symbol_or_corrupt_file_is_refused(settings, project_dir):
    path = project_dir / "state" / "paper_account.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"symbol": "ETH/USDT", "balances": {}}), encoding="utf-8")
    from tradebot.exchange import FatalExchangeError

    with pytest.raises(FatalExchangeError, match="ETH/USDT"):
        make(settings, project_dir)
    path.write_text("{rusak", encoding="utf-8")
    with pytest.raises(FatalExchangeError, match="rusak"):
        make(settings, project_dir)


def test_build_adapter_paper_check_exchange_shows_paper_balance(config_path, project_dir, capsys):
    from tradebot import cli

    code = cli.main(["--config", str(config_path), "check-exchange"]) if False else None
    settings = load_settings(config_path, environ={})
    adapter = build_adapter(
        settings, client_factory=FakeTokocryptoClient, clock=lambda: LOCAL_CLOCK_S
    )
    adapter.connect()
    assert adapter.can_trade and adapter.fetch_balance().total("USDT") == 1000.0
    assert code is None
