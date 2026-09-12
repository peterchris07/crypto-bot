"""Engine backtest: event-driven, bar per bar, tanpa lookahead.

Urutan kejadian pada bar N, persis seperti yang akan dilakukan runner live:

1. Keputusan dihitung dari bar 0..N-1 (semua sudah tutup). Strategi tidak pernah
   melihat bar N.
2. Kalau state target berbeda dari posisi nyata, order market dieksekusi di OPEN
   bar N: beli di open x (1 + slippage), jual di open x (1 - slippage). Fee, pajak,
   dan biaya bursa dipotong per sisi dari nilai transaksi.
3. Selama bar N, stop loss dan take profit lapis 1 dicek dari low dan high bar,
   termasuk untuk posisi yang baru dibuka di open bar itu. Fill di harga level
   ditambah slippage, KECUALI kalau open bar sudah melewati level (harga gap):
   bot live akan market-sell di sekitar open, jadi fill memakai open. Keduanya
   tembus dalam satu bar berarti stop loss (pesimistis).
4. Equity di close bar N = kas + jumlah base x close.

Warmup: strategi butuh lookback_bars bar sebelum bisa memberi sinyal, jadi bar
pertama yang bisa ditransaksikan adalah bar ke-lookback_bars. Buy-and-hold masuk
di bar yang sama, bukan di bar 1, supaya pembandingnya adil; kurva equity dimulai
satu bar sebelumnya.

Bar bolong: kalau bar N datang setelah lubang data (jarak dari bar N-1 lebih dari
satu timeframe), strategi tidak mengambil keputusan untuk bar N, sama seperti
runner live yang melewati iterasi atas bar stale atau bolong. Stop lapis 1 tetap
dicek karena di live pun dicek dari harga, bukan dari bar. Lama posisi dihitung
dari waktu, bukan dari jumlah baris.

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
from tradebot.data.ohlcv import timeframe_to_ms, validate_frame
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
    bars_held: int  # dari selisih waktu, bukan jumlah baris
    exit_reason: ExitReason

    @property
    def return_fraction(self) -> float:
        return self.pnl / (self.amount * self.entry_price)


@dataclass
class _Position:
    amount: float
    entry_price: float
    entry_time: pd.Timestamp
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
        self, amount: float, reference_price: float, time: pd.Timestamp, levels: StopLevels
    ) -> None:
        fill = reference_price * (1 + self.costs.slippage_rate)
        notional = amount * fill
        fees = self._charge(notional, reference_price, amount)
        self.cash -= notional + fees
        self.position = _Position(amount, fill, time, fees, levels)

    def sell(
        self, reference_price: float, time: pd.Timestamp, bars_held: int, reason: ExitReason
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
            bars_held=bars_held,
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
    equity_curve: pd.Series  # index timestamp, nilai equity di close tiap bar sejak warmup
    trades: list[Trade]
    cost_totals: dict[str, float]
    costs: CostConfig
    metrics: Metrics
    benchmark_final_equity: float
    benchmark_metrics: Metrics
    benchmark_cost_totals: dict[str, float]
    raw_price_return: float  # close terakhir / open bar pertama yang bisa ditransaksikan - 1
    strategy_name: str
    lookback_bars: int
    tradable_start: pd.Timestamp  # bar pertama yang bisa ditransaksikan, setelah warmup
    warmup_bars: int
    bars_after_gap: int  # bar yang datang setelah lubang data: tanpa keputusan strategi


def _sell_reference(levels: StopLevels, reason: ExitReason, open_price: float) -> float:
    """Harga acuan jual saat stop tembus. Kalau open sudah melewati level (gap), open."""
    if reason is ExitReason.STOP_LOSS:
        return min(open_price, levels.stop_loss)
    return max(open_price, levels.take_profit)


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
    step = pd.Timedelta(timeframe_to_ms(timeframe), unit="ms")

    warmup = max(1, strategy.lookback_bars)
    if len(frame) <= warmup:
        raise ValueError(
            f"backtest butuh lebih dari {warmup} bar (warmup strategi {strategy.name}), "
            f"dapat {len(frame)}"
        )

    def held(entry_time: pd.Timestamp, exit_time: pd.Timestamp) -> int:
        return int(round((exit_time - entry_time) / step))

    book = _Book(costs=costs, cash=initial_equity)
    equity = [initial_equity]  # bar warmup-1: belum ada keputusan, belum ada transaksi
    warned_short = False
    bars_after_gap = 0

    for i in range(warmup, len(frame)):
        now = stamps.iloc[i]
        after_gap = (now - stamps.iloc[i - 1]) > step
        if after_gap:
            bars_after_gap += 1
            signal = None  # runner live melewati keputusan atas bar setelah lubang
        else:
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
            p = book.position
            book.sell(float(opens[i]), now, held(p.entry_time, now), ExitReason.SIGNAL)
        elif book.position is None and signal is Signal.LONG:
            reference = float(opens[i])
            fill = reference * (1 + costs.slippage_rate)
            amount = risk.size_position(book.cash, fill, limits)
            book.buy(amount, reference, now, risk.stop_levels(fill))

        if book.position is not None:
            p = book.position
            reason = risk.exit_reason_for_bar(p.levels, float(highs[i]), float(lows[i]))
            if reason is not None:
                reference = _sell_reference(p.levels, reason, float(opens[i]))
                book.sell(reference, now, held(p.entry_time, now), reason)

        equity.append(book.equity(float(closes[i])))

    last = len(frame) - 1
    if book.position is not None:
        p = book.position
        end_time = stamps.iloc[last]
        book.sell(
            float(closes[last]), end_time, held(p.entry_time, end_time), ExitReason.END_OF_DATA
        )
        equity[-1] = book.equity(float(closes[last]))

    index = pd.Index(stamps.iloc[warmup - 1 :], name="timestamp")
    curve = pd.Series(equity, index=index, name="equity")
    metrics = compute_metrics(curve, book.trades, bars_per_year)

    bench_curve, bench_book = _buy_and_hold(frame, costs, initial_equity, risk, warmup, step)
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
        raw_price_return=float(closes[-1] / opens[warmup] - 1),
        strategy_name=strategy.name,
        lookback_bars=strategy.lookback_bars,
        tradable_start=stamps.iloc[warmup],
        warmup_bars=warmup,
        bars_after_gap=bars_after_gap,
    )


def _buy_and_hold(
    frame: pd.DataFrame,
    costs: CostConfig,
    initial_equity: float,
    risk: RiskManager,
    warmup: int,
    step: pd.Timedelta,
) -> tuple[pd.Series, _Book]:
    """Beli seluruh equity di open bar pertama yang bisa ditransaksikan, pegang, jual di
    close terakhir. Biaya dan bar mulai sama dengan strategi."""
    stamps = frame["timestamp"]
    closes = frame["close"].to_numpy()
    book = _Book(costs=costs, cash=initial_equity)
    reference = float(frame["open"].iloc[warmup])
    fill = reference * (1 + costs.slippage_rate)
    amount = initial_equity / (fill * (1 + costs.total_fee_rate))
    book.buy(amount, reference, stamps.iloc[warmup], risk.stop_levels(fill))
    equity = [initial_equity] + [book.equity(float(c)) for c in closes[warmup:]]
    last = len(frame) - 1
    bars_held = int(round((stamps.iloc[last] - stamps.iloc[warmup]) / step))
    book.sell(float(closes[last]), stamps.iloc[last], bars_held, ExitReason.END_OF_DATA)
    equity[-1] = book.equity(float(closes[last]))
    index = pd.Index(stamps.iloc[warmup - 1 :], name="timestamp")
    curve = pd.Series(equity, index=index, name="buy_and_hold")
    return curve, book
