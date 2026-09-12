"""Checklist paper run dari log, jurnal, dan ledger: unit dengan berkas buatan, dan integrasi
dengan runner sungguhan yang benar-benar restart, gagal jaringan, dan kena kill switch."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import ccxt

from tests.test_runner import Scripted, build, ts
from tradebot.config import load_settings
from tradebot.ledger import Ledger
from tradebot.live.checklist import format_checklist, read_logs, run_checklist
from tradebot.live.runner import EXIT_KILL_SWITCH
from tradebot.logging_setup import setup_logging
from tradebot.strategy.base import Signal

T = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def write_journal(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"time": T.isoformat(), **e}) for e in events) + "\n")


def test_everything_missing_is_belum(project_dir: Path, config_path: Path):
    settings = load_settings(config_path, environ={})
    items = run_checklist(
        project_dir,
        log_dir="logs",
        journal_path=settings.live.journal_path,
        trades_csv=settings.live.trades_csv,
        min_fills=4,
    )
    assert [item.ok for item in items] == [False, False, False, False]
    text = format_checklist(items)
    assert text.count("[BELUM]") == 4 and "BELUM selesai" in text


def test_synthetic_evidence_marks_items_ok_and_detects_duplicates(
    project_dir: Path, config_path: Path
):
    settings = load_settings(config_path, environ={})
    logs = project_dir / "logs"
    logs.mkdir()
    (logs / "tradebot.log.1").write_text(
        "2026-09-12T09:00:00.000Z WARNING tradebot.risk.manager: "
        "gagal koneksi beruntun 1/5: putus\n"
        "2026-09-12T09:00:30.000Z INFO    tradebot.risk.manager: "
        "koneksi pulih setelah 1 kegagalan beruntun\n"
    )
    (logs / "tradebot.log").write_text(
        "2026-09-12T10:00:00.000Z INFO    tradebot.live.runner: runner siap: paper(x) BTC/USDT 1h, "
        "strategi ema_cross (jendela 250 bar), "
        "posisi=PositionState(amount=0.01, entry_price=100.0, ...)\n"
        "2026-09-12T11:00:00.000Z ERROR   tradebot.live.runner: "
        "BOT BERHENTI: stop_file (exit code 6)\n"
    )
    events = []
    ledger = Ledger(project_dir / settings.live.trades_csv)
    from tests.test_journal import order

    for i, side in enumerate(["buy", "sell", "buy", "sell"]):
        cid = f"c{i}"
        events.append(
            {
                "event": "intent",
                "client_order_id": cid,
                "side": side,
                "amount": 0.01,
                "reason": "signal",
            }
        )
        events.append(
            {"event": "result", "client_order_id": cid, "order_id": f"paper-{i}", "filled": 0.01}
        )
        import dataclasses

        from tradebot.exchange import OrderSide

        ledger.record_fill(dataclasses.replace(order(cid), id=f"paper-{i}", side=OrderSide(side)))
    write_journal(project_dir / settings.live.journal_path, events)
    items = run_checklist(
        project_dir,
        log_dir="logs",
        journal_path=settings.live.journal_path,
        trades_csv=settings.live.trades_csv,
        min_fills=4,
    )
    assert [item.ok for item in items] == [True, True, True, True], format_checklist(items)
    assert "SELESAI" in format_checklist(items)
    assert len(read_logs(logs)) == 4  # file berputar ikut dibaca, yang lama dulu

    # order ganda: dua beli berturut-turut yang terisi -> butir restart gagal
    events.insert(
        2,
        {
            "event": "intent",
            "client_order_id": "dup",
            "side": "buy",
            "amount": 0.01,
            "reason": "signal",
        },
    )
    events.insert(
        3, {"event": "result", "client_order_id": "dup", "order_id": "paper-9", "filled": 0.01}
    )
    write_journal(project_dir / settings.live.journal_path, events)
    items = run_checklist(
        project_dir,
        log_dir="logs",
        journal_path=settings.live.journal_path,
        trades_csv=settings.live.trades_csv,
        min_fills=4,
    )
    assert items[0].ok is False and any("ORDER GANDA" in e for e in items[0].evidence)
    assert items[3].ok is False and any("PUTUS" in e for e in items[3].evidence)


def test_checklist_passes_after_a_real_paper_session_with_all_four_events(
    project_dir: Path, config_path: Path
):
    """Runner sungguhan di atas klien palsu: LONG/FLAT bergantian, satu gagal jaringan yang pulih,
    restart di tengah posisi, lalu kill switch file STOP. Semua terlihat oleh checklist."""
    settings = load_settings(config_path, environ={})
    logger = setup_logging(settings)  # log ke logs/tradebot.log seperti perintah run
    script = {ts(i): (Signal.LONG if i % 2 == 0 else Signal.FLAT) for i in range(2, 12)}
    runner, client, advance = build(
        settings, project_dir, [100.0] * 14, Scripted(script, lookback=2)
    )
    advance(3)
    runner.start()
    runner.iterate()  # bar 2 LONG -> beli
    client.fail_next("fetch_ticker", *[ccxt.NetworkError("putus") for _ in range(3)])
    advance(3, minutes=5)
    runner.iterate()  # gagal koneksi (retry habis)
    advance(3, minutes=6)
    runner.iterate()  # pulih
    advance(4)
    runner.iterate()  # bar 3 FLAT -> jual
    advance(5)
    runner.iterate()  # bar 4 LONG -> beli
    # restart di tengah posisi terbuka
    runner2, client2, advance2 = build(
        settings, project_dir, [100.0] * 14, Scripted(script, lookback=2)
    )
    advance2(5, minutes=2)
    runner2.start()
    runner2.iterate()  # bar 4 sudah diputuskan: tidak ada order ganda
    advance2(6)
    runner2.iterate()  # bar 5 FLAT -> jual
    (project_dir / "STOP").write_text("", encoding="utf-8")
    assert runner2.run(max_iterations=10) == EXIT_KILL_SWITCH
    for handler in logger.handlers:
        handler.flush()

    items = run_checklist(
        project_dir,
        log_dir=settings.logging.dir,
        journal_path=settings.live.journal_path,
        trades_csv=settings.live.trades_csv,
        min_fills=settings.live.checklist_min_fills,
    )
    assert [item.ok for item in items] == [True, True, True, True], format_checklist(items)
    logging.getLogger("tradebot").handlers.clear()
