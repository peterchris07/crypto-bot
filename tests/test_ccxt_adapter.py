"""Tahap 2: CcxtAdapter dengan klien palsu. Tidak menyentuh jaringan."""

from __future__ import annotations

import dataclasses
import logging

import ccxt
import pytest

from tests.fakes import BASE_MS, FakeCcxtClient
from tradebot.config import Credentials, ExchangeConfig, RetryConfig, VenueConfig
from tradebot.exchange import (
    AuthenticationError,
    FatalExchangeError,
    InsufficientFundsError,
    InvalidOrderError,
    MainnetRefusedError,
    OrderNotFoundError,
    OrderSide,
    OrderStateUnknownError,
    OrderStatus,
    OrderType,
    RetryableExchangeError,
    TimeDriftError,
)
from tradebot.exchange.ccxt_adapter import CcxtAdapter

KEY = "unit-test-key-0123456789"
SECRET = "unit-test-secret-9876543210"
CREDS = Credentials(api_key=KEY, api_secret=SECRET)
LOCAL_CLOCK_S = BASE_MS / 1000  # jam lokal persis sama dengan jam server palsu
PUBLIC_DATA_URL = "https://data.example/api/v3"
VENUE = VenueConfig(id="binance", market_data_url=PUBLIC_DATA_URL)


@pytest.fixture
def exchange_config() -> ExchangeConfig:
    return ExchangeConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        recv_window_ms=5000,
        max_time_drift_ms=1000,
        time_sync_samples=3,
        rate_limit=True,
        retry=RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=2.5),
        testnet=VenueConfig(id="binance", market_data_url=""),
        live=VenueConfig(id="tokocrypto", market_data_url="https://toko.example/api/v3"),
    )


def build(
    config: ExchangeConfig,
    credentials: Credentials | None = CREDS,
    *,
    sandbox: bool = True,
    allow_mainnet_trading: bool = False,
    connect: bool = True,
    venue: VenueConfig = VENUE,
) -> tuple[CcxtAdapter, FakeCcxtClient, list[float]]:
    holder: dict[str, FakeCcxtClient] = {}

    def factory(params):
        holder["client"] = FakeCcxtClient(params)
        return holder["client"]

    sleeps: list[float] = []
    adapter = CcxtAdapter(
        config,
        venue,
        credentials,
        sandbox=sandbox,
        allow_mainnet_trading=allow_mainnet_trading,
        client_factory=factory,
        sleep=sleeps.append,
        clock=lambda: LOCAL_CLOCK_S,
    )
    if connect:
        adapter.connect()
    return adapter, holder["client"], sleeps


# --------------------------------------------------------------------------- #
# Konstruksi dan gerbang mainnet
# --------------------------------------------------------------------------- #


def test_testnet_client_gets_keys_sandbox_and_rate_limit(exchange_config):
    adapter, client, _ = build(exchange_config, connect=False)
    assert client.params["apiKey"] == KEY
    assert client.params["secret"] == SECRET
    assert client.params["enableRateLimit"] is True
    assert client.params["options"]["recvWindow"] == 5000
    assert client.params["options"]["defaultType"] == "spot"
    assert client.sandbox is True
    assert adapter.can_trade is True
    assert adapter.is_sandbox is True
    assert adapter.name == "binance-testnet-trading"


def test_public_mainnet_client_has_no_keys_and_cannot_trade(exchange_config):
    adapter, client, _ = build(exchange_config, credentials=None, sandbox=False, connect=False)
    assert "apiKey" not in client.params
    assert "secret" not in client.params
    assert client.sandbox is None
    assert adapter.can_trade is False
    assert adapter.name == "binance-mainnet-public"


def test_public_mainnet_client_uses_public_market_data_url(exchange_config):
    """ISP bisa memblokir api.binance.com; data publik lewat endpoint data resmi."""
    adapter, client, _ = build(exchange_config, credentials=None, sandbox=False, connect=False)
    assert client.urls["api"]["public"] == PUBLIC_DATA_URL
    assert client.urls["api"]["private"] == "https://api.example/api/v3"
    assert client.params["options"]["fetchMarkets"] == {"types": ["spot"], "loadAllOptions": False}


def test_public_url_override_is_optional(exchange_config):
    venue = VenueConfig(id="binance", market_data_url="")
    _, client, _ = build(
        exchange_config, credentials=None, sandbox=False, connect=False, venue=venue
    )
    assert client.urls["api"]["public"] == "https://api.example/api/v3"


@pytest.mark.parametrize(
    ("credentials", "sandbox", "allow"),
    [(CREDS, True, False), (CREDS, False, True)],
)
def test_keyed_clients_keep_default_urls(exchange_config, credentials, sandbox, allow):
    """Adapter berkunci tidak boleh diarahkan ke endpoint data: endpoint akun tidak ada di sana."""
    _, client, _ = build(
        exchange_config, credentials, sandbox=sandbox, allow_mainnet_trading=allow, connect=False
    )
    assert client.urls["api"]["public"] == "https://api.example/api/v3"


def test_mainnet_with_keys_is_refused_without_gate(exchange_config):
    factory_calls: list[dict] = []

    with pytest.raises(MainnetRefusedError, match="build_adapter"):
        CcxtAdapter(
            exchange_config, VENUE, CREDS, sandbox=False, client_factory=factory_calls.append
        )
    assert factory_calls == [], "klien mainnet tidak boleh sempat dibuat"


def test_mainnet_with_keys_needs_explicit_gate_and_warns(exchange_config, caplog):
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, _ = build(
        exchange_config, sandbox=False, allow_mainnet_trading=True, connect=False
    )
    assert adapter.name == "binance-mainnet-trading"
    assert client.sandbox is None
    assert "MAINNET" in caplog.text


# --------------------------------------------------------------------------- #
# connect(): jam server dan pasar
# --------------------------------------------------------------------------- #


def test_connect_measures_drift_and_loads_markets(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    adapter, client, _ = build(exchange_config)
    assert adapter.server_offset_ms == 0
    # satu pemanasan yang dibuang + time_sync_samples sampel
    assert client.count("fetch_time") == 1 + exchange_config.time_sync_samples
    assert client.count("load_markets") == 1
    assert "jam: lokal - server = +0 ms" in caplog.text


def test_connect_refuses_when_clock_drifts_beyond_limit(exchange_config):
    holder: dict[str, FakeCcxtClient] = {}

    def factory(params):
        holder["client"] = FakeCcxtClient(params)
        holder["client"].server_time_ms = BASE_MS - 5000  # lokal 5 detik di depan server
        return holder["client"]

    adapter = CcxtAdapter(
        exchange_config,
        VENUE,
        CREDS,
        sandbox=True,
        client_factory=factory,
        clock=lambda: LOCAL_CLOCK_S,
    )
    with pytest.raises(TimeDriftError) as exc:
        adapter.connect()
    message = str(exc.value)
    assert "+5000 ms" in message
    assert "1000 ms" in message
    assert holder["client"].count("load_markets") == 0
    with pytest.raises(FatalExchangeError, match="connect"):
        adapter.fetch_ticker("BTC/USDT")


def test_connect_fails_when_configured_pair_is_missing(exchange_config):
    holder: dict[str, FakeCcxtClient] = {}

    def factory(params):
        client = FakeCcxtClient(params)
        client.markets = {"ETH/USDT": {**client.markets["BTC/USDT"], "symbol": "ETH/USDT"}}
        holder["client"] = client
        return client

    adapter = CcxtAdapter(
        exchange_config,
        VENUE,
        CREDS,
        sandbox=True,
        client_factory=factory,
        clock=lambda: LOCAL_CLOCK_S,
    )
    with pytest.raises(FatalExchangeError, match="BTC/USDT.*tidak ada"):
        adapter.connect()
    with pytest.raises(FatalExchangeError, match="connect"):
        adapter.fetch_ticker("ETH/USDT")


def test_methods_require_connect_first(exchange_config):
    adapter, client, _ = build(exchange_config, connect=False)
    with pytest.raises(FatalExchangeError, match="connect"):
        adapter.fetch_ohlcv("BTC/USDT", "1h")
    assert client.count("fetch_ohlcv") == 0


def test_public_adapter_refuses_private_calls_without_network(exchange_config):
    adapter, client, _ = build(exchange_config, credentials=None, sandbox=False)
    with pytest.raises(FatalExchangeError, match="kunci"):
        adapter.fetch_balance()
    with pytest.raises(FatalExchangeError, match="kunci"):
        adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01)
    assert client.count("fetch_balance") == 0
    assert client.count("create_order") == 0


# --------------------------------------------------------------------------- #
# Retry dan backoff
# --------------------------------------------------------------------------- #


def test_network_errors_are_retried_with_exponential_backoff(exchange_config, caplog):
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, sleeps = build(exchange_config)
    client.fail_next("fetch_ticker", ccxt.NetworkError("putus"), ccxt.RequestTimeout("lambat"))
    ticker = adapter.fetch_ticker("BTC/USDT")
    assert ticker.last == 50_000.0
    assert client.count("fetch_ticker") == 3
    assert sleeps == [1.0, 2.0]
    assert caplog.text.count("coba lagi") == 2


def test_retry_exhaustion_raises_retryable_error(exchange_config):
    adapter, client, sleeps = build(exchange_config)
    client.fail_next(
        "fetch_ticker", ccxt.NetworkError("1"), ccxt.NetworkError("2"), ccxt.NetworkError("3")
    )
    with pytest.raises(RetryableExchangeError):
        adapter.fetch_ticker("BTC/USDT")
    assert client.count("fetch_ticker") == 3
    assert sleeps == [1.0, 2.0], "setelah percobaan terakhir tidak ada sleep"


def test_backoff_is_capped_at_max_delay(exchange_config):
    config = dataclasses.replace(
        exchange_config,
        retry=RetryConfig(max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=2.5),
    )
    adapter, client, sleeps = build(config)
    client.fail_next("fetch_ticker", *(ccxt.NetworkError(str(i)) for i in range(4)))
    adapter.fetch_ticker("BTC/USDT")
    assert sleeps == [1.0, 2.0, 2.5, 2.5]


def test_rate_limit_and_maintenance_are_retried(exchange_config):
    adapter, client, sleeps = build(exchange_config)
    client.fail_next(
        "fetch_ticker", ccxt.RateLimitExceeded("429"), ccxt.OnMaintenance("maintenance")
    )
    adapter.fetch_ticker("BTC/USDT")
    assert client.count("fetch_ticker") == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (ccxt.AuthenticationError("bad key"), AuthenticationError),
        (ccxt.PermissionDenied("no permission"), AuthenticationError),
        (ccxt.InsufficientFunds("poor"), InsufficientFundsError),
        (ccxt.InvalidOrder("too small"), InvalidOrderError),
        (ccxt.OrderNotFound("gone"), OrderNotFoundError),
        (ccxt.BadSymbol("nope"), FatalExchangeError),
        (ccxt.NotSupported("nope"), FatalExchangeError),
        (ccxt.InvalidNonce("nonce"), TimeDriftError),
        (
            ccxt.BadRequest(
                'binance {"code":-1021,"msg":"Timestamp for this request is outside of the '
                'recvWindow."}'
            ),
            TimeDriftError,
        ),
        (
            ccxt.OperationRejected(
                'binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}'
            ),
            AuthenticationError,
        ),
        (
            ccxt.OperationRejected('binance {"code":-2011,"msg":"Unknown order sent."}'),
            OrderNotFoundError,
        ),
        (ccxt.OperationRejected("something else"), FatalExchangeError),
    ],
)
def test_fatal_errors_are_never_retried(exchange_config, raised, expected):
    adapter, client, sleeps = build(exchange_config)
    client.fail_next("fetch_ticker", raised)
    with pytest.raises(expected):
        adapter.fetch_ticker("BTC/USDT")
    assert client.count("fetch_ticker") == 1
    assert sleeps == []


def test_time_drift_error_from_exchange_names_the_clock(exchange_config):
    adapter, client, _ = build(exchange_config)
    client.fail_next("fetch_balance", ccxt.InvalidNonce("your time is ahead of server"))
    with pytest.raises(TimeDriftError, match="[Jj]am lokal"):
        adapter.fetch_balance()


# --------------------------------------------------------------------------- #
# Pemetaan data publik dan saldo
# --------------------------------------------------------------------------- #


def test_fetch_ohlcv_returns_schema_frame_and_passes_arguments(exchange_config):
    adapter, client, _ = build(exchange_config)
    frame = adapter.fetch_ohlcv("BTC/USDT", "1h", since_ms=BASE_MS + 3_600_000, limit=2)
    args, _ = client.last_call("fetch_ohlcv")
    assert args == ("BTC/USDT", "1h", BASE_MS + 3_600_000, 2)
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert str(frame["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert frame["close"].tolist() == [101.5, 102.5]


def test_fetch_balance_maps_free_used_total(exchange_config):
    adapter, _, _ = build(exchange_config)
    balance = adapter.fetch_balance()
    assert balance.free("USDT") == 1000.0
    assert balance.used("BTC") == 0.1
    assert balance.total("BTC") == 0.6
    assert balance.free("XRP") == 0.0


def test_fetch_ticker_maps_fields(exchange_config):
    adapter, _, _ = build(exchange_config)
    ticker = adapter.fetch_ticker("BTC/USDT")
    assert (ticker.last, ticker.bid, ticker.ask) == (50_000.0, 49_999.0, 50_001.0)
    assert ticker.timestamp.isoformat() == "2023-11-14T22:13:20+00:00"


def test_fetch_market_limits_maps_step_and_min_cost(exchange_config):
    adapter, _, _ = build(exchange_config)
    limits = adapter.fetch_market_limits("BTC/USDT")
    assert limits.base == "BTC"
    assert limits.quote == "USDT"
    assert limits.min_amount == 1e-05
    assert limits.amount_step == 1e-05
    assert limits.min_cost == 5.0
    assert limits.price_step == 0.01


def test_fetch_market_limits_unknown_symbol_is_fatal(exchange_config):
    adapter, _, _ = build(exchange_config)
    with pytest.raises(FatalExchangeError, match="DOGE/USDT"):
        adapter.fetch_market_limits("DOGE/USDT")


# --------------------------------------------------------------------------- #
# Order: niat dicatat sebelum kirim, tidak pernah dikirim ulang
# --------------------------------------------------------------------------- #


def test_create_order_logs_intent_before_sending(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    adapter, client, _ = build(exchange_config)
    client.fail_next("create_order", ccxt.InsufficientFunds("poor"))
    with pytest.raises(InsufficientFundsError):
        adapter.create_order(
            "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="intent-1"
        )
    assert client.count("create_order") == 1
    assert "ORDER INTENT" in caplog.text
    assert "client_order_id=intent-1" in caplog.text
    assert "ORDER RESULT" not in caplog.text


def test_create_order_passes_client_order_id_and_maps_result(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    adapter, client, _ = build(exchange_config)
    order = adapter.create_order(
        "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="abc-123"
    )
    args, _ = client.last_call("create_order")
    assert args[:5] == ("BTC/USDT", "market", "buy", 0.01, None)
    assert args[5]["clientOrderId"] == "abc-123"
    assert order.id == "1"
    assert order.client_order_id == "abc-123"
    assert order.status is OrderStatus.CLOSED
    assert order.filled == 0.01
    assert order.average == 50_000.0
    assert order.cost == pytest.approx(500.0)
    assert order.fee == pytest.approx(0.5)
    assert order.fee_currency == "USDT"
    assert order.timestamp is not None
    assert "ORDER RESULT" in caplog.text


def test_create_order_is_never_retried_on_network_error(exchange_config, caplog):
    caplog.set_level(logging.ERROR, logger="tradebot")
    adapter, client, sleeps = build(exchange_config)
    client.fail_next("create_order", ccxt.RequestTimeout("jawaban hilang"))
    with pytest.raises(OrderStateUnknownError, match="mungkin sudah masuk"):
        adapter.create_order(
            "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="lost-1"
        )
    assert client.count("create_order") == 1
    assert sleeps == []
    assert "ORDER STATE UNKNOWN" in caplog.text
    assert "lost-1" in caplog.text


def test_stop_loss_limit_maps_to_limit_with_stop_price(exchange_config):
    """Lapis 2: interface stop di exchange sudah ada sekarang, dipakai tahap 8."""
    adapter, client, _ = build(exchange_config)
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
    assert args[5]["stopLossPrice"] == 47_500.0
    assert order.type is OrderType.STOP_LOSS_LIMIT
    assert order.stop_price == 47_500.0
    assert order.is_open


@pytest.mark.parametrize(
    ("order_type", "amount", "price", "stop_price"),
    [
        (OrderType.MARKET, 0.01, 50_000.0, None),
        (OrderType.MARKET, 0.01, None, 49_000.0),
        (OrderType.MARKET, 0.0, None, None),
        (OrderType.MARKET, -1.0, None, None),
        (OrderType.LIMIT, 0.01, None, None),
        (OrderType.LIMIT, 0.01, 50_000.0, 49_000.0),
        (OrderType.STOP_LOSS_LIMIT, 0.01, 47_000.0, None),
        (OrderType.STOP_LOSS_LIMIT, 0.01, None, 47_500.0),
    ],
)
def test_invalid_order_requests_fail_locally(
    exchange_config, order_type, amount, price, stop_price
):
    adapter, client, _ = build(exchange_config)
    with pytest.raises(InvalidOrderError):
        adapter.create_order(
            "BTC/USDT", OrderSide.BUY, order_type, amount, price=price, stop_price=stop_price
        )
    assert client.count("create_order") == 0


def test_cancel_order_maps_not_found(exchange_config):
    adapter, _, _ = build(exchange_config)
    with pytest.raises(OrderNotFoundError):
        adapter.cancel_order("999", "BTC/USDT")


def test_cancel_order_returns_canceled_order(exchange_config):
    adapter, _, _ = build(exchange_config)
    placed = adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    canceled = adapter.cancel_order(placed.id, "BTC/USDT")
    assert canceled.id == placed.id
    assert canceled.status is OrderStatus.CANCELED
    assert adapter.fetch_open_orders("BTC/USDT") == []


def test_cancel_all_orders_is_a_noop_when_nothing_is_open(exchange_config):
    adapter, client, _ = build(exchange_config)
    assert adapter.cancel_all_orders("BTC/USDT") == []
    assert client.count("cancel_all_orders") == 0


def test_cancel_all_orders_cancels_and_verifies(exchange_config, caplog):
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, _ = build(exchange_config)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.02, price=41_000.0)
    canceled = adapter.cancel_all_orders("BTC/USDT")
    assert len(canceled) == 2
    assert all(order.status is OrderStatus.CANCELED for order in canceled)
    assert adapter.fetch_open_orders("BTC/USDT") == []
    assert "CANCEL ALL INTENT" in caplog.text
    assert "CANCEL ALL RESULT" in caplog.text


def test_cancel_all_orders_falls_back_to_per_order_cancel(exchange_config):
    adapter, client, _ = build(exchange_config)
    client.has["cancelAllOrders"] = False
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.02, price=41_000.0)
    canceled = adapter.cancel_all_orders("BTC/USDT")
    assert len(canceled) == 2
    assert client.count("cancel_all_orders") == 0
    assert client.count("cancel_order") == 2


def test_cancel_all_orders_raises_if_orders_remain(exchange_config):
    adapter, client, _ = build(exchange_config)
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    client.cancel_all_orders = (
        lambda symbol=None, params=None: []
    )  # exchange diam-diam tidak membatalkan
    with pytest.raises(FatalExchangeError, match="masih terbuka"):
        adapter.cancel_all_orders("BTC/USDT")


def test_fetch_order_by_client_order_id(exchange_config):
    adapter, client, _ = build(exchange_config)
    placed = adapter.create_order(
        "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0, client_order_id="find-me"
    )
    found = adapter.fetch_order("BTC/USDT", client_order_id="find-me")
    args, _ = client.last_call("fetch_order")
    assert args[2]["clientOrderId"] == "find-me"
    assert found.id == placed.id
    by_id = adapter.fetch_order("BTC/USDT", order_id=placed.id)
    assert by_id.client_order_id == "find-me"


def test_fetch_order_requires_an_identifier(exchange_config):
    adapter, client, _ = build(exchange_config)
    with pytest.raises(InvalidOrderError):
        adapter.fetch_order("BTC/USDT")
    assert client.count("fetch_order") == 0


def test_adapter_never_logs_secrets(exchange_config, caplog):
    """Diperiksa pada log mentah, sebelum MaskingFormatter: adapter sendiri tidak boleh bocor."""
    caplog.set_level(logging.DEBUG, logger="tradebot")
    adapter, _, _ = build(exchange_config)
    adapter.fetch_balance()
    adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.01, client_order_id="x")
    assert KEY not in caplog.text
    assert SECRET not in caplog.text


def test_empty_order_from_exchange_becomes_order_not_found(exchange_config):
    """Hipotesis review #7: ccxt bisa mengembalikan order kosong, bukan exception."""
    adapter, client, _ = build(exchange_config)
    client.fetch_order = lambda id, symbol=None, params=None: {
        "id": None,
        "clientOrderId": None,
        "symbol": None,
        "side": None,
        "type": None,
        "status": None,
        "amount": None,
        "info": {},
    }
    with pytest.raises(OrderNotFoundError, match="999"):
        adapter.fetch_order("BTC/USDT", order_id="999")


def test_canceling_status_is_still_open_and_unknown_status_warns(exchange_config, caplog):
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, _ = build(exchange_config)
    placed = adapter.create_order("BTC/USDT", OrderSide.BUY, OrderType.LIMIT, 0.01, price=40_000.0)
    client.orders[placed.id]["status"] = "canceling"
    assert adapter.fetch_order("BTC/USDT", order_id=placed.id).status is OrderStatus.OPEN
    client.orders[placed.id]["status"] = "weird"
    assert adapter.fetch_order("BTC/USDT", order_id=placed.id).status is OrderStatus.UNKNOWN
    assert "weird" in caplog.text


def test_http_418_ban_is_fatal_and_reports_retry_after(exchange_config):
    """Hipotesis review #11: 418 adalah ban IP; mencoba ulang memperpanjang ban."""
    adapter, client, sleeps = build(exchange_config)
    client.last_response_headers = {"Retry-After": "120"}
    client.fail_next(
        "fetch_ticker", ccxt.DDoSProtection("binance GET https://x/api/v3/ticker 418 I'm a teapot")
    )
    with pytest.raises(FatalExchangeError, match="418.*120") as exc:
        adapter.fetch_ticker("BTC/USDT")
    assert not isinstance(exc.value, RetryableExchangeError)
    assert client.count("fetch_ticker") == 1
    assert sleeps == []


def test_http_429_is_still_retried(exchange_config):
    adapter, client, sleeps = build(exchange_config)
    client.fail_next(
        "fetch_ticker", ccxt.RateLimitExceeded("binance GET https://x 429 Too Many Requests")
    )
    adapter.fetch_ticker("BTC/USDT")
    assert client.count("fetch_ticker") == 2


def test_fetch_my_trades_maps_fee(exchange_config):
    adapter, client, _ = build(exchange_config)
    client.trades = [
        {
            "id": "t1",
            "order": "9",
            "symbol": "BTC/USDT",
            "side": "buy",
            "amount": 0.01,
            "price": 50_000.0,
            "cost": 500.0,
            "fee": {"cost": 0.5, "currency": "USDT"},
            "timestamp": BASE_MS,
        }
    ]
    trades = adapter.fetch_my_trades("BTC/USDT", since_ms=BASE_MS - 1000)
    assert len(trades) == 1
    assert trades[0].order_id == "9"
    assert trades[0].fee == 0.5 and trades[0].fee_currency == "USDT"
    assert trades[0].side is OrderSide.BUY
    args, _ = client.last_call("fetch_my_trades")
    assert args == ("BTC/USDT", BASE_MS - 1000, None)


def test_market_limits_expose_price_band(exchange_config):
    adapter, _, _ = build(exchange_config)
    assert adapter.fetch_market_limits("BTC/USDT").price_band_down == 0.2


def test_testnet_warns_when_stop_support_unknown(exchange_config, caplog):
    """Aturan orderTypes per mode: testnet (uang palsu) cukup peringatan."""
    caplog.set_level(logging.WARNING, logger="tradebot")
    adapter, client, _ = build(exchange_config, connect=False)
    client.markets["BTC/USDT"]["info"]["orderTypes"] = ["LIMIT", "MARKET"]
    adapter.connect()
    assert adapter.is_sandbox
    assert any(
        "STOP_LOSS_LIMIT" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records
    )


def test_mainnet_trading_refuses_when_stop_support_unknown(exchange_config):
    adapter, client, _ = build(
        exchange_config, sandbox=False, allow_mainnet_trading=True, connect=False
    )
    client.markets["BTC/USDT"]["info"]["orderTypes"] = ["LIMIT", "MARKET"]
    with pytest.raises(FatalExchangeError, match="STOP_LOSS_LIMIT"):
        adapter.connect()


# --------------------------------------------------------------------------- #
# Pengukuran jam: pemanasan, rtt terkecil, dan pemisahan "jam melenceng" dari
# "pengukuran tidak konklusif"
# --------------------------------------------------------------------------- #

from tradebot.exchange import ClockMeasurementError  # noqa: E402


def build_with_clock(exchange_config, clock_ms_values, server_ms=BASE_MS):
    """Adapter publik dengan jam yang membaca nilai berurutan (ms) untuk t0, t1 tiap sampel."""
    holder = {}

    def factory(params):
        client = FakeCcxtClient(params)
        client.server_time_ms = server_ms
        holder["client"] = client
        return client

    values = iter(clock_ms_values)
    adapter = CcxtAdapter(
        exchange_config,
        exchange_config.testnet,
        None,
        sandbox=True,
        client_factory=factory,
        clock=lambda: next(values) / 1000,
    )
    return adapter, holder


def samples(pairs):
    """[(t0_ms, t1_ms), ...] -> urutan nilai jam."""
    return [v for pair in pairs for v in pair]


def test_clock_good_network_good(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    good = [(BASE_MS - 50, BASE_MS + 50)] * 3  # rtt 100 ms, selisih 0
    adapter, holder = build_with_clock(exchange_config, samples(good))
    adapter.connect()
    assert adapter.server_offset_ms == 0
    assert holder["client"].count("fetch_time") == 4, "1 pemanasan + 3 sampel"
    assert "rtt terbaik 100 ms" in caplog.text and "3 sampel" in caplog.text
    assert "TIDAK KONKLUSIF" not in caplog.text and "melenceng" not in caplog.text


def test_clock_bad_network_good_is_reported_as_clock_drift(exchange_config):
    ahead = [(BASE_MS + 1950, BASE_MS + 2050)] * 3  # rtt 100 ms, lokal 2000 ms di depan
    adapter, holder = build_with_clock(exchange_config, samples(ahead))
    with pytest.raises(TimeDriftError) as exc:
        adapter.connect()
    message = str(exc.value)
    assert "jam lokal melenceng +2000 ms" in message
    assert "rtt terbaik 100 ms" in message and "selisih di sampel itu +2000 ms" in message
    assert "3 sampel" in message
    assert holder["client"].count("load_markets") == 0


def test_clock_good_network_bad_is_inconclusive_not_drift(exchange_config, caplog):
    """Data dari mesin pengembang: rtt 3207 ms, selisih -1612 ms. Ketidakpastian rtt/2
    = 1600 ms > batas 1000 ms, jadi tidak bisa memutuskan; batas atas selisih 3212 ms
    masih di bawah recv_window 5000 ms, jadi lanjut dengan peringatan."""
    caplog.set_level(logging.INFO, logger="tradebot")
    slow = [(BASE_MS - 3212, BASE_MS - 12)] * 3  # rtt 3200 ms, titik tengah -1612 ms
    adapter, holder = build_with_clock(exchange_config, samples(slow))
    adapter.connect()  # tidak melempar
    assert holder["client"].count("load_markets") == 1
    assert "TIDAK KONKLUSIF" in caplog.text
    assert "bukan jam yang salah" in caplog.text
    assert "rtt terbaik 3200 ms" in caplog.text and "-1612 ms" in caplog.text
    assert "melenceng" not in caplog.text


def test_clock_good_network_terrible_stops_blaming_the_measurement(exchange_config):
    terrible = [(BASE_MS - 5000, BASE_MS + 3000)] * 3  # rtt 8000, titik tengah -1000
    adapter, holder = build_with_clock(exchange_config, samples(terrible))
    with pytest.raises(ClockMeasurementError) as exc:
        adapter.connect()
    message = str(exc.value)
    assert "pengukuran jam" in message and "bukan jam Anda" in message
    assert "rtt terbaik 8000 ms" in message and "recv_window 5000 ms" in message
    assert "melenceng" not in message
    assert not isinstance(exc.value, TimeDriftError)
    assert holder["client"].count("load_markets") == 0


def test_clock_uses_sample_with_smallest_rtt_not_the_average(exchange_config, caplog):
    caplog.set_level(logging.INFO, logger="tradebot")
    mixed = [
        (BASE_MS - 3212, BASE_MS - 12),  # dingin: rtt 3200, selisih -1612
        (BASE_MS - 50, BASE_MS + 50),  # hangat: rtt 100, selisih 0
        (BASE_MS - 230, BASE_MS + 270),  # rtt 500, selisih +20
    ]
    adapter, _ = build_with_clock(exchange_config, samples(mixed))
    adapter.connect()
    assert adapter.server_offset_ms == 0, "sampel rtt terkecil, bukan rata-rata (-531 ms)"
    assert "rtt terbaik 100 ms" in caplog.text
    assert "TIDAK KONKLUSIF" not in caplog.text
