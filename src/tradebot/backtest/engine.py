"""Engine backtest: event-driven, bar per bar, tanpa lookahead.

Urutan kejadian pada bar N, persis seperti yang akan dilakukan runner live:

1. Keputusan dihitung dari bar 0..N-1 (semua sudah tutup). Strategi tidak pernah
   melihat bar N.
2. Kalau state target berbeda dari posisi nyata, order market dieksekusi di OPEN
   bar N: beli di open x (1 + slippage), jual di open x (1 - slippage). Fee, pajak,
   dan biaya bursa dipotong per sisi dari nilai transaksi.
3. Selama bar N, stop loss dan take profit lapis 1 dicek dari low dan high bar,
   termasuk untuk posisi yang baru dibuka di open bar itu. Fill di harga stop
   ditambah slippage. Keduanya tembus dalam satu bar berarti stop loss (pesimistis).
4. Equity di close bar N = kas + jumlah base x close.

Di akhir data, posisi yang masih terbuka ditutup di close bar terakhir dengan
biaya penuh, supaya semua trade lengkap dan bisa dibandingkan dengan buy-and-hold
yang dihitung dengan aturan yang sama: beli di open bar pertama yang bisa
ditransaksikan, jual di close terakhir, keduanya berbiaya.

SHORT dari strategi diperlakukan sebagai FLAT dengan satu peringatan; ini bot spot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from tradebot.backtest.metrics import Metrics, compute_metrics
from tradebot.config import CostConfig
from tradebot.data.ohlcv import validate_frame
from tradebot.exchange.base import MarketLimits
from tradebot.risk.manager import ExitReason, RiskManager, StopLevels
from tradebot.strategy.base import Signal, Strategy

log = logging.getLogger(__name__)

COST_COMPONENTS = ("taker_fee", "tax", "exchange_fee", "slippage")


@dataclass(frozen=True)
class Trade:
    entry_time: pd.Timestamp
    entry_price: float  # harga isi, sudah termasuk slippage
    exit_time: pd.Timestamp
    exit_price: float
    amount: float
    entry_fees: float  # fee + pajak + bursa saat masuk, dalam quote
    exit_fees: float
    pnl: float  # bersih setelah semua biaya, dalam quote
    bars_held: int
    exit_reason: ExitReason

    @property
    def return_fraction(self) -> float:
        return self.pnl / (self.amount * self.entry_price)


@dataclass
class _Position:
    amount: float
    entry_price: float
    entry_time: pd.Timestamp
    entry_index: int
    entry_fees: float
    levels: StopLevels


@dataclass
class _Book:
    """Kas, posisi, dan akumulasi biaya per komponen untuk satu simulasi."""

    costs: CostConfig
    cash: float
    position: _Position | None = None
    cost_totals: dict[str, float] = field(
        default_factory=lambda: {component: 0.0 for component in COST_COMPONENTS}
    )
    trades: list[Trade] = field(default_factory=list)

    def _charge(self, notional: float, reference_price: float, amount: float) -> float:
        c = self.costs
        self.cost_totals["taker_fee"] += notional * c.taker_fee_rate
        self.cost_totals["tax"] += notional * c.tax_rate
        self.cost_totals["exchange_fee"] += notional * c.exchange_fee_rate
        # Slippage adalah selisih harga isi terhadap harga acuan, bukan potongan terpisah.
        self.cost_totals["slippage"] += amount * reference_price * c.slippage_rate
        return notional * c.total_fee_rate

    def buy(
        self,
        amount: float,
        reference_price: float,
        time: pd.Timestamp,
        index: int,
        levels: StopLevels,
    ) -> None:
        fill = reference_price * (1 + self.costs.slippage_rate)
        notional = amount * fill
        fees = self._charge(notional, reference_price, amount)
        self.cash -= notional + fees
        self.position = _Position(amount, fill, time, index, fees, levels)

    def sell(
        self, reference_price: float, time: pd.Timestamp, index: int, reason: ExitReason
    ) -> Trade:
        assert self.position is not None
        p = self.position
        fill = reference_price * (1 - self.costs.slippage_rate)
        notional = p.amount * fill
        fees = self._charge(notional, reference_price, p.amount)
        self.cash += notional - fees
        pnl = notional - fees - (p.amount * p.entry_price + p.entry_fees)
        trade = Trade(
            entry_time=p.entry_time,
            entry_price=p.entry_price,
            exit_time=time,
            exit_price=fill,
            amount=p.amount,
            entry_fees=p.entry_fees,
            exit_fees=fees,
            pnl=pnl,
            bars_held=index - p.entry_index,
            exit_reason=reason,
        )
        self.trades.append(trade)
        self.position = None
        return trade

    def equity(self, close: float) -> float:
        return self.cash + (self.position.amount * close if self.position else 0.0)


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    timeframe: str
    start: pd.Timestamp
    end: pd.Timestamp
    bars: int
    initial_equity: float
    final_equity: float
    equity_curve: pd.Series  # index timestamp, nilai equity di close tiap bar
    trades: list[Trade]
    cost_totals: dict[str, float]
    costs: CostConfig
    metrics: Metrics
    benchmark_final_equity: float
    benchmark_metrics: Metrics
    benchmark_cost_totals: dict[str, float]
    raw_price_return: float  # close terakhir / open pertama - 1, tanpa biaya
    strategy_name: str
    lookback_bars: int


def _sell_reference(levels: StopLevels, reason: ExitReason) -> float:
    return levels.stop_loss if reason is ExitReason.STOP_LOSS else levels.take_profit


def run_backtest(
    bars: pd.DataFrame,
    strategy: Strategy,
    risk: RiskManager,
    costs: CostConfig,
    *,
    initial_equity: float,
    bars_per_year: int,
    symbol: str,
    timeframe: str,
    limits: MarketLimits | None = None,
) -> BacktestResult:
    validate_frame(bars)
    if len(bars) < 2:
        raise ValueError("backtest butuh minimal 2 bar: satu untuk keputusan, satu untuk eksekusi")
    if initial_equity <= 0:
        raise ValueError("initial_equity harus > 0")

    stamps = bars["timestamp"].reset_index(drop=True)
    opens = bars["open"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()
    frame = bars.reset_index(drop=True)

    book = _Book(costs=costs, cash=initial_equity)
    equity = [initial_equity]  # bar 0: belum ada keputusan, belum ada transaksi
    warned_short = False

    for i in range(1, len(frame)):
        signal = strategy.signal(frame.iloc[:i])  # hanya bar yang sudah tutup
        if signal is Signal.SHORT:
            if not warned_short:
                log.warning(
                    "strategi %s memberi SHORT; bot spot memperlakukannya sebagai FLAT",
                    strategy.name,
                )
                warned_short = True
            signal = Signal.FLAT

        if book.position is not None and signal is Signal.FLAT:
            book.sell(float(opens[i]), stamps.iloc[i], i, ExitReason.SIGNAL)
        elif book.position is None and signal is Signal.LONG:
            reference = float(opens[i])
            fill = reference * (1 + costs.slippage_rate)
            amount = risk.size_position(book.cash, fill, limits)
            book.buy(amount, reference, stamps.iloc[i], i, risk.stop_levels(fill))

        if book.position is not None:
            reason = risk.exit_reason_for_bar(book.position.levels, float(highs[i]), float(lows[i]))
            if reason is not None:
                book.sell(_sell_reference(book.position.levels, reason), stamps.iloc[i], i, reason)

        equity.append(book.equity(float(closes[i])))

    last = len(frame) - 1
    if book.position is not None:
        book.sell(float(closes[last]), stamps.iloc[last], last, ExitReason.END_OF_DATA)
        equity[-1] = book.equity(float(closes[last]))

    curve = pd.Series(equity, index=pd.Index(stamps, name="timestamp"), name="equity")
    metrics = compute_metrics(curve, book.trades, bars_per_year)

    bench_curve, bench_book = _buy_and_hold(frame, costs, initial_equity, risk)
    bench_metrics = compute_metrics(bench_curve, bench_book.trades, bars_per_year)

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        start=stamps.iloc[0],
        end=stamps.iloc[-1],
        bars=len(frame),
        initial_equity=initial_equity,
        final_equity=float(curve.iloc[-1]),
        equity_curve=curve,
        trades=book.trades,
        cost_totals=dict(book.cost_totals),
        costs=costs,
        metrics=metrics,
        benchmark_final_equity=float(bench_curve.iloc[-1]),
        benchmark_metrics=bench_metrics,
        benchmark_cost_totals=dict(bench_book.cost_totals),
        raw_price_return=float(closes[-1] / opens[1] - 1),
        strategy_name=strategy.name,
        lookback_bars=strategy.lookback_bars,
    )


def _buy_and_hold(
    frame: pd.DataFrame, costs: CostConfig, initial_equity: float, risk: RiskManager
) -> tuple[pd.Series, _Book]:
    """Beli seluruh equity di open bar 1, pegang, jual di close terakhir. Biaya sama."""
    stamps = frame["timestamp"]
    closes = frame["close"].to_numpy()
    book = _Book(costs=costs, cash=initial_equity)
    reference = float(frame["open"].iloc[1])
    fill = reference * (1 + costs.slippage_rate)
    amount = initial_equity / (fill * (1 + costs.total_fee_rate))
    book.buy(amount, reference, stamps.iloc[1], 1, risk.stop_levels(fill))
    equity = [initial_equity] + [book.equity(float(c)) for c in closes[1:]]
    last = len(frame) - 1
    book.sell(float(closes[last]), stamps.iloc[last], last, ExitReason.END_OF_DATA)
    equity[-1] = book.equity(float(closes[last]))
    curve = pd.Series(equity, index=pd.Index(stamps, name="timestamp"), name="buy_and_hold")
    return curve, book
