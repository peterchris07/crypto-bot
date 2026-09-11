"""CcxtAdapter: satu kode untuk Binance Spot Testnet dan mainnet.

Tiga wujud, ditentukan murni dari Settings.mode:

  paper    tanpa kunci, mainnet, hanya data publik (harga asli untuk PaperAdapter)
  testnet  kunci testnet, set_sandbox_mode(True)
  live     kunci mainnet, hanya lewat from_settings dengan mode live yang sah

Gerbang mainnet: klien mainnet DENGAN kunci hanya bisa dibuat kalau
allow_mainnet_trading=True, dan satu-satunya kode yang memberikannya adalah
from_settings saat mode LIVE. Mode LIVE sendiri hanya bisa lahir dari
resolve_mode dengan TRADING_MODE=live plus flag CLI. Jadi ada dua kunci
berurutan sebelum satu pun order bisa menyentuh uang asli.

Retry: hanya untuk gangguan sementara, dengan backoff eksponensial dari config.
create_order tidak pernah dicoba ulang: kalau jawabannya hilang di jaringan,
order mungkin sudah masuk, dan mengirim ulang berarti posisi ganda.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import ccxt
import pandas as pd

from tradebot.config import Credentials, ExchangeConfig, Settings, TradingMode
from tradebot.data.ohlcv import frame_from_rows
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
)
from tradebot.exchange.errors import (
    AuthenticationError,
    ExchangeError,
    FatalExchangeError,
    InsufficientFundsError,
    InvalidOrderError,
    MainnetRefusedError,
    OrderNotFoundError,
    OrderStateUnknownError,
    RetryableExchangeError,
    TimeDriftError,
)

log = logging.getLogger(__name__)

ClientFactory = Callable[[dict[str, Any]], Any]

_STATUS_MAP = {
    "open": OrderStatus.OPEN,
    "closed": OrderStatus.CLOSED,
    "canceled": OrderStatus.CANCELED,
    "cancelled": OrderStatus.CANCELED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}

_TIME_DRIFT_HINTS = ("-1021", "recvwindow", "ahead of the server", "timestamp for this request")
_AUTH_HINTS = ("-2015", "api-key", "invalid api", "signature", "-2014", "-1022")
_ORDER_GONE_HINTS = ("-2011", "unknown order")


def _default_client_factory(exchange_id: str) -> ClientFactory:
    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise FatalExchangeError(f"exchange {exchange_id!r} tidak dikenal oleh ccxt")
    return exchange_cls


def _to_datetime(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CcxtAdapter(ExchangeAdapter):
    def __init__(
        self,
        exchange: ExchangeConfig,
        credentials: Credentials | None,
        *,
        sandbox: bool,
        allow_mainnet_trading: bool = False,
        client_factory: ClientFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if credentials is not None and not sandbox and not allow_mainnet_trading:
            raise MainnetRefusedError(
                "Klien mainnet dengan kunci API ditolak: adapter dibuat tanpa "
                "allow_mainnet_trading. "
                "Jalur yang sah hanya CcxtAdapter.from_settings dengan mode live, yang butuh "
                "TRADING_MODE=live dan flag --i-know-what-im-doing."
            )
        self._config = exchange
        self._credentials = credentials
        self._sandbox = sandbox
        self._sleep = sleep
        self._clock = clock
        self._connected = False
        self._server_offset_ms = 0.0

        venue = "testnet" if sandbox else "mainnet"
        role = "trading" if credentials is not None else "public"
        self.name = f"{exchange.id}-{venue}-{role}"

        params: dict[str, Any] = {
            "enableRateLimit": exchange.rate_limit,
            "options": {
                "defaultType": "spot",
                "recvWindow": exchange.recv_window_ms,
                "adjustForTimeDifference": False,
                # Hanya pasar spot: tidak menyentuh endpoint futures sama sekali.
                "fetchMarkets": {"types": ["spot"], "loadAllOptions": False},
            },
        }
        if credentials is not None:
            params["apiKey"] = credentials.api_key
            params["secret"] = credentials.api_secret
        factory = client_factory or _default_client_factory(exchange.id)
        self._client = factory(params)
        if sandbox:
            self._client.set_sandbox_mode(True)
        elif credentials is None and exchange.public_market_data_url:
            self._use_public_market_data_url(exchange.public_market_data_url)

        log.info(
            "adapter %s dibuat (rate_limit=%s recv_window=%dms)",
            self.name,
            exchange.rate_limit,
            exchange.recv_window_ms,
        )
        if credentials is not None and not sandbox:
            log.warning("klien MAINNET berkunci dibuat: order dari adapter ini memakai uang asli")

    def _use_public_market_data_url(self, url: str) -> None:
        """Arahkan endpoint publik ke URL data pasar resmi. Hanya untuk adapter tanpa kunci."""
        urls = getattr(self._client, "urls", None)
        if not isinstance(urls, dict) or not isinstance(urls.get("api"), dict):
            raise FatalExchangeError(
                f"{self.name}: klien ccxt tidak punya urls['api'], tidak bisa memakai {url}"
            )
        urls["api"]["public"] = url
        log.info("data pasar publik lewat %s", url)

    # ------------------------------------------------------------------ #
    # Konstruksi dari Settings
    # ------------------------------------------------------------------ #

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> CcxtAdapter:
        mode = settings.mode
        if mode is TradingMode.PAPER:
            return cls(settings.exchange, None, sandbox=False, **kwargs)
        if mode is TradingMode.TESTNET:
            return cls(settings.exchange, settings.credentials, sandbox=True, **kwargs)
        if mode is TradingMode.LIVE:
            return cls(
                settings.exchange,
                settings.credentials,
                sandbox=False,
                allow_mainnet_trading=True,
                **kwargs,
            )
        raise FatalExchangeError(f"mode tidak dikenal: {mode!r}")

    # ------------------------------------------------------------------ #
    # Properti
    # ------------------------------------------------------------------ #

    @property
    def can_trade(self) -> bool:
        return self._credentials is not None

    @property
    def is_sandbox(self) -> bool:
        return self._sandbox

    @property
    def server_offset_ms(self) -> float:
        """lokal - server, diukur saat connect()."""
        return self._server_offset_ms

    # ------------------------------------------------------------------ #
    # Retry dan terjemahan error
    # ------------------------------------------------------------------ #

    @staticmethod
    def _translate(exc: ccxt.BaseError) -> ExchangeError:
        text = str(exc)
        lowered = text.lower()
        if isinstance(exc, ccxt.InvalidNonce) or any(h in lowered for h in _TIME_DRIFT_HINTS):
            return TimeDriftError(
                f"exchange menolak timestamp request: {text}. "
                "Jam lokal melenceng dari jam server; sinkronkan jam sistem."
            )
        if isinstance(exc, ccxt.AuthenticationError):
            return AuthenticationError(
                f"autentikasi ditolak: {text}. Cek kunci, secret, IP whitelist, dan izin kunci."
            )
        if isinstance(exc, ccxt.InsufficientFunds):
            return InsufficientFundsError(f"saldo tidak cukup: {text}")
        if isinstance(exc, ccxt.OrderNotFound):
            return OrderNotFoundError(f"order tidak ditemukan: {text}")
        if isinstance(exc, ccxt.InvalidOrder):
            return InvalidOrderError(f"order ditolak exchange: {text}")
        if isinstance(exc, ccxt.OperationRejected):
            if any(h in lowered for h in _AUTH_HINTS):
                return AuthenticationError(
                    f"exchange menolak kunci: {text}. Cek kunci, IP whitelist, dan izin kunci."
                )
            if any(h in lowered for h in _ORDER_GONE_HINTS):
                return OrderNotFoundError(f"order sudah tidak ada: {text}")
            return FatalExchangeError(f"operasi ditolak exchange: {text}")
        if isinstance(exc, ccxt.NetworkError):
            return RetryableExchangeError(f"gangguan jaringan/exchange: {text}")
        return FatalExchangeError(f"error exchange: {type(exc).__name__}: {text}")

    def _call(
        self, name: str, fn: Callable[..., Any], *args: Any, retry: bool = True, **kwargs: Any
    ) -> Any:
        retry_cfg = self._config.retry
        max_attempts = retry_cfg.max_attempts if retry else 1
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn(*args, **kwargs)
            except ccxt.BaseError as exc:
                translated = self._translate(exc)
                if not isinstance(translated, RetryableExchangeError):
                    log.error("%s gagal (tidak dicoba ulang): %s", name, translated)
                    raise translated from exc
                if attempt >= max_attempts:
                    log.error("%s gagal setelah %d percobaan: %s", name, attempt, translated)
                    raise translated from exc
                delay = min(
                    retry_cfg.base_delay_seconds * (2 ** (attempt - 1)),
                    retry_cfg.max_delay_seconds,
                )
                log.warning(
                    "%s gagal (percobaan %d/%d), coba lagi dalam %.2fs: %s",
                    name,
                    attempt,
                    max_attempts,
                    delay,
                    translated,
                )
                self._sleep(delay)

    def _require_connected(self) -> None:
        if not self._connected:
            raise FatalExchangeError(f"{self.name}: panggil connect() dulu sebelum method lain")

    def _require_trading(self, action: str) -> None:
        if self._credentials is None:
            raise FatalExchangeError(
                f"{self.name}: {action} butuh kunci API, adapter ini hanya untuk data publik"
            )

    # ------------------------------------------------------------------ #
    # Koneksi dan waktu
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        t0 = self._clock()
        server_ms = self.fetch_server_time_ms()
        t1 = self._clock()
        rtt_ms = (t1 - t0) * 1000
        local_mid_ms = (t0 + t1) / 2 * 1000
        drift_ms = local_mid_ms - server_ms
        self._server_offset_ms = drift_ms
        limit = self._config.max_time_drift_ms
        log.info(
            "jam: lokal - server = %+.0f ms (batas %d ms, rtt %.0f ms)", drift_ms, limit, rtt_ms
        )
        if abs(drift_ms) > limit:
            raise TimeDriftError(
                f"jam lokal melenceng {drift_ms:+.0f} ms dari jam server {self._config.id} "
                f"(batas {limit} ms). Sinkronkan jam sistem, lalu jalankan lagi."
            )
        self._call("load_markets", self._client.load_markets)
        self._connected = True
        log.info("terhubung ke %s", self.name)

    def fetch_server_time_ms(self) -> int:
        return int(self._call("fetch_time", self._client.fetch_time))

    # ------------------------------------------------------------------ #
    # Data publik
    # ------------------------------------------------------------------ #

    def fetch_market_limits(self, symbol: str) -> MarketLimits:
        self._require_connected()
        try:
            market = self._client.market(symbol)
        except ccxt.BaseError as exc:
            raise FatalExchangeError(
                f"pasangan {symbol!r} tidak ada di {self.name}: {exc}"
            ) from exc
        limits = market.get("limits") or {}
        precision = market.get("precision") or {}
        return MarketLimits(
            symbol=symbol,
            base=str(market.get("base")),
            quote=str(market.get("quote")),
            min_amount=_float_or_none((limits.get("amount") or {}).get("min")),
            amount_step=_float_or_none(precision.get("amount")),
            min_cost=_float_or_none((limits.get("cost") or {}).get("min")),
            price_step=_float_or_none(precision.get("price")),
        )

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        *,
        since_ms: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        self._require_connected()
        rows: Sequence[Sequence[float]] = self._call(
            "fetch_ohlcv", self._client.fetch_ohlcv, symbol, timeframe, since_ms, limit
        )
        return frame_from_rows(rows)

    def fetch_ticker(self, symbol: str) -> Ticker:
        self._require_connected()
        raw = self._call("fetch_ticker", self._client.fetch_ticker, symbol)
        last = _float_or_none(raw.get("last"))
        if last is None:
            raise FatalExchangeError(f"ticker {symbol} tanpa harga last: {raw!r}")
        stamp = _to_datetime(raw.get("timestamp")) or datetime.now(tz=UTC)
        return Ticker(
            symbol=symbol,
            last=last,
            bid=_float_or_none(raw.get("bid")),
            ask=_float_or_none(raw.get("ask")),
            timestamp=stamp,
        )

    # ------------------------------------------------------------------ #
    # Akun dan order
    # ------------------------------------------------------------------ #

    def fetch_balance(self) -> Balance:
        self._require_connected()
        self._require_trading("fetch_balance")
        raw = self._call("fetch_balance", self._client.fetch_balance)
        free = raw.get("free") or {}
        used = raw.get("used") or {}
        total = raw.get("total") or {}
        assets = {
            asset: AssetBalance(
                free=float(free.get(asset) or 0.0),
                used=float(used.get(asset) or 0.0),
                total=float(total.get(asset) or 0.0),
            )
            for asset in total
        }
        return Balance(assets=assets)

    def _parse_order(self, raw: dict[str, Any], requested_type: OrderType | None = None) -> Order:
        raw_type = str(raw.get("type") or "").lower()
        if raw_type in ("stop_loss_limit", "stop_loss", "stop"):
            order_type = OrderType.STOP_LOSS_LIMIT
        elif raw_type in ("market", "limit"):
            order_type = OrderType(raw_type)
        else:
            order_type = requested_type or OrderType.LIMIT
        fee = raw.get("fee") or {}
        fee_cost = _float_or_none(fee.get("cost")) if isinstance(fee, dict) else None
        fee_currency = fee.get("currency") if isinstance(fee, dict) else None
        if fee_cost is None and isinstance(raw.get("fees"), list) and raw["fees"]:
            fees = [f for f in raw["fees"] if isinstance(f, dict) and f.get("cost") is not None]
            currencies = {f.get("currency") for f in fees}
            if fees and len(currencies) == 1:
                fee_cost = sum(float(f["cost"]) for f in fees)
                fee_currency = currencies.pop()
        stop_price = _float_or_none(raw.get("stopPrice"))
        if stop_price is None:
            stop_price = _float_or_none(raw.get("triggerPrice"))
        return Order(
            id=str(raw["id"]) if raw.get("id") is not None else None,
            client_order_id=raw.get("clientOrderId"),
            symbol=str(raw.get("symbol")),
            side=OrderSide(str(raw.get("side")).lower()),
            type=order_type,
            amount=float(raw.get("amount") or 0.0),
            price=_float_or_none(raw.get("price")),
            stop_price=stop_price,
            status=_STATUS_MAP.get(str(raw.get("status") or "").lower(), OrderStatus.UNKNOWN),
            filled=float(raw.get("filled") or 0.0),
            average=_float_or_none(raw.get("average")),
            cost=_float_or_none(raw.get("cost")),
            fee=fee_cost,
            fee_currency=fee_currency,
            timestamp=_to_datetime(raw.get("timestamp")),
        )

    @staticmethod
    def _validate_order_request(
        order_type: OrderType, amount: float, price: float | None, stop_price: float | None
    ) -> None:
        if not amount or amount <= 0:
            raise InvalidOrderError(f"amount harus > 0, dapat {amount!r}")
        if order_type is OrderType.MARKET:
            if price is not None or stop_price is not None:
                raise InvalidOrderError("order MARKET tidak boleh punya price atau stop_price")
        elif order_type is OrderType.LIMIT:
            if price is None or price <= 0:
                raise InvalidOrderError("order LIMIT butuh price > 0")
            if stop_price is not None:
                raise InvalidOrderError(
                    "order LIMIT tidak boleh punya stop_price; pakai STOP_LOSS_LIMIT"
                )
        elif order_type is OrderType.STOP_LOSS_LIMIT:
            if price is None or price <= 0 or stop_price is None or stop_price <= 0:
                raise InvalidOrderError("order STOP_LOSS_LIMIT butuh price > 0 dan stop_price > 0")

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
        self._require_connected()
        self._require_trading("create_order")
        self._validate_order_request(order_type, amount, price, stop_price)

        params: dict[str, Any] = {}
        if client_order_id:
            params["clientOrderId"] = client_order_id
        ccxt_type = "market" if order_type is OrderType.MARKET else "limit"
        if order_type is OrderType.STOP_LOSS_LIMIT:
            params["stopLossPrice"] = stop_price

        # Aturan keras 5: niat order tercatat SEBELUM request keluar.
        log.info(
            "ORDER INTENT venue=%s symbol=%s side=%s type=%s amount=%s price=%s stop=%s "
            "client_order_id=%s",
            self.name,
            symbol,
            side.value,
            order_type.value,
            amount,
            price,
            stop_price,
            client_order_id,
        )
        try:
            raw = self._call(
                "create_order",
                self._client.create_order,
                symbol,
                ccxt_type,
                side.value,
                amount,
                price,
                params,
                retry=False,
            )
        except RetryableExchangeError as exc:
            # Request mungkin sudah sampai. Dilarang kirim ulang. Pemanggil harus
            # rekonsiliasi lewat client_order_id.
            log.error(
                "ORDER STATE UNKNOWN client_order_id=%s: jawaban tidak sampai (%s)",
                client_order_id,
                exc,
            )
            raise OrderStateUnknownError(
                f"order {client_order_id or '(tanpa client id)'} mungkin sudah masuk: {exc}"
            ) from exc
        order = self._parse_order(raw, requested_type=order_type)
        log.info(
            "ORDER RESULT id=%s client_order_id=%s status=%s filled=%s average=%s cost=%s "
            "fee=%s %s",
            order.id,
            order.client_order_id,
            order.status.value,
            order.filled,
            order.average,
            order.cost,
            order.fee,
            order.fee_currency or "",
        )
        return order

    def cancel_order(self, order_id: str, symbol: str) -> Order:
        self._require_connected()
        self._require_trading("cancel_order")
        log.info("CANCEL INTENT venue=%s symbol=%s order_id=%s", self.name, symbol, order_id)
        raw = self._call("cancel_order", self._client.cancel_order, order_id, symbol)
        order = self._parse_order(raw)
        log.info("CANCEL RESULT order_id=%s status=%s", order.id, order.status.value)
        return order

    def cancel_all_orders(self, symbol: str) -> list[Order]:
        self._require_connected()
        self._require_trading("cancel_all_orders")
        open_orders = self.fetch_open_orders(symbol)
        if not open_orders:
            log.info("cancel_all_orders %s: tidak ada order terbuka", symbol)
            return []
        log.warning(
            "CANCEL ALL INTENT venue=%s symbol=%s jumlah=%d ids=%s",
            self.name,
            symbol,
            len(open_orders),
            [o.id for o in open_orders],
        )
        canceled: list[Order] = []
        if (getattr(self._client, "has", {}) or {}).get("cancelAllOrders"):
            try:
                raw = self._call("cancel_all_orders", self._client.cancel_all_orders, symbol)
            except OrderNotFoundError:
                raw = []
            if isinstance(raw, list):
                canceled = [self._parse_order(item) for item in raw if isinstance(item, dict)]
        else:
            for order in open_orders:
                if order.id is None:
                    continue
                try:
                    canceled.append(self.cancel_order(order.id, symbol))
                except OrderNotFoundError:
                    log.info("order %s sudah tidak ada saat dibatalkan", order.id)
        remaining = self.fetch_open_orders(symbol)
        if remaining:
            raise FatalExchangeError(
                f"{len(remaining)} order masih terbuka setelah cancel_all_orders: "
                f"{[o.id for o in remaining]}"
            )
        log.warning("CANCEL ALL RESULT symbol=%s dibatalkan=%d", symbol, len(open_orders))
        return canceled or [
            Order(**{**o.__dict__, "status": OrderStatus.CANCELED}) for o in open_orders
        ]

    def fetch_open_orders(self, symbol: str) -> list[Order]:
        self._require_connected()
        self._require_trading("fetch_open_orders")
        raw = self._call("fetch_open_orders", self._client.fetch_open_orders, symbol)
        return [self._parse_order(item) for item in raw]

    def fetch_order(
        self,
        symbol: str,
        *,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        self._require_connected()
        self._require_trading("fetch_order")
        if not order_id and not client_order_id:
            raise InvalidOrderError("fetch_order butuh order_id atau client_order_id")
        params: dict[str, Any] = {}
        if client_order_id:
            params["clientOrderId"] = client_order_id
        raw = self._call("fetch_order", self._client.fetch_order, order_id or "", symbol, params)
        return self._parse_order(raw)
