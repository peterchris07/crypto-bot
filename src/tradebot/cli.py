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
from collections.abc import Mapping, Sequence
from typing import Any

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
EXIT_BIAS_DETECTED = 7
EXIT_CHECKLIST_INCOMPLETE = 8
EXIT_PREFLIGHT_FAILED = 9

# Perintah yang hanya membaca catatan dan config: tidak menambah baris ke log bot.
READ_ONLY_COMMANDS = frozenset(
    {
        "check-config",
        "ledger-status",
        "status",
        "paper-checklist",
        "compare-paper",
        "live-size",
        "local-set",
        "local-unset",
    }
)


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

    ledger_status = sub.add_parser(
        "ledger-status",
        help="Laporkan ledger trade: jumlah baris, order yang fee-nya masih pending.",
    )
    _add_live_flag(ledger_status)

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
            "biaya dari config. Buy-and-hold selalu ditampilkan. TRADING_MODE di .env diabaikan: "
            "selalu berjalan sebagai paper dan tidak bisa mengirim order."
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
            "simulasi), testnet, atau live. Live butuh TRADING_MODE=live, flag, live.enabled "
            "true di config/local.yaml, dan preflight lulus."
        ),
    )
    run.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Berhenti setelah N iterasi (uji coba). Default: jalan terus sampai kill switch.",
    )
    _add_live_flag(run)

    compare = sub.add_parser(
        "compare-paper",
        help=(
            "Bandingkan harga isi paper (ledger) dengan backtest di periode yang sama, per "
            "trade, dengan tanda. Bias satu arah berarti exit code 7."
        ),
    )
    compare.add_argument("--start", default=None, help="Awal periode; default: fill pertama.")
    compare.add_argument(
        "--end", default=None, help="Akhir periode; default: setelah fill terakhir."
    )
    _add_live_flag(compare)

    status = sub.add_parser(
        "status",
        help=(
            "Satu perintah untuk tahu keadaan: proses, posisi dan saldo paper, jumlah trade, "
            "ledger-status, paper-checklist, dan compare-paper kalau pasangannya cukup. "
            "Di mode live yang dibaca adalah catatan live (state/live, trades/live)."
        ),
    )
    _add_live_flag(status)

    preflight = sub.add_parser(
        "preflight",
        help=(
            "Pemeriksaan kesiapan live tanpa mengirim order: tanggal verifikasi izin kunci, kunci "
            "bisa membaca saldo, pasangan dan minimum notional, sizing, jam, dukungan stop."
        ),
    )
    _add_live_flag(preflight)

    live_size = sub.add_parser(
        "live-size",
        help=(
            "Lihat atau ubah penanda ukuran order di live: minimum (order pertama dipaksa ke "
            "minimum exchange) atau normal (hasil sizing). Naik ke normal ditolak sebelum "
            "live.min_cycles_before_normal siklus selesai."
        ),
    )
    live_size.add_argument("--normal", action="store_true", help="Naik ke ukuran normal.")
    live_size.add_argument("--minimum", action="store_true", help="Kembali ke ukuran minimum.")
    _add_live_flag(live_size)

    checklist = sub.add_parser(
        "paper-checklist",
        help=(
            "Periksa dari log, jurnal, dan ledger apakah paper run sudah memperlihatkan restart "
            "di tengah posisi, kegagalan jaringan, kill switch, dan trade yang tertelusuri."
        ),
    )
    _add_live_flag(checklist)

    local_set = sub.add_parser(
        "local-set",
        help=(
            "Tulis nilai ke config/local.yaml (overlay lokal, tidak di-commit), lalu muat ulang "
            "untuk validasi; kalau ditolak, file dikembalikan. Contoh: local-set "
            "live.api_key_verified_date=2026-09-13 risk.position_fraction=0.25. Tidak pernah "
            "menerima kunci API."
        ),
    )
    local_set.add_argument(
        "assignments", nargs="+", metavar="section.key=nilai", help="Satu atau lebih nilai."
    )
    _add_live_flag(local_set)

    local_unset = sub.add_parser(
        "local-unset",
        help=(
            "Hapus key dari config/local.yaml lalu muat ulang untuk validasi; kalau ditolak, "
            "file dikembalikan. Juga jalan saat overlay rusak, supaya bisa diperbaiki."
        ),
    )
    local_unset.add_argument("keys", nargs="+", metavar="section.key", help="Satu atau lebih key.")
    _add_live_flag(local_unset)
    return parser


def _scrub(message: str, values: Mapping[str, Any]) -> str:
    """Nilai yang ditolak bisa saja kunci yang salah tempel: jangan pernah dipantulkan."""
    for key, value in values.items():
        text = str(value)
        if len(text) >= 4:
            message = message.replace(text, f"<nilai {key}>")
    return message


def _local_set(settings: Settings, args: argparse.Namespace) -> int:
    from tradebot.localconfig import LOCAL_CONFIG_NAME, parse_assignment, set_local_values

    values: dict[str, Any] = {}
    for text in args.assignments:
        try:
            key, value = parse_assignment(text)
        except ValueError as exc:
            raw = text.split("=", 1)[-1]
            print(f"CONFIG ERROR: {_scrub(str(exc), {'?': raw})}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        values[key] = value
    forbidden = [
        k
        for k in values
        if k.rsplit(".", 1)[-1].lower() in {"api_key", "api_secret", "secret", "password"}
        or k.lower().endswith(("_api_key", "_api_secret", "_secret", "_password"))
    ]
    if forbidden:
        print(
            f"CONFIG ERROR: {', '.join(forbidden)}: kunci API hanya boleh di .env, bukan di "
            f"{LOCAL_CONFIG_NAME}",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    config_dir = settings.config_path.parent
    path = config_dir / LOCAL_CONFIG_NAME
    backup = path.read_bytes() if path.exists() else None
    try:
        set_local_values(config_dir, values)
        load_settings(settings.config_path, i_know_what_im_doing=args.i_know_what_im_doing)
    except ConfigError as exc:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)
        print(
            f"CONFIG ERROR: {_scrub(str(exc), values)}; {LOCAL_CONFIG_NAME} dikembalikan",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    print(f"ditulis ke {path}:")
    for key, value in values.items():
        shown = value if not isinstance(value, str) or len(value) <= 24 else "<teks panjang>"
        print(f"  {key}: {shown}")
    return EXIT_OK


def _local_unset(settings: Settings, args: argparse.Namespace) -> int:
    from tradebot.localconfig import LOCAL_CONFIG_NAME, unset_local_values

    config_dir = settings.config_path.parent
    path = config_dir / LOCAL_CONFIG_NAME
    backup = path.read_bytes() if path.exists() else None
    try:
        unset_local_values(config_dir, args.keys)
        load_settings(settings.config_path, i_know_what_im_doing=args.i_know_what_im_doing)
    except (ConfigError, ValueError) as exc:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)
        print(f"CONFIG ERROR: {exc}; {LOCAL_CONFIG_NAME} dikembalikan", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"dihapus dari {path}: {', '.join(args.keys)}")
    return EXIT_OK


def _compare_paper(settings: Settings, start: str | None, end: str | None) -> int:
    from datetime import datetime

    from tradebot.backtest import run_backtest
    from tradebot.backtest.compare import (
        backtest_fills,
        compare_fills,
        format_comparison,
        paper_fills,
    )
    from tradebot.data.cache import OhlcvCache
    from tradebot.data.errors import DataError
    from tradebot.data.ohlcv import floor_to_bar, parse_utc_ms, stamp_of, timeframe_to_ms
    from tradebot.ledger import Ledger
    from tradebot.live.journal import OrderJournal
    from tradebot.risk import RiskError, RiskManager
    from tradebot.strategy import build_strategy

    symbol, timeframe = settings.exchange.symbol, settings.exchange.timeframe
    timeframe_ms = timeframe_to_ms(timeframe)
    ledger = Ledger(settings.root / settings.live.trades_csv)
    journal = OrderJournal(settings.root / settings.live.journal_path)
    fills = paper_fills(ledger.rows(), journal.entries(), symbol, timeframe)
    if not fills:
        print(f"DATA ERROR: ledger {ledger.path} belum punya fill untuk {symbol}", file=sys.stderr)
        return EXIT_DATA_ERROR
    try:
        start_ms = (
            parse_utc_ms(start) if start else min(int(f.bar.value // 1_000_000) for f in fills)
        )
        end_ms = (
            parse_utc_ms(end)
            if end
            else max(int(f.bar.value // 1_000_000) for f in fills) + timeframe_ms
        )
    except ValueError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    strategy = build_strategy(settings.strategy)
    cache = OhlcvCache(settings.root / settings.data.cache_dir)
    try:
        bars = cache.load(cache.path_for(settings.exchange.live.id, symbol, timeframe))
    except DataError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    warmup_ms = strategy.lookback_bars * timeframe_ms
    window = bars[
        (bars["timestamp"] >= stamp_of(floor_to_bar(start_ms, timeframe_ms) - warmup_ms))
        & (bars["timestamp"] < stamp_of(end_ms))
    ].reset_index(drop=True)
    if len(window) <= max(1, strategy.lookback_bars):
        print(
            f"DATA ERROR: cache hanya punya {len(window)} bar untuk periode fill (butuh lebih dari "
            f"{max(1, strategy.lookback_bars)}); jalankan fetch-data dulu",
            file=sys.stderr,
        )
        return EXIT_DATA_ERROR
    try:
        result = run_backtest(
            window,
            strategy,
            RiskManager(settings.risk, settings.costs),
            settings.costs,
            initial_equity=settings.backtest.initial_equity,
            bars_per_year=settings.backtest.bars_per_year,
            symbol=symbol,
            timeframe=timeframe,
        )
    except (RiskError, ValueError) as exc:
        print(f"BACKTEST ERROR: {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    comparison = compare_fills(fills, backtest_fills(result))
    text, biased = format_comparison(
        comparison,
        min_trades=settings.live.bias_min_trades,
        adverse_share=settings.live.bias_adverse_share,
    )
    print(
        f"compare-paper {symbol} {timeframe}: {stamp_of(start_ms).isoformat()} .. "
        f"{stamp_of(end_ms).isoformat()}, {len(fills)} fill paper, {len(result.trades)} trade "
        f"backtest (dibuat {datetime.now().astimezone().isoformat(timespec='seconds')})"
    )
    print(text)
    return EXIT_BIAS_DETECTED if biased else EXIT_OK


def _status(settings: Settings) -> int:
    import io
    import json
    import os
    from contextlib import redirect_stderr, redirect_stdout

    from tradebot.ledger import Ledger
    from tradebot.live.checklist import format_checklist, run_checklist
    from tradebot.live.journal import OrderJournal
    from tradebot.live.runner import PositionStore

    root = settings.root
    symbol = settings.exchange.symbol
    base, quote = symbol.split("/", 1)
    print(f"status {symbol} {settings.exchange.timeframe}, mode {settings.mode.value}, root {root}")
    print(
        f"config lokal: {settings.local_config_path or 'tidak ada'}; "
        f"live.enabled={'true' if settings.live.enabled else 'false'}; "
        f"catatan mode ini di state/{settings.mode_dir} dan trades/{settings.mode_dir}"
    )

    # proses. Supervisor per mode: state/paper_supervisor.* dan state/live_supervisor.*
    sup_name = f"{settings.mode.value}_supervisor"
    pid_file = root / "state" / f"{sup_name}.pid"
    if pid_file.exists():
        pid = pid_file.read_text().strip()
        alive = False
        try:
            os.kill(int(pid), 0)
            alive = True
        except (OSError, ValueError):
            pass
        print(f"proses: supervisor pid {pid} {'HIDUP' if alive else 'MATI (pid file basi)'}")
    else:
        print(f"proses: supervisor tidak berjalan (tidak ada state/{sup_name}.pid)")
    # Mode lain yang sedang jalan harus terlihat: setelah live-setup, .env berisi
    # TRADING_MODE=live, jadi status.command menampilkan catatan live walau yang jalan paper.
    for other in ("paper", "testnet", "live"):
        if other == settings.mode.value:
            continue
        other_pid = root / "state" / f"{other}_supervisor.pid"
        if other_pid.exists():
            try:
                os.kill(int(other_pid.read_text().strip()), 0)
            except (OSError, ValueError):
                continue
            other_pid_text = other_pid.read_text().strip()
            print(
                f"PERHATIAN: supervisor {other} sedang HIDUP (pid {other_pid_text}); "
                f"status ini menampilkan catatan {settings.mode.value}, bukan {other}"
            )
    supervisor_state = root / "state" / f"{sup_name}.json"
    if supervisor_state.exists():
        try:
            sup = json.loads(supervisor_state.read_text(encoding="utf-8"))
            restarts = int(sup.get("restarts", 0))
            last_exit = sup.get("last_exit_code")
            line = (
                f"supervisor: mulai ulang {restarts} kali (batas {sup.get('max_restarts')}), "
                f"terakhir {sup.get('last_restart') or 'tidak pernah'}, exit terakhir "
                f"{'belum ada' if last_exit is None else last_exit}, mulai "
                f"{sup.get('started_at')}, {'berjalan' if sup.get('running') else 'berhenti'}"
            )
            if restarts > 0:
                line += (
                    "; PERHATIAN: mulai ulang berarti bot pernah mati karena error, "
                    f"lihat logs/{settings.mode.value}.out"
                )
            print(line)
        except (OSError, ValueError) as exc:
            print(f"supervisor: state tidak terbaca ({exc})")
    if settings.stop_file_path.exists():
        print(f"proses: file STOP ada di {settings.stop_file_path}; bot tidak akan jalan")
    log_file = root / settings.logging.dir / "tradebot.log"
    if log_file.exists():
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if lines:
            print(f"log terakhir: {lines[-1]}")
        halts = [line for line in lines if "BOT BERHENTI:" in line]
        if halts:
            print(f"kill switch terakhir: {halts[-1]}")

    # posisi dan saldo paper
    position = PositionStore(root / settings.live.position_path).load()
    print(f"posisi: {position if position else 'FLAT'}")
    if position is not None:
        if position.stop_order_id:
            print(f"lapis 2: order {position.stop_order_id} stop {position.stop_price}")
        else:
            print("lapis 2: tidak ada stop di exchange (paper, atau belum terpasang)")
    from tradebot.live.stage import LiveStageStore

    try:
        stage = LiveStageStore(root / settings.live.stage_path).load()
        print(
            f"live: enabled={settings.live.enabled}, ukuran order {stage.stage}, siklus selesai "
            f"{stage.cycles_completed}, izin kunci diperiksa "
            f"{settings.live.api_key_verified_date or 'BELUM PERNAH'}"
        )
    except ValueError as exc:
        print(f"live: penanda ukuran tidak terbaca ({exc})")
    account_path = root / settings.live.paper_account_path
    if account_path.exists():
        try:
            account = json.loads(account_path.read_text(encoding="utf-8"))
            balances = account.get("balances", {})
            quote_balance = balances.get(quote, 0.0)
            base_balance = balances.get(base, 0.0)
            print(
                f"akun paper: {quote_balance:.4f} {quote}, {base_balance:.6f} {base}, "
                f"{len(account.get('orders', []))} order (saldo awal "
                f"{settings.backtest.initial_equity:.2f} {quote})"
            )
        except (OSError, ValueError) as exc:
            print(f"akun paper: tidak terbaca ({exc})")

    # trade
    ledger = Ledger(root / settings.live.trades_csv)
    rows = ledger.rows()
    seen: dict[str, dict[str, str]] = {}
    for row in rows:
        seen[row["order_id"]] = row
    buys = sum(1 for r in seen.values() if r["side"] == "buy")
    sells = sum(1 for r in seen.values() if r["side"] == "sell")
    entries = OrderJournal(root / settings.live.journal_path).entries()
    intents = [e for e in entries if e.get("event") == "intent"]
    print(
        f"trade: {buys} beli, {sells} jual ({len(seen)} order di ledger, {len(intents)} niat di "
        f"jurnal, {len(rows)} baris ledger)"
    )
    print(
        f"ledger: {'LENGKAP' if ledger.is_complete() else 'BELUM LENGKAP, ada fee pending'} "
        f"({len(ledger.pending_rows())} pending)"
    )

    # checklist
    items = run_checklist(
        root,
        log_dir=settings.logging.dir,
        journal_path=settings.live.journal_path,
        trades_csv=settings.live.trades_csv,
        min_fills=settings.live.checklist_min_fills,
    )
    print(format_checklist(items))

    # perbandingan, kalau sudah ada fill
    if seen:
        buffer, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(errors):
            code = _compare_paper(settings, None, None)
        text = buffer.getvalue().strip()
        if code in (EXIT_OK, EXIT_BIAS_DETECTED):
            summary = [
                line
                for line in text.splitlines()
                if line.startswith(
                    (
                        "compare-paper",
                        "pasangan:",
                        "semua:",
                        "  signal",
                        "  stop_",
                        "  take_",
                        "  kill_",
                        "BIAS",
                        "belum cukup",
                        "tidak ada bias",
                    )
                )
            ]
            print("\n".join(summary))
        else:
            print(f"compare-paper belum bisa: {errors.getvalue().strip() or text}")
    else:
        print("compare-paper: belum ada fill")
    return EXIT_OK


def _paper_checklist(settings: Settings) -> int:
    from tradebot.live.checklist import format_checklist, run_checklist

    items = run_checklist(
        settings.root,
        log_dir=settings.logging.dir,
        journal_path=settings.live.journal_path,
        trades_csv=settings.live.trades_csv,
        min_fills=settings.live.checklist_min_fills,
    )
    print(format_checklist(items))
    return EXIT_OK if all(item.ok for item in items) else EXIT_CHECKLIST_INCOMPLETE


def _build_risk(settings: Settings):
    from tradebot.risk import DailyStateStore, RiskManager

    return RiskManager(
        settings.risk,
        settings.costs,
        stop_file=settings.stop_file_path,
        state_store=DailyStateStore(settings.root / settings.live.state_path),
    )


def _preflight(settings: Settings) -> int:
    from tradebot.exchange.factory import build_adapter
    from tradebot.live.preflight import format_preflight, run_preflight

    adapter = build_adapter(settings)
    checks = run_preflight(settings, adapter, _build_risk(settings))
    print(format_preflight(checks, settings))
    return EXIT_OK if all(c.ok for c in checks) else EXIT_PREFLIGHT_FAILED


def _live_size(settings: Settings, normal: bool, minimum: bool) -> int:
    from tradebot.live.stage import MINIMUM, NORMAL, LiveStageStore

    store = LiveStageStore(settings.root / settings.live.stage_path)
    if normal and minimum:
        print("CONFIG ERROR: pilih salah satu, --normal atau --minimum", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        if normal:
            stage = store.set_stage(
                NORMAL,
                min_cycles=settings.live.min_cycles_before_normal,
                note="dinaikkan secara sadar lewat live-size --normal",
            )
        elif minimum:
            stage = store.set_stage(MINIMUM, min_cycles=0, note="dikembalikan lewat live-size")
        else:
            stage = store.load()
    except ValueError as exc:
        print(f"LIVE-SIZE DITOLAK: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(
        f"ukuran order live: {stage.stage} (siklus selesai di ukuran minimum: "
        f"{stage.cycles_completed}, butuh {settings.live.min_cycles_before_normal} untuk naik; "
        f"diperbarui {stage.updated_at}; {stage.note})"
    )
    return EXIT_OK


def _run(settings: Settings, iterations: int | None) -> int:
    from tradebot.config import TradingMode
    from tradebot.exchange.factory import build_adapter
    from tradebot.ledger import Ledger
    from tradebot.live.journal import OrderJournal
    from tradebot.live.preflight import format_preflight, run_preflight
    from tradebot.live.runner import EXIT_KILL_SWITCH as RUNNER_KILL_SWITCH
    from tradebot.live.runner import PositionStore, Runner
    from tradebot.live.stage import LiveStageStore
    from tradebot.strategy import build_strategy

    if iterations is not None and iterations < 1:
        print("CONFIG ERROR: --iterations harus >= 1", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    root = settings.root
    risk = _build_risk(settings)
    adapter = build_adapter(settings)
    if settings.mode is TradingMode.LIVE:
        # Kunci ketiga, di luar TRADING_MODE=live dan flag: live.enabled di config. Tetap false
        # sampai pemilik menyatakan siap. Lalu preflight harus lulus sebelum loop dimulai.
        if not settings.live.enabled:
            print(
                "CONFIG ERROR: mode live belum diaktifkan: live.enabled masih false di config. "
                "Syaratnya: paper-checklist OK, preflight lulus, dan Anda menyatakan siap "
                "dengan mengubah live.enabled ke true secara sadar.",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        checks = run_preflight(settings, adapter, risk)
        print(format_preflight(checks, settings))
        if not all(c.ok for c in checks):
            print("PREFLIGHT GAGAL: bot live tidak dijalankan.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILED
    runner = Runner(
        settings,
        adapter,
        build_strategy(settings.strategy),
        risk,
        OrderJournal(root / settings.live.journal_path),
        Ledger(root / settings.live.trades_csv),
        PositionStore(root / settings.live.position_path),
        stage_store=LiveStageStore(root / settings.live.stage_path),
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
    if args.command in ("fetch-data", "backtest"):
        # Perintah data publik dan offline: mode dan kunci di .env tidak relevan dan tidak
        # boleh menghalangi. Dipaksa paper, mode teraman, yang tidak pernah butuh kunci.
        environ = {**os.environ, MODE_ENV_VAR: TradingMode.PAPER.value}
    try:
        settings = load_settings(
            args.config,
            i_know_what_im_doing=getattr(args, "i_know_what_im_doing", False),
            environ=environ,
            # overlay yang rusak harus tetap bisa diperbaiki lewat local-set/local-unset
            ignore_local=args.command in ("local-set", "local-unset"),
        )
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # Perintah baca-saja tidak menulis ke log bot: kalau tidak, "log terakhir" di status
    # selalu berisi banner status sendiri, bukan baris terakhir bot.
    read_only = args.command in READ_ONLY_COMMANDS
    logger = setup_logging(settings, to_file=not read_only)
    if not read_only:
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
    if args.command == "compare-paper":
        return _compare_paper(settings, args.start, args.end)
    if args.command == "paper-checklist":
        return _paper_checklist(settings)
    if args.command == "status":
        return _status(settings)
    if args.command == "preflight":
        return _preflight(settings)
    if args.command == "live-size":
        return _live_size(settings, args.normal, args.minimum)
    if args.command == "local-set":
        return _local_set(settings, args)
    if args.command == "local-unset":
        return _local_unset(settings, args)

    parser.error(f"perintah tidak dikenal: {args.command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
