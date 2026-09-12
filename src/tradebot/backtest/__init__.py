"""Backtest event-driven bar per bar, memakai Strategy dan RiskManager yang sama dengan live."""

from tradebot.backtest.engine import BacktestResult, Trade, run_backtest
from tradebot.backtest.metrics import Metrics, compute_metrics
from tradebot.backtest.report import format_report

__all__ = ["BacktestResult", "Metrics", "Trade", "compute_metrics", "format_report", "run_backtest"]
