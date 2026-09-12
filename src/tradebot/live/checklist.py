"""Checklist paper run: bukti dari log, jurnal, dan ledger, bukan dari jumlah hari.

Paper selesai kalau keempat hal ini sudah TERLIHAT:

1. Satu restart di tengah posisi terbuka, pulih tanpa order ganda.
2. Satu kegagalan jaringan yang tertangani (gagal, lalu pulih).
3. Satu kill switch menyala dan berhenti dengan exit code 6.
4. Beberapa trade yang bisa ditelusuri dari niat di jurnal sampai hasil dan
   sampai baris ledger.

Setiap butir menyebut buktinya (baris log atau id order), supaya tidak perlu
membaca log secara manual. Kalimat yang dicari di sini adalah kalimat yang
ditulis runner dan RiskManager; kalau kalimat itu diubah, test checklist merah.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradebot.ledger import Ledger
from tradebot.live.journal import CLOSING_EVENTS, INTENT, OrderJournal

RESTART_WITH_POSITION = "runner siap:"
POSITION_MARKER = "posisi=PositionState("
FAILURE_MARKER = "gagal koneksi beruntun"
RECOVERY_MARKER = "koneksi pulih setelah"
KILL_MARKER = "BOT BERHENTI:"
EXIT_6_MARKER = "(exit code 6)"


@dataclass(frozen=True)
class Item:
    key: str
    title: str
    ok: bool
    evidence: list[str]


def read_logs(log_dir: Path, name: str = "tradebot.log") -> list[str]:
    """Semua baris log termasuk file berputar (.1 .. .N), urut dari yang paling lama."""

    def order(path: Path) -> tuple[int, int]:
        rotation = path.name[len(name) :].lstrip(".")  # "" untuk file aktif, "1".."N" untuk lama
        return (0, -int(rotation)) if rotation.isdigit() else (1, 0)

    files = sorted((p for p in log_dir.glob(f"{name}*") if p.is_file()), key=order)
    lines: list[str] = []
    for path in files:
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def _filled_intents(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Intent yang tertutup dengan filled > 0, urut waktu."""
    closing: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.get("event") in CLOSING_EVENTS:
            closing[e["client_order_id"]] = e
    result = []
    for e in entries:
        if e.get("event") != INTENT:
            continue
        close = closing.get(e["client_order_id"])
        if close and float(close.get("filled") or 0) > 0:
            result.append({**e, "close": close})
    return result


def check_restart_without_duplicate(log_lines: list[str], entries: list[dict[str, Any]]) -> Item:
    restarts = [
        line for line in log_lines if RESTART_WITH_POSITION in line and POSITION_MARKER in line
    ]
    fills = _filled_intents(entries)
    duplicates = [
        b["client_order_id"]
        for a, b in zip(fills, fills[1:], strict=False)
        if a["side"] == b["side"]
    ]
    ok = bool(restarts) and not duplicates
    evidence = [f"restart dengan posisi terbuka: {line}" for line in restarts[:3]]
    if not restarts:
        evidence.append("belum ada baris 'runner siap' dengan posisi terbuka di log")
    if duplicates:
        evidence.append(
            f"ORDER GANDA: dua {len(duplicates)} order sisi sama berturut-turut: {duplicates}"
        )
    else:
        evidence.append(f"{len(fills)} fill di jurnal bergantian beli/jual, tidak ada order ganda")
    return Item(
        "restart", "restart di tengah posisi terbuka, pulih tanpa order ganda", ok, evidence
    )


def check_network_failure_handled(log_lines: list[str]) -> Item:
    failures = [line for line in log_lines if FAILURE_MARKER in line]
    recoveries = [line for line in log_lines if RECOVERY_MARKER in line]
    ok = bool(failures) and bool(recoveries)
    evidence = []
    if failures:
        evidence.append(f"kegagalan: {failures[0]}")
    else:
        evidence.append("belum ada baris 'gagal koneksi beruntun'")
    if recoveries:
        evidence.append(f"pulih: {recoveries[0]}")
    else:
        evidence.append("belum ada baris 'koneksi pulih'")
    return Item("network", "kegagalan jaringan yang tertangani", ok, evidence)


def check_kill_switch_exit_6(log_lines: list[str]) -> Item:
    hits = [line for line in log_lines if KILL_MARKER in line and EXIT_6_MARKER in line]
    evidence = [f"kill switch: {line}" for line in hits[:3]] or [
        "belum ada baris 'BOT BERHENTI' dengan exit code 6"
    ]
    return Item(
        "kill_switch", "kill switch menyala dan berhenti dengan exit code 6", bool(hits), evidence
    )


def check_traceable_trades(entries: list[dict[str, Any]], ledger: Ledger, min_fills: int) -> Item:
    ledger_by_cid = {
        row.get("client_order_id"): row for row in ledger.rows() if row.get("client_order_id")
    }
    traceable: list[str] = []
    broken: list[str] = []
    for intent in _filled_intents(entries):
        cid = intent["client_order_id"]
        close = intent["close"]
        row = ledger_by_cid.get(cid)
        if (
            close.get("order_id")
            and row is not None
            and row.get("order_id") == close.get("order_id")
        ):
            traceable.append(
                f"{cid}: intent {intent['side']} {intent['amount']} -> {close['event']} "
                f"order {close['order_id']} filled {close['filled']} -> ledger {row['side']} "
                f"{row['amount']} @ {row['price']}"
            )
        else:
            broken.append(cid)
    ok = len(traceable) >= min_fills and not broken
    evidence = traceable[:6]
    evidence.append(f"{len(traceable)} fill tertelusuri penuh (minimal {min_fills})")
    if broken:
        evidence.append(
            f"PUTUS: {len(broken)} intent terisi tanpa baris ledger yang cocok: {broken}"
        )
    return Item(
        "trades", "trade tertelusuri dari niat di jurnal sampai hasil dan ledger", ok, evidence
    )


def run_checklist(
    root: Path, *, log_dir: str, journal_path: str, trades_csv: str, min_fills: int
) -> list[Item]:
    log_lines = read_logs(root / log_dir)
    entries = OrderJournal(root / journal_path).entries()
    ledger = Ledger(root / trades_csv)
    return [
        check_restart_without_duplicate(log_lines, entries),
        check_network_failure_handled(log_lines),
        check_kill_switch_exit_6(log_lines),
        check_traceable_trades(entries, ledger, min_fills),
    ]


def format_checklist(items: list[Item]) -> str:
    lines = ["checklist paper run:"]
    for item in items:
        lines.append(f"[{'OK' if item.ok else 'BELUM'}] {item.title}")
        for line in item.evidence:
            lines.append(f"      {line}")
    done = all(item.ok for item in items)
    lines.append("paper run SELESAI menurut checklist" if done else "paper run BELUM selesai")
    return "\n".join(lines)
