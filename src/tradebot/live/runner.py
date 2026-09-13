"""Runner: loop utama mode paper, testnet, dan (tahap 8) live.

Satu iterasi, urutannya tetap:

1. Kill switch manual: file STOP.
2. Harga dan saldo dari exchange. Gagal jaringan dihitung sebagai satu kegagalan
   koneksi beruntun; sukses meresetnya.
3. Equity mark-to-market -> batas rugi harian.
4. Stop lapis 1 dari harga terakhir untuk posisi yang dipegang.
5. Bar: ambil bar terakhir, buang bar yang masih berjalan, lewati kalau warmup
   belum penuh, kalau bar terakhir stale, atau kalau bar terakhir datang setelah
   lubang (sama seperti backtest). Satu keputusan per bar tutup. Signal adalah
   state target: order dibuat hanya kalau berbeda dari posisi nyata.

Order: risk.before_order (runaway), jurnal intent, kirim, jurnal result, ledger.
Jawaban yang hilang: jurnal unknown, cari lewat client_order_id, jangan kirim
ulang. Saat start, intent yang belum tertutup direkonsiliasi dulu, dan catatan
posisi dicocokkan dengan saldo nyata.

Kill switch menghentikan run() dengan exit code EXIT_KILL_SWITCH; posisi dijual
dulu hanya kalau pemicunya membawa flatten=True; semua order terbuka dibatalkan.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tradebot.config import Settings, TradingMode
from tradebot.data.cache import atomic_write
from tradebot.data.ohlcv import floor_to_bar, stamp_of, timeframe_to_ms, validate_frame
from tradebot.exchange.base import (
    Balance,
    ExchangeAdapter,
    MarketLimits,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Ticker,
)
from tradebot.exchange.errors import (
    ExchangeError,
    FatalExchangeError,
    OrderNotFoundError,
    OrderStateUnknownError,
    RetryableExchangeError,
)
from tradebot.ledger import Ledger
from tradebot.live.journal import OrderJournal
from tradebot.live.preflight import minimum_order_amount
from tradebot.live.stage import MINIMUM, LiveStageStore
from tradebot.risk.manager import (
    ExitReason,
    KillSwitchTriggered,
    RiskError,
    RiskManager,
    quantize_down,
)
from tradebot.strategy.base import Signal, Strategy

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_EXCHANGE_ERROR = 3
EXIT_RISK_ERROR = 5
EXIT_KILL_SWITCH = 6


@dataclass(frozen=True)
class PositionState:
    amount: float
    entry_price: float
    entry_time: str
    stop_loss: float
    take_profit: float
    client_order_id: str | None
    # Lapis 2: stop order di exchange yang mengunci aset; harus dibatalkan sebelum order keluar.
    stop_order_id: str | None = None
    stop_client_order_id: str | None = None
    stop_price: float | None = None


class PositionStore:
    """Catatan posisi yang dipegang dan bar terakhir yang sudah diputuskan, dipersist supaya stop
    lapis 1 selamat dari restart dan bar yang sama tidak diputuskan dua kali setelah restart."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"position": None, "last_decided_bar": None}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FatalExchangeError(f"catatan posisi {self.path} rusak: {exc}") from exc
        if raw is None:
            return {"position": None, "last_decided_bar": None}
        if "amount" in raw:  # format lama: hanya posisi
            return {"position": raw, "last_decided_bar": None}
        return {"position": raw.get("position"), "last_decided_bar": raw.get("last_decided_bar")}

    def _write(self, data: dict[str, Any]) -> None:
        text = json.dumps(data, indent=2) + "\n"
        atomic_write(self.path, lambda tmp: tmp.write_text(text, encoding="utf-8"))

    def load(self) -> PositionState | None:
        raw = self._read()["position"]
        return None if raw is None else PositionState(**raw)

    def save(self, position: PositionState | None) -> None:
        data = self._read()
        data["position"] = None if position is None else asdict(position)
        self._write(data)

    def load_last_decided(self) -> pd.Timestamp | None:
        raw = self._read()["last_decided_bar"]
        return None if raw is None else pd.Timestamp(raw)

    def save_last_decided(self, bar: pd.Timestamp) -> None:
        data = self._read()
        data["last_decided_bar"] = bar.isoformat()
        self._write(data)


class Runner:
    def __init__(
        self,
        settings: Settings,
        adapter: ExchangeAdapter,
        strategy: Strategy,
        risk: RiskManager,
        journal: OrderJournal,
        ledger: Ledger,
        position_store: PositionStore,
        *,
        stage_store: LiveStageStore | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.strategy = strategy
        self.risk = risk
        self.journal = journal
        self.ledger = ledger
        self.position_store = position_store
        self.stage_store = stage_store
        self.live = settings.mode is TradingMode.LIVE
        # Dicari saat init, bukan saat definisi, supaya test bisa mengganti time.sleep.
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self.symbol = settings.exchange.symbol
        self.timeframe = settings.exchange.timeframe
        self.base, self.quote = self.symbol.split("/", 1)
        self.timeframe_ms = timeframe_to_ms(self.timeframe)
        self.limits: MarketLimits | None = None
        self.position: PositionState | None = None
        self.last_decided_bar: pd.Timestamp | None = None
        self.iterations = 0
        self.warned_short = False
        self.started = False

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=UTC)

    # ------------------------------------------------------------------ #
    # Start: koneksi, rekonsiliasi jurnal, catatan posisi vs saldo
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self.started:
            return
        self.adapter.connect()
        self.limits = self.adapter.fetch_market_limits(self.symbol)
        self._reconcile_journal()
        self.position = self.position_store.load()
        self.last_decided_bar = self.position_store.load_last_decided()
        self._reconcile_exchange_stop_on_start()
        self._reconcile_position_with_balance()
        if self.adapter.can_trade:
            # Ledger dua fase: fee yang masih pending dari sesi sebelumnya diisi sekarang.
            self.ledger.reconcile(self.adapter, self.symbol)
        self._ensure_exchange_stop()
        self.started = True
        log.info(
            "runner siap: %s %s %s, strategi %s (jendela %d bar), posisi=%s",
            self.adapter.name,
            self.symbol,
            self.timeframe,
            self.strategy.name,
            self.strategy.lookback_bars,
            self.position,
        )

    def _reconcile_journal(self) -> None:
        for intent in self.journal.pending_intents():
            cid = intent["client_order_id"]
            log.warning(
                "JURNAL: intent %s (%s %s) belum punya hasil; dicari di exchange, "
                "TIDAK dikirim ulang",
                cid,
                intent.get("side"),
                intent.get("amount"),
            )
            try:
                order = self.adapter.fetch_order(self.symbol, client_order_id=cid)
            except OrderNotFoundError:
                self.journal.record_reconciled(cid, "not_found", self.now())
                log.warning("JURNAL: order %s tidak ada di exchange; tidak pernah masuk", cid)
                continue
            self.journal.record_reconciled(cid, "found", self.now(), order)
            if order.filled > 0 and not self._ledger_has(cid):
                self.ledger.record_fill(order)
            log.warning(
                "JURNAL: order %s ternyata %s, filled=%s", cid, order.status.value, order.filled
            )

    def _ledger_has(self, client_order_id: str) -> bool:
        return any(row.get("client_order_id") == client_order_id for row in self.ledger.rows())

    def _reconcile_position_with_balance(self) -> None:
        balance = self.adapter.fetch_balance()
        base_total = balance.total(self.base)
        ticker = self.adapter.fetch_ticker(self.symbol)
        min_cost = (self.limits.min_cost if self.limits else None) or 0.0
        if self.position is not None:
            # Dengan catatan posisi, yang menentukan adalah lot-nya masih ada, bukan notional:
            # order pertama live berukuran minimum exchange, jadi turun 1 persen saja sudah
            # membuat notional < min_cost padahal lot dan stop lapis 2-nya masih dipegang.
            holds_base = base_total > 0 and base_total >= self.position.amount * 0.5
        else:
            holds_base = base_total * ticker.last > min_cost and base_total > 0
        if self.position is not None and not holds_base:
            log.warning(
                "catatan posisi %s tapi saldo %s hanya %s; catatan dihapus, dianggap FLAT",
                self.position,
                self.base,
                base_total,
            )
            self.position = None
            self.position_store.save(None)
        elif self.position is None and holds_base:
            entry = self._last_open_buy_from_ledger()
            if entry is not None:
                entry_price, entry_time, cid = entry
                source = "harga isi dari ledger"
            else:
                entry_price, entry_time, cid = ticker.last, self.now().isoformat(), None
                source = (
                    "harga SEKARANG karena ledger tidak punya pembelian yang belum dijual; "
                    "stop dan target efektif melebar sebesar pergerakan sejak masuk"
                )
            levels = self.risk.stop_levels(entry_price)
            self.position = PositionState(
                amount=base_total,
                entry_price=entry_price,
                entry_time=entry_time,
                stop_loss=levels.stop_loss,
                take_profit=levels.take_profit,
                client_order_id=cid,
            )
            self.position_store.save(self.position)
            log.warning(
                "ada %s %s tanpa catatan posisi; dianggap posisi dengan harga masuk %s (%s; "
                "stop %s, target %s)",
                base_total,
                self.base,
                entry_price,
                source,
                levels.stop_loss,
                levels.take_profit,
            )
        elif self.position is not None and base_total < self.position.amount * 0.999:
            log.warning(
                "saldo %s %s lebih kecil dari catatan posisi %s; catatan disesuaikan",
                base_total,
                self.base,
                self.position.amount,
            )
            self.position = PositionState(**{**asdict(self.position), "amount": base_total})
            self.position_store.save(self.position)

    def _last_open_buy_from_ledger(self) -> tuple[float, str, str | None] | None:
        """Pembelian terakhir di ledger yang belum diikuti penjualan: (harga, waktu, client id)."""
        last_buy: dict[str, str] | None = None
        for row in self.ledger.rows():
            if row.get("pair") != self.symbol:
                continue
            if row.get("side") == "buy":
                last_buy = row
            elif row.get("side") == "sell":
                last_buy = None
        if last_buy is None:
            return None
        try:
            return float(last_buy["price"]), last_buy["timestamp"], last_buy.get("client_order_id")
        except (KeyError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    # Iterasi
    # ------------------------------------------------------------------ #

    def iterate(self) -> None:
        self.iterations += 1
        now = self.now()
        self.risk.check_stop_file()

        try:
            ticker = self.adapter.fetch_ticker(self.symbol)
            balance = self.adapter.fetch_balance()
        except RetryableExchangeError as exc:
            self.risk.record_connection_failure(str(exc))
            return
        self.risk.record_connection_success()

        equity = balance.total(self.quote) + balance.total(self.base) * ticker.last
        self.risk.check_daily_loss(equity, now)

        # Urutan sama dengan backtest: keputusan atas bar yang baru tutup dieksekusi dulu
        # (di "open" bar berjalan), baru stop lapis 1 dicek pada harga sekarang. Kalau
        # urutannya dibalik, posisi yang keluar karena stop akan langsung masuk lagi di bar
        # yang sama, sesuatu yang tidak pernah terjadi di backtest.
        self._decide(now, ticker, balance)

        if self.position is not None:
            levels = self.risk.stop_levels(self.position.entry_price)
            reason = self.risk.exit_reason_for_price(levels, ticker.last)
            if reason is not None:
                log.info(
                    "stop lapis 1 %s pada harga %s (stop %s, target %s)",
                    reason.value,
                    ticker.last,
                    levels.stop_loss,
                    levels.take_profit,
                )
                self._sell(reason.value)
        self._ensure_exchange_stop()

    def _decide(self, now: datetime, ticker: Ticker, balance: Balance) -> None:
        closed = self._closed_bars(now)
        if closed is None:
            return
        last_bar = closed["timestamp"].iloc[-1]
        if self.last_decided_bar is not None and last_bar <= self.last_decided_bar:
            return  # bar ini sudah diputuskan
        if len(closed) >= 2 and (last_bar - closed["timestamp"].iloc[-2]) > pd.Timedelta(
            self.timeframe_ms, unit="ms"
        ):
            log.warning(
                "bar %s datang setelah lubang data; tidak ada keputusan untuk bar ini",
                last_bar.isoformat(),
            )
            self._mark_decided(last_bar)
            return

        signal = self.strategy.signal(closed)
        if signal is Signal.SHORT:
            if not self.warned_short:
                log.warning("strategi memberi SHORT; bot spot memperlakukannya sebagai FLAT")
                self.warned_short = True
            signal = Signal.FLAT
        self._mark_decided(last_bar)
        log.info(
            "bar %s tutup: sinyal %s, posisi %s, harga %s",
            last_bar.isoformat(),
            signal.value,
            "LONG" if self.position else "FLAT",
            ticker.last,
        )

        if signal is Signal.LONG and self.position is None:
            reference = ticker.ask or ticker.last
            fill = reference * (1 + self.settings.costs.slippage_rate)
            amount = self._entry_amount(balance.free(self.quote), fill)
            self._buy(amount)
        elif signal is Signal.FLAT and self.position is not None:
            self._sell(ExitReason.SIGNAL.value)

    def _entry_amount(self, free_quote: float, fill: float) -> float:
        """Sizing normal, KECUALI di live saat penanda ukuran masih "minimum": order pertama
        di uang asli dipaksa ke ukuran minimum exchange, bukan hasil sizing."""
        sized = self.risk.size_position(free_quote, fill, self.limits)
        if not (self.live and self.stage_store is not None and self.limits is not None):
            return sized
        stage = self.stage_store.load()
        if stage.stage != MINIMUM:
            return sized
        minimum = minimum_order_amount(self.limits, fill)
        log.warning(
            "LIVE ukuran minimum: order pertama %s %s (~%.2f %s), bukan hasil sizing %s; "
            "siklus selesai %d, naikkan lewat `tradebot live-size --normal`",
            minimum,
            self.base,
            minimum * fill,
            self.quote,
            sized,
            stage.cycles_completed,
        )
        return min(sized, minimum) if sized < minimum else minimum

    def _mark_decided(self, bar: pd.Timestamp) -> None:
        self.last_decided_bar = bar
        self.position_store.save_last_decided(bar)

    def _closed_bars(self, now: datetime) -> pd.DataFrame | None:
        """Bar yang sudah tutup, cukup untuk jendela strategi; None kalau tidak layak diputuskan."""
        need = self.strategy.lookback_bars + 2
        try:
            frame = self.adapter.fetch_ohlcv(self.symbol, self.timeframe, limit=need)
        except RetryableExchangeError as exc:
            self.risk.record_connection_failure(str(exc))
            return None
        validate_frame(frame)
        now_ms = int(now.timestamp() * 1000)
        current_open = stamp_of(floor_to_bar(now_ms, self.timeframe_ms))
        closed = frame[frame["timestamp"] < current_open].reset_index(drop=True)
        if len(closed) < max(1, self.strategy.lookback_bars):
            log.info(
                "warmup: %d dari %d bar tutup; belum ada keputusan",
                len(closed),
                self.strategy.lookback_bars,
            )
            return None
        expected_last = current_open - pd.Timedelta(self.timeframe_ms, unit="ms")
        last_bar = closed["timestamp"].iloc[-1]
        if last_bar >= expected_last:
            return closed  # bar yang seharusnya ada memang ada; umurnya tidak penting
        # Exchange belum memberi bar yang seharusnya sudah tutup. Beri waktu sebesar toleransi
        # sejak bar itu tutup (= current_open); lewat itu, bar dianggap stale dan dilewati.
        overdue = now - current_open.to_pydatetime()
        if overdue > timedelta(seconds=self.settings.live.stale_bar_tolerance_seconds):
            log.warning(
                "bar terakhir stale: seharusnya bar %s sudah tutup, exchange baru memberi %s "
                "(terlambat %s); iterasi dilewati",
                expected_last.isoformat(),
                last_bar.isoformat(),
                overdue,
            )
        else:
            log.debug("menunggu bar %s dari exchange (%s sejak tutup)", expected_last, overdue)
        return None

    # ------------------------------------------------------------------ #
    # Order
    # ------------------------------------------------------------------ #

    def _submit(
        self,
        side: OrderSide,
        amount: float,
        reason: str,
        *,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        stop_price: float | None = None,
    ) -> Order | None:
        now = self.now()
        self.risk.before_order(now)
        cid = f"tb-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.journal.record_intent(
            cid,
            symbol=self.symbol,
            side=side.value,
            order_type=order_type.value,
            amount=amount,
            reason=reason,
            time=now,
        )
        try:
            order = self.adapter.create_order(
                self.symbol,
                side,
                order_type,
                amount,
                price=price,
                stop_price=stop_price,
                client_order_id=cid,
            )
        except OrderStateUnknownError as exc:
            self.journal.record_unknown(cid, str(exc), self.now())
            try:
                order = self.adapter.fetch_order(self.symbol, client_order_id=cid)
            except OrderNotFoundError:
                self.journal.record_reconciled(cid, "not_found", self.now())
                log.error(
                    "order %s tidak ditemukan setelah jawaban hilang; TIDAK dikirim ulang", cid
                )
                return None
            self.journal.record_reconciled(cid, "found", self.now(), order)
        else:
            self.journal.record_result(cid, order, self.now())
        if order.filled > 0:
            self.ledger.record_fill(order)
            self.ledger.reconcile(self.adapter, self.symbol)
        return order

    def _buy(self, amount: float) -> None:
        order = self._submit(OrderSide.BUY, amount, "signal")
        if order is None or order.filled <= 0:
            return
        entry = order.average or 0.0
        held_amount = order.filled
        if order.fee_currency == self.base and order.fee:
            held_amount -= order.fee  # venue memotong fee dari base (Binance tanpa BNB)
        free_base = self.adapter.fetch_balance().free(self.base)
        if free_base < held_amount:
            log.warning(
                "saldo %s %s lebih kecil dari fill %s; posisi memakai saldo",
                self.base,
                free_base,
                held_amount,
            )
            held_amount = free_base
        levels = self.risk.stop_levels(entry)
        self.position = PositionState(
            amount=held_amount,
            entry_price=entry,
            entry_time=self.now().isoformat(),
            stop_loss=levels.stop_loss,
            take_profit=levels.take_profit,
            client_order_id=order.client_order_id,
        )
        self.position_store.save(self.position)
        log.info(
            "POSISI LONG %s %s @ %s stop=%s target=%s",
            held_amount,
            self.base,
            entry,
            levels.stop_loss,
            levels.take_profit,
        )
        self._ensure_exchange_stop()

    # ------------------------------------------------------------------ #
    # Lapis 2: stop order di exchange
    # ------------------------------------------------------------------ #

    def _exchange_stop_prices(self, entry_price: float) -> tuple[float, float]:
        """(stop_price, limit_price): jarak stop = stop_loss_fraction x exchange_stop_multiplier,
        lebih lebar dari lapis 1; limit sedikit di bawah stop supaya terisi saat terpicu."""
        cfg = self.settings.risk
        stop_price = entry_price * (1 - cfg.stop_loss_fraction * cfg.exchange_stop_multiplier)
        limit_price = stop_price * (1 - cfg.exchange_stop_limit_offset_fraction)
        step = (self.limits.price_step if self.limits else None) or 0.0
        if step:
            stop_price = quantize_down(stop_price, step)
            limit_price = quantize_down(limit_price, step)
        return stop_price, limit_price

    def _ensure_exchange_stop(self) -> None:
        """Pasang stop lapis 2 kalau ada posisi tanpa stop di exchange dan venue mendukungnya."""
        if self.position is None or self.position.stop_order_id is not None:
            return
        if not self.adapter.supports_exchange_stops:
            return
        stop_price, limit_price = self._exchange_stop_prices(self.position.entry_price)
        step = (self.limits.amount_step if self.limits else None) or 0.0
        amount = quantize_down(self.position.amount, step) if step else self.position.amount
        if amount <= 0:
            log.error(
                "lapis 2 tidak dipasang: jumlah posisi %s di bawah step", self.position.amount
            )
            return
        try:
            order = self._submit(
                OrderSide.SELL,
                amount,
                "exchange_stop",
                order_type=OrderType.STOP_LOSS_LIMIT,
                price=limit_price,
                stop_price=stop_price,
            )
        except (ExchangeError, RiskError) as exc:
            log.error("lapis 2 gagal dipasang: %s; dicoba lagi iterasi berikutnya", exc)
            return
        if order is None or order.id is None:
            log.error("lapis 2: tidak ada id order dari exchange; dicoba lagi iterasi berikutnya")
            return
        self.position = PositionState(
            **{
                **asdict(self.position),
                "stop_order_id": order.id,
                "stop_client_order_id": order.client_order_id,
                "stop_price": stop_price,
            }
        )
        self.position_store.save(self.position)
        log.info(
            "LAPIS 2 terpasang: stop %s limit %s untuk %s %s (order %s)",
            stop_price,
            limit_price,
            amount,
            self.base,
            order.id,
        )

    def _cancel_exchange_stop(self) -> bool:
        """Batalkan stop lapis 2 SEBELUM order keluar. True kalau aman mengirim order keluar.

        Kalau stop ternyata sudah tereksekusi, posisi sudah tidak ada: ledger dan jurnal
        disamakan dan posisi dihapus; False karena tidak ada lagi yang harus dijual.
        """
        assert self.position is not None
        stop_id = self.position.stop_order_id
        if stop_id is None:
            return True
        cid = self.position.stop_client_order_id or stop_id
        now = self.now()
        try:
            self.adapter.cancel_order(stop_id, self.symbol)
        except OrderNotFoundError:
            return not self._absorb_executed_stop(stop_id, cid, "cancel_not_found")
        except ExchangeError as exc:
            self.journal.record_cancel(cid, stop_id, f"gagal: {exc}", now)
            log.error(
                "pembatalan stop lapis 2 %s gagal: %s; order keluar TIDAK dikirim supaya tidak "
                "ditolak karena saldo terkunci",
                stop_id,
                exc,
            )
            return False
        self.journal.record_cancel(cid, stop_id, "canceled", now)
        self.position = PositionState(
            **{**asdict(self.position), "stop_order_id": None, "stop_client_order_id": None}
        )
        self.position_store.save(self.position)
        log.info("LAPIS 2 dibatalkan sebelum order keluar: %s", stop_id)
        return True

    def _absorb_executed_stop(self, stop_id: str, cid: str, context: str) -> bool:
        """Periksa stop lapis 2 yang tidak bisa dibatalkan. True kalau ternyata sudah tereksekusi
        dan posisi sudah diselesaikan (ledger, jurnal, catatan posisi)."""
        assert self.position is not None
        now = self.now()
        try:
            order = self.adapter.fetch_order(self.symbol, order_id=stop_id)
        except OrderNotFoundError:
            self.journal.record_cancel(cid, stop_id, f"{context}: tidak ada di exchange", now)
            log.warning("stop lapis 2 %s tidak ada di exchange; dianggap sudah hilang", stop_id)
            self.position = PositionState(
                **{**asdict(self.position), "stop_order_id": None, "stop_client_order_id": None}
            )
            self.position_store.save(self.position)
            return False
        if order.filled > 0:
            self.journal.record_reconciled(cid, "executed", now, order)
            if not self._ledger_has(cid) and order.client_order_id:
                self.ledger.record_fill(order)
            elif not self._ledger_has(cid):
                self.ledger.record_fill(order)
            self.ledger.reconcile(self.adapter, self.symbol)
            remaining = self.position.amount - order.filled
            log.warning(
                "STOP LAPIS 2 TEREKSEKUSI di exchange: %s terjual @ %s (%s); posisi %s",
                order.filled,
                order.average,
                context,
                "sebagian" if remaining > 1e-12 else "ditutup",
            )
            if remaining > 1e-12:
                self.position = PositionState(
                    **{
                        **asdict(self.position),
                        "amount": remaining,
                        "stop_order_id": None,
                        "stop_client_order_id": None,
                    }
                )
                self.position_store.save(self.position)
                return False
            self.position = None
            self.position_store.save(None)
            self._record_cycle()
            return True
        self.journal.record_cancel(cid, stop_id, f"{context}: status {order.status.value}", now)
        self.position = PositionState(
            **{**asdict(self.position), "stop_order_id": None, "stop_client_order_id": None}
        )
        self.position_store.save(self.position)
        return False

    def _reconcile_exchange_stop_on_start(self) -> None:
        """Bot bangun dengan catatan stop lapis 2: cek nasibnya di exchange dulu."""
        if self.position is None or self.position.stop_order_id is None:
            return
        if not self.adapter.can_trade:
            return
        stop_id = self.position.stop_order_id
        cid = self.position.stop_client_order_id or stop_id
        try:
            order = self.adapter.fetch_order(self.symbol, order_id=stop_id)
        except OrderNotFoundError:
            self._absorb_executed_stop(stop_id, cid, "start")
            return
        except ExchangeError as exc:
            log.error("stop lapis 2 %s tidak bisa diperiksa saat start: %s", stop_id, exc)
            return
        if order.status is OrderStatus.OPEN:
            log.info("stop lapis 2 %s masih terbuka di exchange", stop_id)
            return
        self._absorb_executed_stop(stop_id, cid, "start")

    def _record_cycle(self) -> None:
        if self.live and self.stage_store is not None:
            stage = self.stage_store.record_cycle()
            log.info("siklus live selesai: %d (ukuran %s)", stage.cycles_completed, stage.stage)

    def _sell(self, reason: str) -> None:
        assert self.position is not None
        # Urutan wajib: stop lapis 2 mengunci aset, jadi dibatalkan DULU. Kalau ternyata sudah
        # tereksekusi, posisi sudah selesai dan tidak ada yang dijual.
        if not self._cancel_exchange_stop():
            return
        if self.position is None:
            return
        order = self._submit(OrderSide.SELL, self.position.amount, reason)
        if order is None or order.filled <= 0:
            log.error(
                "jual (%s) tidak terisi (%s); posisi %s TETAP dicatat dan dicoba lagi",
                reason,
                None if order is None else order.status.value,
                self.position.amount,
            )
            return
        remaining = self.position.amount - order.filled
        if remaining > 1e-12:
            self.position = PositionState(**{**asdict(self.position), "amount": remaining})
            self.position_store.save(self.position)
            log.warning(
                "jual (%s) terisi sebagian %s; sisa posisi %s", reason, order.filled, remaining
            )
            return
        self.position = None
        self.position_store.save(None)
        log.info("POSISI FLAT (%s): jual %s @ %s", reason, order.filled, order.average)
        self._record_cycle()

    # ------------------------------------------------------------------ #
    # Loop
    # ------------------------------------------------------------------ #

    def _halt(self, exc: KillSwitchTriggered) -> int:
        log.error("%s", exc)
        if exc.flatten and self.position is not None:
            # Urutan penting: stop lapis 2 (tahap 8) mengunci aset, jadi order terbuka
            # dibatalkan DULU; order keluar yang dikirim sebelum itu ditolak karena saldo
            # terkunci.
            try:
                self.adapter.cancel_all_orders(self.symbol)
            except ExchangeError as cancel_exc:
                log.error(
                    "cancel_all_orders gagal: %s; flatten tidak dikirim, posisi dipegang",
                    cancel_exc,
                )
            else:
                try:
                    self._sell(ExitReason.KILL_SWITCH.value)
                except (ExchangeError, RiskError) as sell_exc:
                    log.error("flatten gagal: %s; posisi tetap dipegang", sell_exc)
        elif self.position is not None:
            # Tanpa flatten (file STOP, gagal koneksi, runaway): posisi dipegang dan jaring
            # lapis 2 di exchange TIDAK dilepas; itulah gunanya selama bot mati.
            log.error(
                "posisi %s dipegang; stop lapis 2 di exchange tetap terpasang (id %s, harga %s)",
                self.position.amount,
                self.position.stop_order_id,
                self.position.stop_price,
            )
        log.error("BOT BERHENTI: %s (exit code %d)", exc.switch.value, EXIT_KILL_SWITCH)
        return EXIT_KILL_SWITCH

    def run(self, max_iterations: int | None = None) -> int:
        try:
            self.start()
            while max_iterations is None or self.iterations < max_iterations:
                self.iterate()
                if max_iterations is None or self.iterations < max_iterations:
                    self._sleep(self.settings.live.loop_interval_seconds)
        except KillSwitchTriggered as exc:
            return self._halt(exc)
        except RiskError as exc:
            log.error("RISK: %s", exc)
            return EXIT_RISK_ERROR
        except ExchangeError as exc:
            log.error("EXCHANGE: %s", exc)
            return EXIT_EXCHANGE_ERROR
        return EXIT_OK
