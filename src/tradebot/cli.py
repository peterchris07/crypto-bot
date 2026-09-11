"""Titik masuk command line.

Perintah yang ada sejauh ini: check-config (tahap 1) dan check-exchange
(tahap 2). fetch-data, backtest, dan run ditambahkan di tahap berikutnya.
Flag --i-know-what-im-doing hanya ada pada perintah yang bisa menyentuh
exchange dengan kunci; keberadaannya tidak pernah cukup sendiri,
TRADING_MODE=live juga harus ada.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from tradebot import __version__
from tradebot.config import (
    DEFAULT_CONFIG_PATH,
    LIVE_FLAG,
    ConfigError,
    Settings,
    describe,
    load_settings,
)
from tradebot.logging_setup import log_startup_banner, setup_logging

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_EXCHANGE_ERROR = 3


def _add_live_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        LIVE_FLAG,
        dest="i_know_what_im_doing",
        action="store_true",
        help="Syarat kedua mode live. Hanya berlaku bersama TRADING_MODE=live di .env.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradebot",
        description="Bot trading crypto spot Binance. Default mode: paper.",
    )
    parser.add_argument("--version", action="version", version=f"tradebot {__version__}")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path file YAML konfigurasi (default: config/default.yaml).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check-config",
        help="Muat config dan .env, tampilkan mode serta parameter. Tidak menyentuh exchange.",
    )
    _add_live_flag(check)

    check_exchange = sub.add_parser(
        "check-exchange",
        help=(
            "Hubungkan ke exchange sesuai mode: cek jam, pasar, harga, saldo, order terbuka. "
            "Tidak pernah mengirim order."
        ),
    )
    _add_live_flag(check_exchange)
    return parser


def _check_exchange(settings: Settings) -> int:
    from tradebot.exchange.ccxt_adapter import CcxtAdapter
    from tradebot.exchange.errors import ExchangeError

    symbol = settings.exchange.symbol
    timeframe = settings.exchange.timeframe
    try:
        adapter = CcxtAdapter.from_settings(settings)
        adapter.connect()
        limits = adapter.fetch_market_limits(symbol)
        ticker = adapter.fetch_ticker(symbol)
        bars = adapter.fetch_ohlcv(symbol, timeframe, limit=3)
        print(f"venue: {adapter.name}")
        print(f"jam: lokal - server = {adapter.server_offset_ms:+.0f} ms")
        print(
            f"pasar {symbol}: min_amount={limits.min_amount} {limits.base} "
            f"amount_step={limits.amount_step} min_cost={limits.min_cost} {limits.quote} "
            f"price_step={limits.price_step}"
        )
        print(f"harga terakhir: {ticker.last} (bid {ticker.bid} / ask {ticker.ask})")
        if len(bars):
            last = bars.iloc[-1]
            print(
                f"bar {timeframe} terakhir: {last['timestamp'].isoformat()} close={last['close']}"
            )
        if adapter.can_trade:
            balance = adapter.fetch_balance()
            held = sorted(
                ((asset, b) for asset, b in balance.assets.items() if b.total > 0),
                key=lambda item: -item[1].total,
            )
            print("saldo (total > 0):")
            for asset, b in held[:15]:
                print(f"  {asset}: free={b.free} used={b.used} total={b.total}")
            if not held:
                print("  (kosong)")
            open_orders = adapter.fetch_open_orders(symbol)
            print(f"order terbuka {symbol}: {len(open_orders)}")
            for order in open_orders:
                print(
                    f"  id={order.id} {order.side.value} {order.type.value} amount={order.amount} "
                    f"price={order.price} stop={order.stop_price} status={order.status.value}"
                )
        else:
            print("tanpa kunci (mode paper): saldo dan order tidak tersedia")
    except ExchangeError as exc:
        print(f"EXCHANGE ERROR: {exc}", file=sys.stderr)
        return EXIT_EXCHANGE_ERROR
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings(
            args.config, i_know_what_im_doing=getattr(args, "i_know_what_im_doing", False)
        )
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logger = setup_logging(settings)
    log_startup_banner(logger, settings)

    if args.command == "check-config":
        print(describe(settings))
        return EXIT_OK
    if args.command == "check-exchange":
        return _check_exchange(settings)

    parser.error(f"perintah tidak dikenal: {args.command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
