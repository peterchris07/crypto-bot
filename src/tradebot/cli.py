"""Titik masuk command line.

Perintah yang ada: check-config (tahap 1), check-exchange dan ledger-status
(tahap 2), fetch-data (tahap 3), backtest dan backtest --stress (tahap 5), run
(tahap 7; mode live baru diaktifkan di tahap 8).
Flag --i-know-what-im-doing hanya ada pada perintah yang bisa menyentuh
exchange dengan kunci; keberadaannya tidak pernah cukup sendiri,
TRADING_MODE=live juga harus ada.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from tradebot import __version__
from tradebot.config import (
    DEFAULT_CONFIG_PATH,
    LIVE_FLAG,
    MODE_ENV_VAR,
    ConfigError,
    Settings,
    TradingMode,
    describe,
    load_settings,
)
from tradebot.logging_setup import log_startup_banner, setup_logging

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_EXCHANGE_ERROR = 3
EXIT_LEDGER_PENDING = 4
EXIT_DATA_ERROR = 5
EXIT_KILL_SWITCH = 6


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
            "TRADING_MODE dan kunci di .env diabaikan: perintah ini selalu berjalan sebagai paper "
            "dan tidak bisa mengirim order. Inkremental: hanya bar yang belum ada."
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

    backtest = sub.add_parser(
        "backtest",
        help=(
            "Jalankan backtest dari cache parquet (tanpa jaringan) dengan strategi, risk, dan "
            "biaya dari config. Buy-and-hold selalu ditampilkan."
        ),
    )
    backtest.add_argument("--start", default=None, help="Awal periode, tanggal ISO UTC.")
    backtest.add_argument("--end", default=None, help="Akhir periode (eksklusif), tanggal ISO UTC.")
    backtest.add_argument(
        "--stress",
        action="store_true",
        help="Gandakan fee, biaya bursa, dan slippage dengan costs.stress_multiplier; pajak tetap.",
    )

    run = sub.add_parser(
        "run",
        help=(
            "Jalankan loop trading sesuai mode: paper (default, harga Tokocrypto, eksekusi "
            "simulasi) atau testnet. Mode live menunggu tahap 8."
        ),
    )
    run.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Berhenti setelah N iterasi (uji coba). Default: jalan terus sampai kill switch.",
    )
    _add_live_flag(run)
    return parser


def _run(settings: Settings, iterations: int | None) -> int:
    from tradebot.config import TradingMode
    from tradebot.exchange.factory import build_adapter
    from tradebot.ledger import Ledger
    from tradebot.live.journal import OrderJournal
    from tradebot.live.runner import EXIT_KILL_SWITCH as RUNNER_KILL_SWITCH
    from tradebot.live.runner import PositionStore, Runner
    from tradebot.risk import DailyStateStore, RiskManager
    from tradebot.strategy import build_strategy

    if settings.mode is TradingMode.LIVE:
        print(
            "CONFIG ERROR: mode live belum diaktifkan; tahap 8 (lapis 2, urutan cancel stop, "
            "ukuran minimum) belum dikerjakan. Jalankan paper dulu beberapa hari.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    if iterations is not None and iterations < 1:
        print("CONFIG ERROR: --iterations harus >= 1", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    root = settings.root
    risk = RiskManager(
        settings.risk,
        settings.costs,
        stop_file=settings.stop_file_path,
        state_store=DailyStateStore(root / settings.live.state_path),
    )
    runner = Runner(
        settings,
        build_adapter(settings),
        build_strategy(settings.strategy),
        risk,
        OrderJournal(root / settings.live.journal_path),
        Ledger(root / settings.live.trades_csv),
        PositionStore(root / settings.live.position_path),
    )
    code = runner.run(iterations)
    if code == RUNNER_KILL_SWITCH:
        print("BOT BERHENTI karena kill switch; lihat log.", file=sys.stderr)
        return EXIT_KILL_SWITCH
    if code != 0:
        print(f"BOT BERHENTI dengan error (exit {code}); lihat log.", file=sys.stderr)
    return code


def _backtest(settings: Settings, start: str | None, end: str | None, stress: bool) -> int:
    from tradebot.backtest import format_report, run_backtest
    from tradebot.data.cache import OhlcvCache
    from tradebot.data.errors import DataError
    from tradebot.data.ohlcv import parse_utc_ms, stamp_of, timeframe_to_ms
    from tradebot.risk import RiskError, RiskManager
    from tradebot.strategy import build_strategy

    symbol = settings.exchange.symbol
    timeframe = settings.exchange.timeframe
    try:
        start_ms = parse_utc_ms(start) if start else None
        end_ms = parse_utc_ms(end) if end else None
    except ValueError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    cache = OhlcvCache(settings.root / settings.data.cache_dir)
    path = cache.path_for(settings.exchange.live.id, symbol, timeframe)
    try:
        bars = cache.load(path)
    except DataError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    if bars.empty:
        print(f"DATA ERROR: cache {path} belum ada; jalankan fetch-data dulu", file=sys.stderr)
        return EXIT_DATA_ERROR
    costs = settings.costs.stressed() if stress else settings.costs
    strategy = build_strategy(settings.strategy)
    risk = RiskManager(settings.risk, costs)
    if start_ms is not None:
        # Sertakan lookback_bars bar SEBELUM --start sebagai warmup, supaya periode yang
        # diminta bisa diperdagangkan penuh oleh strategi maupun buy-and-hold.
        warmup_ms = strategy.lookback_bars * timeframe_to_ms(timeframe)
        bars = bars[bars["timestamp"] >= stamp_of(start_ms - warmup_ms)]
    if end_ms is not None:
        bars = bars[bars["timestamp"] < stamp_of(end_ms)]
    bars = bars.reset_index(drop=True)
    warmup = max(1, strategy.lookback_bars)
    if len(bars) <= warmup:
        print(
            f"DATA ERROR: hanya {len(bars)} bar di periode yang diminta (termasuk warmup); "
            f"butuh lebih dari {warmup}",
            file=sys.stderr,
        )
        return EXIT_DATA_ERROR
    if start_ms is not None:
        before = int((bars["timestamp"] < stamp_of(start_ms)).sum())
        if before < strategy.lookback_bars:
            shifted = bars["timestamp"].iloc[warmup]
            print(
                f"PERINGATAN: hanya {before} bar sebelum --start untuk warmup "
                f"{strategy.lookback_bars} bar; bar pertama yang diperdagangkan bergeser ke "
                f"{shifted.isoformat()}",
                file=sys.stderr,
            )
    try:
        result = run_backtest(
            bars,
            strategy,
            risk,
            costs,
            initial_equity=settings.backtest.initial_equity,
            bars_per_year=settings.backtest.bars_per_year,
            symbol=symbol,
            timeframe=timeframe,
        )
    except (RiskError, ValueError) as exc:
        print(f"BACKTEST ERROR: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    print(format_report(result, stressed=stress))
    return EXIT_OK


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
    logging.getLogger("tradebot.cli").info(
        "fetch-data: %s di .env diabaikan; memakai data publik %s tanpa kunci",
        MODE_ENV_VAR,
        venue_id,
    )
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

    print(f"venue: {adapter.name} (data publik, tanpa kunci; {MODE_ENV_VAR} diabaikan)")
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

    environ = None
    if args.command == "fetch-data":
        # Perintah data publik: mode dan kunci di .env tidak relevan dan tidak boleh
        # menghalangi. Dipaksa paper, mode teraman, yang tidak pernah butuh kunci.
        environ = {**os.environ, MODE_ENV_VAR: TradingMode.PAPER.value}
    try:
        settings = load_settings(
            args.config,
            i_know_what_im_doing=getattr(args, "i_know_what_im_doing", False),
            environ=environ,
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
    if args.command == "backtest":
        return _backtest(settings, args.start, args.end, args.stress)
    if args.command == "run":
        return _run(settings, args.iterations)

    parser.error(f"perintah tidak dikenal: {args.command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
