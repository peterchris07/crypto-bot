"""Venue live: TokocryptoAdapter dengan klien palsu. Tidak menyentuh jaringan."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot.config import Credentials, ExchangeConfig, RetryConfig, VenueConfig
from tradebot.exchange import (
    FatalExchangeError,
    MainnetRefusedError,
    OrderNotFoundError,
    OrderSide,
    OrderStatus,
    OrderType,
)
from tradebot.exchange.tokocrypto_adapter import TokocryptoAdapter

KEY = "toko-test-key-0123456789"
SECRET = "toko-test-secret-9876543210"
CREDS = Credentials(api_key=KEY, api_secret=SECRET)
LOCAL_CLOCK_S = BASE_MS / 1000
MARKET_DATA_URL = "https://data.example/api/v3"


@pytest.fixture
def exchange_config() -> ExchangeConfig:
    return ExchangeConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        recv_window_ms=5000,
        max_time_drift_ms=1000,
        rate_limit=True,
        retry=RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=2.5),
        testnet=VenueConfig(id="binance", market_data_url=""),
        live=VenueConfig(id="tokocrypto", market_data_url=MARKET_DATA_URL),
    )


def build(
    config: ExchangeConfig,
    credentials: Credentials | None = None,
    *,
    allow_mainnet_trading: bool = False,
    connect: bool = True,
    venue: VenueConfig | None = None,
    mutate_market=None,
):
    holder: dict[str, FakeTokocryptoClient] = {}

    def factory(params):
        client = FakeTokocryptoClient(params)
        if mutate_market is not None:
            mutate_market(client)
        holder["client"] = client
        return client

    sleeps: list[float] = []
    adapter = TokocryptoAdapter(
        config,
        venue or config.live,
        credentials,
        allow_mainnet_trading=allow_mainnet_trading,
        client_factory=factory,
        sleep=sleeps.append,
        clock=lambda: LOCAL_CLOCK_S,
    )
    if connect:
        adapter.connect()
    return adapter, holder["client"], sleeps


# --------------------------------------------------------------------------- #
# Klien, host data pasar, gerbang
# --------------------------------------------------------------------------- #


def test_public_adapter_routes_market_data_to_official_host(exchange_config):
    adapter, client, _ = build(exchange_config, connect=False)
    assert client.urls["api"]["rest"]["binance"] == MARKET_DATA_URL
    assert client.urls["api"]["rest"]["public"] == "https://www.tokocrypto.com"
    assert client.urls["api"]["rest"]["private"] == "https://www.tokocrypto.com"
    assert adapter.name == "tokocrypto-mainnet-public"
    assert adapter.can_trade is False
    assert "apiKey" not in client.params


def test_trading_adapter_also_routes_market_data_to_official_host(exchange_config, caplog):
    """Berbeda dari Binance: host data resmi dipakai adapter berkunci juga."""
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, _ = build(exchange_config, CREDS, allow_mainnet_trading=True, connect=False)
    assert client.urls["api"]["rest"]["binance"] == MARKET_DATA_URL
    assert adapter.name == "tokocrypto-mainnet-trading"
    assert client.params["apiKey"] == KEY
    assert client.params["options"]["recvWindow"] == 5000
    assert "MAINNET" in caplog.text


def test_empty_market_data_url_keeps_ccxt_default_and_warns(exchange_config, caplog):
    caplog.set_level(logging.WARNING, logger="tradebot")
    venue = VenueConfig(id="tokocrypto", market_data_url="")
    _, client, _ = build(exchange_config, venue=venue, connect=False)
    assert client.urls["api"]["rest"]["binance"] == "https://api.binance.com/api/v3"
    assert "api.binance.com" in caplog.text


def test_sandbox_is_refused(exchange_config):
    with pytest.raises(FatalExchangeError, match="tidak punya testnet"):
        TokocryptoAdapter(
            exchange_config,
            exchange_config.live,
            CREDS,
            sandbox=True,
            client_factory=FakeTokocryptoClient,
        )


def test_keys_without_gate_are_refused_before_client_exists(exchange_config):
    calls: list[dict] = []
    with pytest.raises(MainnetRefusedError, match="build_adapter"):
        TokocryptoAdapter(exchange_config, exchange_config.live, CREDS, client_factory=calls.append)
    assert calls == []


# --------------------------------------------------------------------------- #
# Validasi pair saat connect (Tokocrypto pernah memindahkan pair tanpa aba-aba)
# --------------------------------------------------------------------------- #


def test_connect_fails_clearly_when_pair_is_gone(exchange_config):
    def remove_pair(client):
        client.markets = {"ETH/USDT": {**client.markets["BTC/USDT"], "symbol": "ETH/USDT"}}

    with pytest.raises(FatalExchangeError, match="BTC/USDT.*tidak ada.*USDT=1") as exc:
        build(exchange_config, mutate_market=remove_pair)
    assert "dihapus atau diganti" in str(exc.value)


def test_connect_rejects_inactive_pair(exchange_config):
    def deactivate(client):
        client.markets["BTC/USDT"]["active"] = False

    with pytest.raises(FatalExchangeError, match="tidak aktif"):
        build(exchange_config, mutate_market=deactivate)


def test_connect_rejects_non_mbx_market_type(exchange_config):
    def make_nextme(client):
        client.markets["BTC/USDT"]["info"]["type"] = 3

    with pytest.raises(FatalExchangeError, match="berjenis 3"):
        build(exchange_config, mutate_market=make_nextme)


def test_connect_rejects_pair_without_required_order_types(exchange_config):
    def drop_stop(client):
        client.markets["BTC/USDT"]["info"]["orderTypes"] = ["LIMIT", "MARKET"]

    with pytest.raises(FatalExchangeError, match="STOP_LOSS_LIMIT"):
        build(exchange_config, mutate_market=drop_stop)


def test_connect_rejects_spot_disabled(exchange_config):
    def disable(client):
        client.markets["BTC/USDT"]["info"]["spotTradingEnable"] = False

    with pytest.raises(FatalExchangeError, match="spot trading dimatikan"):
        build(exchange_config, mutate_market=disable)


def test_connect_logs_validated_pair(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    build(exchange_config)
    assert "pair BTC/USDT tervalidasi" in caplog.text
    assert "id=BTC_USDT type=1" in caplog.text


# --------------------------------------------------------------------------- #
# Batas pasar: minimum notional dibaca dari filter mentah
# --------------------------------------------------------------------------- #


def test_market_limits_read_notional_filter_that_ccxt_skips(exchange_config):
    adapter, _, _ = build(exchange_config)
    limits = adapter.fetch_market_limits("BTC/USDT")
    assert limits.min_cost == 5.0
    assert limits.amount_step == 1e-05
    assert limits.price_step == 0.01
    assert limits.min_amount == 1e-05


def test_market_limits_without_notional_filter_is_none(exchange_config):
    def drop_notional(client):
        info = client.markets["BTC/USDT"]["info"]
        info["filters"] = [f for f in info["filters"] if f["filterType"] != "NOTIONAL"]

    adapter, _, _ = build(exchange_config, mutate_market=drop_notional)
    assert adapter.fetch_market_limits("BTC/USDT").min_cost is None


# --------------------------------------------------------------------------- #
# Order: quoteOrderQty, stopPrice, clientId, tanpa cancelAll
# --------------------------------------------------------------------------- #


def trading(exchange_config):
    return build(exchange_config, CREDS, allow_mainnet_trading=True)


def test_market_buy_is_converted_to_quote_cost_using_ask(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    adapter, client, _ = trading(exchange_config)
    order = adapter.create_order(
        "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="mb-1"
    )
    args, _ = client.last_call("create_order")
    assert args[1] == "market"
    assert args[4] is None
    assert args[5]["cost"] == pytest.approx(0.01 * 50_001.0)
    assert args[5]["clientId"] == "mb-1"
    assert "clientOrderId" not in args[5]
    assert order.type is OrderType.MARKET
    assert order.status is OrderStatus.CLOSED
    assert order.filled == pytest.approx(0.01)
    assert order.client_order_id == "mb-1"
    assert "dikonversi ke quote" in caplog.text
    assert "ORDER INTENT" in caplog.text


def test_market_sell_sends_base_amount_without_cost(exchange_config):
    adapter, client, _ = trading(exchange_config)
    order = adapter.create_order("BTC/USDT", OrderSide.SELL, OrderType.MARKET, 0.02)
    args, _ = client.last_call("create_order")
    assert "cost" not in args[5]
    assert args[3] == 0.02
    assert order.filled == 0.02
    assert order.average == 49_999.0


def test_stop_loss_limit_uses_stop_price_and_is_parsed_from_info_type(exchange_config):
    adapter, client, _ = trading(exchange_config)
    order = adapter.create_order(
        "BTC/USDT",
        OrderSide.SELL,
        OrderType.STOP_LOSS_LIMIT,
        0.01,
        price=47_000.0,
        stop_price=47_500.0,
        client_order_id="stop-1",
    )
    args, _ = client.last_call("create_order")
    assert args[1] == "limit"
    assert args[4] == 47_000.0
    assert args[5]["stopPrice"] == 47_500.0
    assert "stopLossPrice" not in args[5]
    assert order.type is OrderType.STOP_LOSS_LIMIT, "ccxt melabeli type 4 sebagai limit"
    assert order.stop_price == 47_500.0
    assert order.is_open


def test_limit_order_type_parsed_from_info(exchange_config):
    adapter, _, _ = trading(exchange_config)
    order = adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    assert order.type is OrderType.LIMIT
    assert order.is_open


def test_cancel_all_orders_cancels_one_by_one(exchange_config, caplog):
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, _ = trading(exchange_config)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.02, price=41_000.0)
    canceled = adapter.cancel_all_orders("BTC/USDT")
    assert len(canceled) == 2
    assert client.count("cancel_all_orders") == 0
    assert client.count("cancel_order") == 2
    assert adapter.fetch_open_orders("BTC/USDT") == []
    assert "CANCEL ALL RESULT" in caplog.text


def test_fetch_order_by_client_id_scans_open_orders_then_history(exchange_config):
    adapter, client, _ = trading(exchange_config)
    placed = adapter.create_order(
        "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0, client_order_id="cid-1"
    )
    found = adapter.fetch_order("BTC/USDT", client_order_id="cid-1")
    assert found.id == placed.id
    assert client.count("fetch_orders") == 0, "masih terbuka: cukup dari open orders"

    adapter.cancel_order(placed.id, "BTC/USDT")
    found_again = adapter.fetch_order("BTC/USDT", client_order_id="cid-1")
    assert found_again.id == placed.id
    assert found_again.status is OrderStatus.CANCELED
    assert client.count("fetch_orders") == 1

    with pytest.raises(OrderNotFoundError, match="nope"):
        adapter.fetch_order("BTC/USDT", client_order_id="nope")


def test_fetch_order_by_id_uses_order_id(exchange_config):
    adapter, client, _ = trading(exchange_config)
    placed = adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    found = adapter.fetch_order("BTC/USDT", order_id=placed.id)
    args, _ = client.last_call("fetch_order")
    assert args[0] == placed.id
    assert found.id == placed.id


def test_public_adapter_refuses_private_calls(exchange_config):
    adapter, client, _ = build(exchange_config)
    with pytest.raises(FatalExchangeError, match="kunci"):
        adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01)
    assert client.count("create_order") == 0


def test_retry_config_is_shared_with_base(exchange_config):
    config = dataclasses.replace(
        exchange_config,
        retry=RetryConfig(max_attempts=2, base_delay_seconds=0.5, max_delay_seconds=0.5),
    )
    adapter, client, sleeps = build(config)
    import ccxt

    client.fail_next("fetch_ticker", ccxt.NetworkError("putus"))
    assert adapter.fetch_ticker("BTC/USDT").last == 50_000.0
    assert sleeps == [0.5]


def test_adapter_never_logs_secrets(exchange_config, caplog):
    caplog.set_level(logging.DEBUG, logger="tradebot")
    adapter, _, _ = trading(exchange_config)
    adapter.fetch_balance()
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="x")
    assert KEY not in caplog.text
    assert SECRET not in caplog.text
