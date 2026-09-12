"""Satu-satunya tempat adapter dibuat dari Settings.

mode testnet  venue exchange.testnet, sandbox, kunci testnet
mode paper    venue exchange.live, tanpa kunci, hanya data (dibungkus PaperAdapter di tahap 7)
mode live     venue exchange.live, kunci live, allow_mainnet_trading=True

build_public_adapter mengabaikan mode: selalu venue exchange.live tanpa kunci.
Dipakai fetch-data, karena data historis selalu dari venue live (Tokocrypto),
bukan dari testnet yang harganya menyimpang dari pasar asli.

Ini satu-satunya kode yang pernah memberikan allow_mainnet_trading=True, dan
hanya ketika settings.mode adalah LIVE, yang sendiri hanya lahir dari
TRADING_MODE=live plus flag --i-know-what-im-doing.
"""

from __future__ import annotations

from typing import Any

from tradebot.config import Settings, TradingMode
from tradebot.exchange.base import ExchangeAdapter
from tradebot.exchange.ccxt_adapter import CcxtAdapter
from tradebot.exchange.ccxt_base import CcxtBase
from tradebot.exchange.errors import FatalExchangeError
from tradebot.exchange.tokocrypto_adapter import TokocryptoAdapter

ADAPTERS: dict[str, type[CcxtBase]] = {
    "binance": CcxtAdapter,
    "tokocrypto": TokocryptoAdapter,
}


def adapter_class(venue_id: str) -> type[CcxtBase]:
    try:
        return ADAPTERS[venue_id]
    except KeyError:
        raise FatalExchangeError(
            f"venue {venue_id!r} tidak punya adapter. Tersedia: {sorted(ADAPTERS)}"
        ) from None


def build_public_adapter(settings: Settings, **kwargs: Any) -> ExchangeAdapter:
    """Adapter data publik venue live, tanpa kunci, apa pun mode-nya. Tidak bisa order."""
    exchange = settings.exchange
    cls = adapter_class(exchange.live.id)
    return cls(exchange, exchange.live, None, sandbox=False, **kwargs)


def build_adapter(settings: Settings, **kwargs: Any) -> ExchangeAdapter:
    mode = settings.mode
    exchange = settings.exchange
    if mode is TradingMode.TESTNET:
        cls = adapter_class(exchange.testnet.id)
        return cls(exchange, exchange.testnet, settings.credentials, sandbox=True, **kwargs)
    if mode is TradingMode.PAPER:
        return build_public_adapter(settings, **kwargs)
    if mode is TradingMode.LIVE:
        cls = adapter_class(exchange.live.id)
        return cls(
            exchange,
            exchange.live,
            settings.credentials,
            sandbox=False,
            allow_mainnet_trading=True,
            **kwargs,
        )
    raise FatalExchangeError(f"mode tidak dikenal: {mode!r}")
