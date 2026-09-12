"""Laporan backtest yang bisa dibaca tanpa membuka kode. Buy-and-hold selalu ditampilkan."""

from __future__ import annotations

from tradebot.backtest.engine import BacktestResult
from tradebot.backtest.metrics import Metrics


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2%}"


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _metrics_lines(label: str, metrics: Metrics, final_equity: float, initial: float) -> list[str]:
    win_rate = "n/a" if metrics.win_rate is None else f"{metrics.win_rate:.1%}"
    return [
        f"{label}:",
        f"  equity akhir: {final_equity:.2f} (awal {initial:.2f}), "
        f"return total {_pct(metrics.total_return)}",
        f"  max drawdown: {metrics.max_drawdown:.2%}",
        f"  sharpe: {_num(metrics.sharpe)} (basis {metrics.sharpe_basis_bars_per_year} bar/tahun, "
        "dari return per bar)",
        f"  trade: {metrics.trade_count}, win rate {win_rate}, "
        f"profit factor {_num(metrics.profit_factor)}, "
        f"rata-rata {_num(metrics.avg_bars_held, 1)} bar per posisi",
    ]


def format_report(result: BacktestResult, *, stressed: bool) -> str:
    c = result.costs
    lines = [
        f"backtest {result.symbol} {result.timeframe}: {result.start.isoformat()} .. "
        f"{result.end.isoformat()} ({result.bars} bar)",
        f"strategi: {result.strategy_name}, jendela {result.lookback_bars} bar; warmup "
        f"{result.warmup_bars} bar, bar pertama yang diperdagangkan (strategi maupun "
        f"buy-and-hold) {result.tradable_start.isoformat()}",
        f"bar setelah lubang data, tanpa keputusan strategi: {result.bars_after_gap}",
        f"biaya per sisi{' (STRESS)' if stressed else ''}: fee {c.taker_fee_rate:.4%} + pajak "
        f"{c.tax_rate:.4%} + bursa {c.exchange_fee_rate:.4%} + slippage {c.slippage_rate:.4%} "
        f"= {c.cost_per_side_rate:.4%}; all-in per putaran {c.round_trip_rate:.4%}",
    ]
    lines += _metrics_lines("strategi", result.metrics, result.final_equity, result.initial_equity)
    totals = result.cost_totals
    lines.append(
        f"  biaya terbayar: fee {totals['taker_fee']:.2f}, pajak {totals['tax']:.2f}, "
        f"bursa {totals['exchange_fee']:.2f}, slippage {totals['slippage']:.2f} "
        f"(total {sum(totals.values()):.2f})"
    )
    reasons: dict[str, int] = {}
    for trade in result.trades:
        reasons[trade.exit_reason.value] = reasons.get(trade.exit_reason.value, 0) + 1
    if reasons:
        lines.append(
            "  alasan keluar: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
        )
    lines += _metrics_lines(
        "buy-and-hold (pembanding, biaya sama)",
        result.benchmark_metrics,
        result.benchmark_final_equity,
        result.initial_equity,
    )
    lines.append(f"  return harga mentah tanpa biaya: {_pct(result.raw_price_return)}")
    gap = result.final_equity - result.benchmark_final_equity
    verdict = "KALAH dari buy-and-hold" if gap < 0 else "di atas buy-and-hold"
    lines.append(
        f"strategi {verdict} sebesar {gap:+.2f} {result.symbol.split('/')[-1]} setelah biaya"
    )
    lines.append(
        "catatan: hasil backtest bukan bukti strategi menguntungkan; lihat RESEARCH.md soal "
        "jumlah percobaan dan holdout"
    )
    return "\n".join(lines)
