"""CcxtAdapter: Binance Spot lewat ccxt, dipakai untuk Binance Spot Testnet.

Satu kode untuk testnet (set_sandbox_mode) maupun mainnet Binance. Mainnet
Binance bukan lagi venue live project ini karena api.binance.com diblokir ISP,
tapi jalurnya tetap ada dan tetap dijaga gerbang mainnet yang sama, kalau suatu
saat config exchange.live.id diisi binance lagi.
"""

from __future__ import annotations

import logging
from typing import Any

from tradebot.exchange.base import Order, OrderSide, OrderType
from tradebot.exchange.ccxt_base import CcxtBase
from tradebot.exchange.errors import FatalExchangeError

log = logging.getLogger(__name__)


class CcxtAdapter(CcxtBase):
    def _extra_options(self) -> dict[str, Any]:
        # Hanya pasar spot: tidak menyentuh endpoint futures sama sekali.
        return {"fetchMarkets": {"types": ["spot"], "loadAllOptions": False}}

    def _configure_client(self) -> None:
        """Adapter publik mainnet boleh memakai endpoint data pasar alternatif dari config."""
        url = self._venue.market_data_url
        if self._sandbox or self._credentials is not None or not url:
            return
        urls = getattr(self._client, "urls", None)
        if not isinstance(urls, dict) or not isinstance(urls.get("api"), dict):
            raise FatalExchangeError(
                f"{self.name}: klien ccxt tidak punya urls['api'], tidak bisa memakai {url}"
            )
        urls["api"]["public"] = url
        log.info("data pasar publik lewat %s", url)

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
            params["clientOrderId"] = client_order_id
        ccxt_type = "market" if order_type is OrderType.MARKET else "limit"
        if order_type is OrderType.STOP_LOSS_LIMIT:
            params["stopLossPrice"] = stop_price
        return ccxt_type, price, params

    def _fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> Order:
        raw = self._call(
            "fetch_order",
            self._client.fetch_order,
            "",
            symbol,
            {"clientOrderId": client_order_id},
        )
        return self._parse_order(raw)
