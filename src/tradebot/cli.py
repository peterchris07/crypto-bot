"""Titik masuk command line.

Perintah yang ada sejauh ini: check-config (tahap 1), check-exchange dan
ledger-status (tahap 2), fetch-data (tahap 3). backtest dan run ditambahkan di
tahap berikutnya.
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
EXIT_LEDGER_PENDING = 4
EXIT_DATA_ERROR = 5


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
        description="Bot trading crypto spot: develop di Binance Testnet, live di Tokocrypto. "
        "Default mode: paper.",
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

    sub.add_parser(
        "ledger-status",
        help="Laporkan ledger trade: jumlah baris, order yang fee-nya masih pending.",
    )

    fetch = sub.add_parser(
        "fetch-data",
        help=(
            "Unduh OHLCV historis dari data publik venue live ke cache parquet di data.cache_dir. "
            "Tidak butuh kunci, tidak peduli mode. Inkremental: hanya bar yang belum ada."
        ),
    )
    fetch.add_argument(
        "--start",
        default=None,
        help="Awal rentang, tanggal ISO UTC (contoh 2025-09-01). Default: data.history_start.",
    )
    fetch.add_argument(
        "--end",
        default=None,
        help=(
            "Akhir rentang (eksklusif), tanggal ISO UTC. Default dan batas atas: bar yang sedang "
            "berjalan menurut jam server; bar itu tidak pernah disimpan."
        ),
    )
    return parser


def _ledger_status(settings: Settings) -> int:
    from tradebot.ledger import Ledger

    ledger = Ledger(settings.root / settings.live.trades_csv)
    rows = ledger.rows()
    pending = ledger.pending_rows()
    print(f"ledger: {ledger.path}")
    print(f"baris: {len(rows)}")
    print(f"order dengan fee pending: {len(pending)}")
    for row in pending:
        print(
            f"  order_id={row['order_id']} {row['side']} {row['amount']} {row['pair']} "
            f"@ {row['price']} ({row['timestamp']})"
        )
    print("status: LENGKAP" if ledger.is_complete() else "status: BELUM LENGKAP, ada fee pending")
    return EXIT_OK if ledger.is_complete() else EXIT_LEDGER_PENDING


def _fetch_data(settings: Settings, start: str | None, end: str | None) -> int:
    from tradebot.data.cache import OhlcvCache
    from tradebot.data.errors import DataError
    from tradebot.data.fetch import update_cache
    from tradebot.data.ohlcv import parse_utc_ms
    from tradebot.exchange.errors import ExchangeError
    from tradebot.exchange.factory import build_public_adapter

    symbol = settings.exchange.symbol
    timeframe = settings.exchange.timeframe
    venue_id = settings.exchange.live.id
    try:
        start_ms = parse_utc_ms(start if start is not None else settings.data.history_start)
        end_requested_ms = parse_utc_ms(end) if end is not None else None
    except ValueError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        adapter = build_public_adapter(settings)
        adapter.connect()
        server_now_ms = adapter.fetch_server_time_ms()
        # Batas atas selalu jam server: --end di masa depan tidak boleh melahirkan gap palsu.
        end_ms = min(end_requested_ms, server_now_ms) if end_requested_ms else server_now_ms
        cache = OhlcvCache(settings.root / settings.data.cache_dir)
        report = update_cache(
            adapter,
            cache,
            venue_id=venue_id,
            symbol=symbol,
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            max_gap_bars=settings.data.max_gap_bars,
        )
    except ExchangeError as exc:
        print(f"EXCHANGE ERROR: {exc}", file=sys.stderr)
        return EXIT_EXCHANGE_ERROR
    except DataError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR

    print(f"venue: {adapter.name} (data publik, tanpa kunci)")
    print(f"pasangan: {symbol} {timeframe}")
    print(
        f"rentang diminta: {report.requested_start.isoformat()} .. "
        f"{report.requested_end.isoformat()} (eksklusif)"
    )
    print(f"cache: {report.path}")
    print(
        f"bar di cache: {len(report.frame)} ({report.first.isoformat()} .. "
        f"{report.last.isoformat()}), {report.new_bars} baru, {report.requests} permintaan"
    )
    if report.leading_missing_bars:
        print(
            f"PERHATIAN: data dimulai {report.leading_missing_bars} bar setelah awal yang diminta; "
            "bukan gap kalau pair memang baru tercatat setelah itu"
        )
    print(
        f"gap: {len(report.gaps)} ({report.missing_bars} bar hilang), laporan: {report.gaps_path}"
    )
    for gap in report.gaps:
        print(f"  {gap.describe()}")
    return EXIT_OK


def _check_exchange(settings: Settings) -> int:
    from tradebot.exchange.errors import ExchangeError
    from tradebot.exchange.factory import build_adapter

    symbol = settings.exchange.symbol
    timeframe = settings.exchange.timeframe
    costs = settings.costs
    try:
        adapter = build_adapter(settings)
        adapter.connect()
        limits = adapter.fetch_market_limits(symbol)
        ticker = adapter.fetch_ticker(symbol)
        bars = adapter.fetch_ohlcv(symbol, timeframe, limit=3)
        print(f"venue: {adapter.name} (mode {settings.mode.value})")
        print(f"jam: lokal - server = {adapter.server_offset_ms:+.0f} ms")
        print(
            f"biaya per sisi: fee {costs.taker_fee_rate:.4%} + pajak {costs.tax_rate:.4%} "
            f"+ bursa {costs.exchange_fee_rate:.4%} + slippage {costs.slippage_rate:.4%} "
            f"= {costs.cost_per_side_rate:.4%} (satu putaran {costs.round_trip_rate:.4%})"
        )
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
            print(
                "lapis 2 (stop di exchange) butuh pair mengiklankan STOP_LOSS_LIMIT: "
                "sudah dicek saat connect"
            )
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
    if args.command == "ledger-status":
        return _ledger_status(settings)
    if args.command == "fetch-data":
        return _fetch_data(settings, args.start, args.end)

    parser.error(f"perintah tidak dikenal: {args.command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
