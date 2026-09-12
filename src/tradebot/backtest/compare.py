"""Perbandingan harga isi paper (atau live) terhadap backtest, per trade, dengan tanda.

Yang penting bukan besar selisihnya tapi ARAHNYA. Kalau paper konsisten mengisi
lebih buruk dari backtest, asumsi slippage di config terlalu longgar dan setiap
backtest optimis. Karena itu setiap pasangan fill dicatat sebagai selisih yang
MERUGIKAN dalam pecahan harga backtest:

  beli   merugikan = (harga paper - harga backtest) / harga backtest
  jual   merugikan = (harga backtest - harga paper) / harga backtest

Positif berarti paper lebih buruk untuk saya. Pasangan dibentuk dari sisi dan
bar yang sama (waktu fill dipotong ke grid bar), berurutan. Fill yang tidak
punya pasangan dilaporkan, bukan dibuang diam-diam. Uji tanda binomial dua sisi
memberi p-value: berapa peluang bias sebesar ini muncul kalau arah selisih
sebenarnya acak.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from tradebot.backtest.engine import BacktestResult
from tradebot.data.ohlcv import floor_to_bar, ms_of, stamp_of, timeframe_to_ms


@dataclass(frozen=True)
class Fill:
    side: str  # buy | sell
    bar: pd.Timestamp  # waktu buka bar tempat fill terjadi
    price: float
    amount: float
    reason: str  # signal | stop_loss | take_profit | kill_switch | end_of_data | ?
    ref: str  # client_order_id (paper) atau label backtest


@dataclass(frozen=True)
class FillDiff:
    side: str
    reason: str
    bar: pd.Timestamp
    paper_price: float
    backtest_price: float
    adverse: float  # pecahan harga backtest; positif = merugikan saya
    ref: str

    @property
    def adverse_bps(self) -> float:
        return self.adverse * 10_000


@dataclass(frozen=True)
class DiffStats:
    n: int
    mean_bps: float | None
    std_bps: float | None
    adverse_share: float | None  # pecahan pasangan dengan selisih > 0
    favorable_share: float | None  # pecahan pasangan dengan selisih < 0; nol bukan keduanya
    p_value: float | None  # uji tanda dua sisi, pasangan dengan selisih 0 diabaikan
    worst_bps: float | None


@dataclass(frozen=True)
class FillComparison:
    matched: list[FillDiff]
    unmatched_paper: list[Fill]
    unmatched_backtest: list[Fill]

    def stats(self, reason: str | None = None) -> DiffStats:
        rows = [d for d in self.matched if reason is None or d.reason == reason]
        return diff_stats([d.adverse for d in rows])

    def reasons(self) -> list[str]:
        return sorted({d.reason for d in self.matched})


def diff_stats(values: Sequence[float]) -> DiffStats:
    n = len(values)
    if n == 0:
        return DiffStats(0, None, None, None, None, None, None)
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if n >= 2 else None
    adverse = sum(1 for v in values if v > 0)
    favorable = sum(1 for v in values if v < 0)
    nonzero = [v for v in values if v != 0]
    return DiffStats(
        n=n,
        mean_bps=mean * 10_000,
        std_bps=None if std is None else std * 10_000,
        adverse_share=adverse / n,
        favorable_share=favorable / n,
        p_value=sign_test_p_value(sum(1 for v in nonzero if v > 0), len(nonzero)),
        worst_bps=max(values) * 10_000,
    )


def sign_test_p_value(successes: int, trials: int) -> float | None:
    """Binomial dua sisi dengan p = 0,5. None kalau tidak ada pasangan bukan nol."""
    if trials == 0:
        return None
    lower = sum(math.comb(trials, k) for k in range(0, successes + 1)) / 2**trials
    upper = sum(math.comb(trials, k) for k in range(successes, trials + 1)) / 2**trials
    return min(1.0, 2 * min(lower, upper))


# --------------------------------------------------------------------------- #
# Sumber fill
# --------------------------------------------------------------------------- #


def paper_fills(
    ledger_rows: Iterable[dict[str, str]],
    journal_entries: Iterable[dict[str, Any]],
    symbol: str,
    timeframe: str,
) -> list[Fill]:
    """Fill dari ledger, alasan dari intent di jurnal (lewat client_order_id)."""
    reasons = {
        e["client_order_id"]: str(e.get("reason", "?"))
        for e in journal_entries
        if e.get("event") == "intent"
    }
    timeframe_ms = timeframe_to_ms(timeframe)
    seen: set[str] = set()
    fills: list[Fill] = []
    for row in ledger_rows:
        if row.get("pair") != symbol or not row.get("order_id"):
            continue
        if row["order_id"] in seen:  # baris reconciled mengulang order yang sama
            continue
        seen.add(row["order_id"])
        stamp = datetime.fromisoformat(row["timestamp"])
        bar = stamp_of(floor_to_bar(int(stamp.timestamp() * 1000), timeframe_ms))
        cid = row.get("client_order_id") or ""
        fills.append(
            Fill(
                side=row["side"],
                bar=bar,
                price=float(row["price"]),
                amount=float(row["amount"]),
                reason=reasons.get(cid, "?"),
                ref=cid or row["order_id"],
            )
        )
    return fills


def backtest_fills(result: BacktestResult) -> list[Fill]:
    fills: list[Fill] = []
    for index, trade in enumerate(result.trades):
        fills.append(
            Fill("buy", trade.entry_time, trade.entry_price, trade.amount, "signal", f"bt-{index}")
        )
        if trade.exit_reason.value != "end_of_data":
            fills.append(
                Fill(
                    "sell",
                    trade.exit_time,
                    trade.exit_price,
                    trade.amount,
                    trade.exit_reason.value,
                    f"bt-{index}",
                )
            )
    return fills


# --------------------------------------------------------------------------- #
# Pencocokan dan laporan
# --------------------------------------------------------------------------- #


def compare_fills(paper: Sequence[Fill], backtest: Sequence[Fill]) -> FillComparison:
    """Pasangkan fill paper dan backtest berdasarkan sisi dan bar, berurutan."""
    remaining = list(backtest)
    matched: list[FillDiff] = []
    unmatched_paper: list[Fill] = []
    for fill in paper:
        partner = next(
            (b for b in remaining if b.side == fill.side and ms_of(b.bar) == ms_of(fill.bar)),
            None,
        )
        if partner is None:
            unmatched_paper.append(fill)
            continue
        remaining.remove(partner)
        if fill.side == "buy":
            adverse = (fill.price - partner.price) / partner.price
        else:
            adverse = (partner.price - fill.price) / partner.price
        matched.append(
            FillDiff(
                side=fill.side,
                reason=partner.reason,
                bar=fill.bar,
                paper_price=fill.price,
                backtest_price=partner.price,
                adverse=adverse,
                ref=fill.ref,
            )
        )
    return FillComparison(
        matched=matched, unmatched_paper=unmatched_paper, unmatched_backtest=remaining
    )


def bias_verdict(stats: DiffStats, *, min_trades: int, adverse_share: float) -> str | None:
    """Kalimat temuan kalau bias satu arah terdeteksi; None kalau belum ada bukti."""
    if stats.n < min_trades or stats.adverse_share is None:
        return None
    mean = "n/a" if stats.mean_bps is None else f"{stats.mean_bps:+.1f} bps"
    p_value = "n/a" if stats.p_value is None else f"{stats.p_value:.3f}"
    if stats.adverse_share >= adverse_share:
        return (
            f"BIAS SATU ARAH TERDETEKSI: paper mengisi LEBIH BURUK dari backtest pada "
            f"{stats.adverse_share:.0%} dari {stats.n} fill (rata-rata {mean}, p-value uji tanda "
            f"{p_value}). Asumsi costs.slippage_rate terlalu longgar; setiap backtest optimis "
            "sebesar itu."
        )
    if stats.favorable_share is not None and stats.favorable_share >= adverse_share:
        return (
            f"BIAS SATU ARAH TERDETEKSI: paper mengisi LEBIH BAIK dari backtest pada "
            f"{stats.favorable_share:.0%} dari {stats.n} fill (rata-rata {mean}, p-value uji "
            f"tanda {p_value}). Asumsi slippage terlalu pesimistis; cek sebelum menurunkannya, "
            "karena paper pun bukan eksekusi sungguhan."
        )
    return None


def _fmt(value: float | None, suffix: str = "", digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}{suffix}"


def format_comparison(
    comparison: FillComparison, *, min_trades: int, adverse_share: float
) -> tuple[str, bool]:
    """Teks laporan dan apakah bias satu arah terdeteksi."""
    lines = ["selisih harga isi paper terhadap backtest (positif = merugikan saya):"]
    for diff in comparison.matched:
        lines.append(
            f"  {diff.bar.isoformat()} {diff.side:4s} {diff.reason:12s} "
            f"paper {diff.paper_price:.4f} backtest {diff.backtest_price:.4f} "
            f"selisih {diff.adverse_bps:+.1f} bps ({diff.ref})"
        )
    overall = comparison.stats()
    lines.append(
        f"pasangan: {overall.n}, tanpa pasangan: paper {len(comparison.unmatched_paper)}, "
        f"backtest {len(comparison.unmatched_backtest)}"
    )
    for fill in comparison.unmatched_paper:
        lines.append(
            f"  paper tanpa pasangan: {fill.bar.isoformat()} {fill.side} {fill.reason} ({fill.ref})"
        )
    for fill in comparison.unmatched_backtest:
        lines.append(f"  backtest tanpa pasangan: {fill.bar.isoformat()} {fill.side} {fill.reason}")
    share = None if overall.adverse_share is None else overall.adverse_share * 100
    lines.append(
        f"semua: rata-rata {_fmt(overall.mean_bps, ' bps')}, simpangan "
        f"{_fmt(overall.std_bps, ' bps')}, merugikan {_fmt(share, '%', 0)}, terburuk "
        f"{_fmt(overall.worst_bps, ' bps')}, p-value uji tanda {_fmt(overall.p_value, '', 3)}"
    )
    for reason in comparison.reasons():
        s = comparison.stats(reason)
        lines.append(
            f"  {reason:12s}: n={s.n}, rata-rata {_fmt(s.mean_bps, ' bps')}, simpangan "
            f"{_fmt(s.std_bps, ' bps')}, merugikan "
            f"{_fmt(None if s.adverse_share is None else s.adverse_share * 100, '%', 0)}"
        )
    verdict = bias_verdict(overall, min_trades=min_trades, adverse_share=adverse_share)
    if verdict:
        lines.append(verdict)
    elif overall.n < min_trades:
        lines.append(
            f"belum cukup pasangan untuk menilai bias (butuh {min_trades}, ada {overall.n})"
        )
    else:
        lines.append("tidak ada bias satu arah yang terdeteksi pada ambang ini")
    return "\n".join(lines), verdict is not None
