"""Perbandingan harga isi paper vs backtest: tanda, statistik, uji tanda, vonis bias."""

from __future__ import annotations

import pandas as pd
import pytest

from tradebot.backtest.compare import (
    Fill,
    bias_verdict,
    compare_fills,
    diff_stats,
    format_comparison,
    paper_fills,
    sign_test_p_value,
)

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)


def bar(i: int) -> pd.Timestamp:
    return pd.Timestamp(T0 + i * HOUR, unit="ms", tz="UTC")


def test_adverse_sign_is_positive_when_paper_is_worse_for_me():
    paper = [Fill("buy", bar(1), 101.0, 1.0, "?", "p1"), Fill("sell", bar(2), 99.0, 1.0, "?", "p2")]
    bt = [
        Fill("buy", bar(1), 100.0, 1.0, "signal", "b0"),
        Fill("sell", bar(2), 100.0, 1.0, "signal", "b0"),
    ]
    comparison = compare_fills(paper, bt)
    assert [round(d.adverse, 6) for d in comparison.matched] == [0.01, 0.01]
    assert comparison.matched[0].reason == "signal"  # alasan dari backtest
    better = compare_fills(
        [Fill("buy", bar(1), 99.0, 1.0, "?", "p1"), Fill("sell", bar(2), 101.0, 1.0, "?", "p2")], bt
    )
    assert [round(d.adverse, 6) for d in better.matched] == [-0.01, -0.01]


def test_unmatched_fills_are_reported_not_dropped():
    paper = [
        Fill("buy", bar(1), 100.0, 1.0, "?", "p1"),
        Fill("sell", bar(5), 100.0, 1.0, "?", "p2"),
    ]
    bt = [
        Fill("buy", bar(1), 100.0, 1.0, "signal", "b0"),
        Fill("sell", bar(3), 100.0, 1.0, "stop_loss", "b0"),
    ]
    comparison = compare_fills(paper, bt)
    assert len(comparison.matched) == 1
    assert [f.ref for f in comparison.unmatched_paper] == ["p2"]
    assert [f.reason for f in comparison.unmatched_backtest] == ["stop_loss"]
    text, biased = format_comparison(comparison, min_trades=5, adverse_share=0.75)
    assert "paper tanpa pasangan" in text and "backtest tanpa pasangan" in text
    assert "belum cukup pasangan" in text and not biased


def test_diff_stats_hand_computed():
    stats = diff_stats([0.001, -0.001, 0.003, 0.002, 0.0])
    assert stats.n == 5
    assert stats.mean_bps == pytest.approx(10.0)
    assert stats.std_bps == pytest.approx(pd.Series([10, -10, 30, 20, 0.0]).std(ddof=1))
    assert stats.adverse_share == pytest.approx(3 / 5)
    assert stats.worst_bps == pytest.approx(30.0)
    assert stats.p_value == sign_test_p_value(3, 4)
    assert stats.favorable_share == pytest.approx(1 / 5)
    empty = diff_stats([])
    assert empty.n == 0 and empty.mean_bps is None and empty.p_value is None
    zeros = diff_stats([0.0] * 6)
    assert zeros.adverse_share == 0 and zeros.favorable_share == 0 and zeros.p_value is None
    assert bias_verdict(zeros, min_trades=5, adverse_share=0.75) is None, "selisih nol bukan bias"


def test_sign_test_p_value():
    assert sign_test_p_value(0, 0) is None
    assert sign_test_p_value(10, 10) == pytest.approx(2 / 1024)
    assert sign_test_p_value(5, 10) == pytest.approx(1.0)
    assert sign_test_p_value(8, 10) == pytest.approx(2 * (1 + 10 + 45) / 1024)


def test_bias_verdict_requires_min_trades_and_share():
    worse = diff_stats([0.002] * 4 + [-0.001])
    assert bias_verdict(worse, min_trades=6, adverse_share=0.75) is None
    verdict = bias_verdict(worse, min_trades=5, adverse_share=0.75)
    assert (
        verdict is not None
        and "LEBIH BURUK" in verdict
        and "slippage_rate terlalu longgar" in verdict
    )
    better = diff_stats([-0.002] * 5)
    assert "LEBIH BAIK" in bias_verdict(better, min_trades=5, adverse_share=0.75)
    mixed = diff_stats([0.002, -0.002, 0.001, -0.001, 0.0])
    assert bias_verdict(mixed, min_trades=5, adverse_share=0.75) is None


def test_paper_fills_join_ledger_with_journal_reasons_and_skip_reconciled_duplicates():
    rows = [
        {
            "pair": "BTC/USDT",
            "order_id": "paper-1",
            "client_order_id": "c1",
            "side": "buy",
            "amount": "0.01",
            "price": "100.15",
            "timestamp": "2023-11-14T22:00:30+00:00",
        },
        {
            "pair": "BTC/USDT",
            "order_id": "paper-1",
            "client_order_id": "c1",
            "side": "buy",
            "amount": "0.01",
            "price": "100.15",
            "timestamp": "2023-11-14T22:00:30+00:00",
        },
        {
            "pair": "ETH/USDT",
            "order_id": "x",
            "client_order_id": "c9",
            "side": "buy",
            "amount": "1",
            "price": "1",
            "timestamp": "2023-11-14T22:00:30+00:00",
        },
        {
            "pair": "BTC/USDT",
            "order_id": "paper-2",
            "client_order_id": "c2",
            "side": "sell",
            "amount": "0.01",
            "price": "97.0",
            "timestamp": "2023-11-14T23:40:00+00:00",
        },
    ]
    journal = [
        {"event": "intent", "client_order_id": "c1", "reason": "signal"},
        {"event": "intent", "client_order_id": "c2", "reason": "stop_loss"},
    ]
    fills = paper_fills(rows, journal, "BTC/USDT", "1h")
    assert [(f.side, f.reason, f.ref) for f in fills] == [
        ("buy", "signal", "c1"),
        ("sell", "stop_loss", "c2"),
    ]
    assert fills[0].bar == bar(0) and fills[1].bar == bar(1)


def test_format_comparison_lists_every_pair_with_sign_and_per_reason_stats():
    paper = [Fill("buy", bar(i), 100.5, 1.0, "?", f"p{i}") for i in range(6)]
    bt = [
        Fill("buy", bar(i), 100.0, 1.0, "signal" if i % 2 else "stop_loss", f"b{i}")
        for i in range(6)
    ]
    text, biased = format_comparison(compare_fills(paper, bt), min_trades=5, adverse_share=0.75)
    assert biased
    assert text.count("selisih +50.0 bps") == 6
    assert "signal      : n=3" in text and "stop_loss   : n=3" in text
    assert "BIAS SATU ARAH TERDETEKSI" in text and "merugikan 100%" in text
