"""TokocryptoAdapter: Tokocrypto mainnet lewat ccxt, dengan penanganan khusus venue.

Perbedaan dari Binance yang ditangani di sini, semuanya terverifikasi pada
ccxt 4.5.78 dan dokumentasi resmi Tokocrypto (September 2026):

- Host data pasar. ccxt merutekan ticker, OHLCV, order book, dan trades untuk
  simbol jenis MBX (type 1, termasuk BTC/USDT) ke api.binance.com. Host resmi
  Tokocrypto untuk data itu adalah www.tokocrypto.site; dari config
  exchange.live.market_data_url dan dipasang untuk adapter publik maupun berkunci.
- Tidak ada testnet. sandbox=True ditolak.
- Batas minimum notional tidak dibaca ccxt (filter bernama NOTIONAL, ccxt
  mencari MIN_NOTIONAL). Dibaca dari filter mentah.
- Market buy memakai quoteOrderQty (jumlah quote), bukan jumlah base. Adapter
  mengonversi amount base ke quote memakai harga ask terakhir.
- Stop order lewat parameter stopPrice; ccxt mengubah limit menjadi
  STOP_LOSS_LIMIT (type 4). Parameter stopLossPrice gaya Binance tidak dikenal.
- client id dikirim sebagai clientId; server tidak memeriksa keunikannya.
  cancel_order dan fetch_order ccxt hanya memakai orderId, jadi pencarian
  lewat client id memindai open orders lalu riwayat order.
- Tidak ada cancelAllOrders; dibatalkan satu per satu.
- Pair pernah dipindah mesin dan order terbukanya dibatalkan tanpa aksi bot
  (migrasi IDR November 2025). Karena itu pair divalidasi keras saat connect:
  ada, aktif, jenis MBX, spot diizinkan, dan mengiklankan tipe order yang
  dibutuhkan lapis 1 dan lapis 2.
"""

from __future__ import annotations

import logging
from typing import Any

from tradebot.exchange.base import Order, OrderSide, OrderType
from tradebot.exchange.ccxt_base import CcxtBase, float_or_none
from tradebot.exchange.errors import FatalExchangeError, OrderNotFoundError

log = logging.getLogger(__name__)

REQUIRED_ORDER_TYPES = ("MARKET", "LIMIT", "STOP_LOSS_LIMIT")
MBX_MARKET_TYPE = 1
_STOP_TYPE_CODES = {3, 4, 5, 6}  # STOP_LOSS, STOP_LOSS_LIMIT, TAKE_PROFIT, TAKE_PROFIT_LIMIT
ORDER_HISTORY_LIMIT = 200


class TokocryptoAdapter(CcxtBase):
    def __init__(self, *args: Any, sandbox: bool = False, **kwargs: Any) -> None:
        if sandbox:
            raise FatalExchangeError(
                "Tokocrypto tidak punya testnet. Mode testnet memakai Binance Spot Testnet."
            )
        super().__init__(*args, sandbox=False, **kwargs)

    # ------------------------------------------------------------------ #
    # Klien
    # ------------------------------------------------------------------ #

    def _configure_client(self) -> None:
        url = self._venue.market_data_url
        if not url:
            log.warning(
                "%s: market_data_url kosong; ccxt akan memakai api.binance.com untuk data pasar, "
                "yang bukan host resmi Tokocrypto dan diblokir di jaringan ini",
                self.name,
            )
            return
        urls = getattr(self._client, "urls", None)
        rest = (urls or {}).get("api", {}).get("rest") if isinstance(urls, dict) else None
        if not isinstance(rest, dict) or "binance" not in rest:
            raise FatalExchangeError(
                f"{self.name}: struktur urls klien ccxt tokocrypto berubah, "
                f"tidak menemukan urls['api']['rest']['binance'] untuk diganti ke {url}"
            )
        rest["binance"] = url
        log.info("data pasar Tokocrypto lewat host resmi %s", url)

    # ------------------------------------------------------------------ #
    # Validasi pair saat connect
    # ------------------------------------------------------------------ #

    def _validate_market(self, market: dict[str, Any]) -> None:
        symbol = self._exchange.symbol
        info = market.get("info") or {}
        market_type = info.get("type")
        if market_type is not None and int(market_type) != MBX_MARKET_TYPE:
            raise FatalExchangeError(
                f"pair {symbol!r} berjenis {market_type} di Tokocrypto; adapter ini hanya "
                f"mendukung jenis MBX ({MBX_MARKET_TYPE}) yang datanya di "
                f"{self._venue.market_data_url}"
            )
        if info.get("spotTradingEnable") is False:
            raise FatalExchangeError(f"pair {symbol!r}: spot trading dimatikan oleh Tokocrypto")
        advertised = {str(t).upper() for t in (info.get("orderTypes") or [])}
        if advertised:
            missing = [t for t in REQUIRED_ORDER_TYPES if t not in advertised]
            if missing:
                raise FatalExchangeError(
                    f"pair {symbol!r} tidak mengiklankan tipe order {missing} yang dibutuhkan "
                    f"bot (tersedia: {sorted(advertised)})"
                )
        log.info(
            "pair %s: id=%s type=%s orderTypes=%s",
            symbol,
            market.get("id"),
            market_type,
            sorted(advertised) if advertised else "tidak dilaporkan",
        )

    # ------------------------------------------------------------------ #
    # Batas pasar
    # ------------------------------------------------------------------ #

    def _min_cost_from_market(self, market: dict[str, Any]) -> float | None:
        base = super()._min_cost_from_market(market)
        if base is not None:
            return base
        for item in (market.get("info") or {}).get("filters") or []:
            if str(item.get("filterType", "")).upper() in ("NOTIONAL", "MIN_NOTIONAL"):
                return float_or_none(item.get("minNotional"))
        return None

    # ------------------------------------------------------------------ #
    # Order
    # ------------------------------------------------------------------ #

    def _order_request(
        self,
        order_type: OrderType,
        side: OrderSide,
        amount: float,
        price: float | None,
        stop_price: float | None,
        client_order_id: str | None,
    ) -> tuple[str, float | None, dict[str, Any]]:
        params: dict[str, Any] = {}
        if client_order_id:
            params["clientId"] = client_order_id
        if order_type is OrderType.MARKET:
            if side is OrderSide.BUY:
                # Tokocrypto menerima market buy hanya dalam jumlah quote (quoteOrderQty).
                ticker = self.fetch_ticker(self._exchange.symbol)
                reference = ticker.ask or ticker.last
                params["cost"] = amount * reference
                log.info(
                    "market buy dikonversi ke quote: amount=%s x ask=%s = cost=%s %s",
                    amount,
                    reference,
                    params["cost"],
                    self._exchange.quote,
                )
            return "market", None, params
        if order_type is OrderType.STOP_LOSS_LIMIT:
            params["stopPrice"] = stop_price
        return "limit", price, params

    def _order_type_from_raw(self, raw: dict[str, Any]) -> OrderType | None:
        info = raw.get("info") or {}
        code = info.get("type")
        try:
            code_int = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_int = None
        if code_int in _STOP_TYPE_CODES:
            return OrderType.STOP_LOSS_LIMIT
        if code_int == 2:
            return OrderType.MARKET
        if code_int in (1, 7):
            return OrderType.LIMIT
        return super()._order_type_from_raw(raw)

    def _fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> Order:
        for order in self.fetch_open_orders(symbol):
            if order.client_order_id == client_order_id:
                return order
        raw_history = self._call(
            "fetch_orders", self._client.fetch_orders, symbol, None, ORDER_HISTORY_LIMIT
        )
        for item in raw_history:
            order = self._parse_order(item)
            if order.client_order_id == client_order_id:
                return order
        raise OrderNotFoundError(
            f"order dengan client id {client_order_id!r} tidak ditemukan di {symbol} "
            f"(open orders dan {ORDER_HISTORY_LIMIT} order terakhir)"
        )
