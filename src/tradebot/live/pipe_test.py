"""Uji pipa live sekali jalan: `tradebot pipe-test`.

Membuktikan jalur order bekerja end to end dengan uang asli berukuran minimum, tanpa
menunggu sinyal strategi, ditunggui operator dari awal sampai selesai. Urutannya
dikunci dan tidak punya loop:

1. preflight lengkap; satu saja gagal berarti tidak ada yang dikirim
2. tampilkan ukuran, perkiraan rupiah, perkiraan biaya bolak-balik; ketik UJI PIPA
3. beli ukuran minimum exchange, market; niat ke jurnal (fsync) sebelum dikirim
4. pasang stop lapis 2 (STOP_LOSS_LIMIT), verifikasi lewat fetch_open_orders;
   gagal berarti langsung jual kembali
5. tunggu, tampilkan posisi, harga, stop
6. batalkan stop DULU, verifikasi hilang, baru jual market
7. verifikasi flat dan tidak ada order terbuka
8. rekonsiliasi ledger sampai fee terisi

Tidak menyentuh RiskManager, runner, atau strategi. Protokol niat -> kirim -> hasil dan
penanganan jawaban hilang disalin dari runner (bukan diimpor) supaya runner tidak berubah.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tradebot.config import Settings
from tradebot.exchange import ExchangeError, OrderNotFoundError, OrderStateUnknownError
from tradebot.exchange.base import (
    ExchangeAdapter,
    MarketLimits,
    Order,
    OrderSide,
    OrderType,
    Ticker,
)
from tradebot.ledger import Ledger, fee_components
from tradebot.live.journal import OrderJournal
from tradebot.live.preflight import format_preflight, minimum_order_amount, run_preflight
from tradebot.risk import RiskManager
from tradebot.risk.manager import quantize_down

CONFIRM_PHRASE = "UJI PIPA"
IDR_SYMBOL = "USDT/IDR"
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_EXCHANGE_ERROR = 3
EXIT_PREFLIGHT_FAILED = 9


class PipeFailure(Exception):
    """Gagal di satu langkah; run() menangkapnya, merapikan akun, dan melapor."""

    def __init__(self, step: int, message: str) -> None:
        super().__init__(message)
        self.step = step


@dataclass
class PipeState:
    """Semua yang dibutuhkan laporan akhir, dikumpulkan sepanjang jalan."""

    amount: float = 0.0
    quote_before: float | None = None
    quote_after: float | None = None
    snapshot_before_buy: Ticker | None = None
    snapshot_before_sell: Ticker | None = None
    buy: Order | None = None
    sell: Order | None = None
    stop: Order | None = None
    stop_seen: Order | None = None  # bukti dari fetch_open_orders
    stop_gone_verified: bool = False
    held_amount: float = 0.0
    client_ids: list[str] = field(default_factory=list)
    failed_step: int | None = None
    failure: str = ""
    ledger_complete: bool = False
    final_base: float | None = None
    final_open_orders: list[Order] = field(default_factory=list)
    idr_rate: float | None = None
    notes: list[str] = field(default_factory=list)


class PipeTest:
    def __init__(
        self,
        settings: Settings,
        adapter: ExchangeAdapter,
        risk: RiskManager,
        journal: OrderJournal,
        ledger: Ledger,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        ask: Callable[[str], str] = input,
        out: Callable[[str], None] = print,
        hold_seconds: int = 60,
        idr_rate: float | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.risk = risk
        self.journal = journal
        self.ledger = ledger
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self.ask = ask
        self.out = out
        self.hold_seconds = hold_seconds
        self.symbol = settings.exchange.symbol
        self.base, self.quote = self.symbol.split("/", 1)
        self.limits: MarketLimits | None = None
        self.state = PipeState(idr_rate=idr_rate)

    # ------------------------------------------------------------------ #
    # Alat
    # ------------------------------------------------------------------ #

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=UTC)

    def _idr(self, usdt: float) -> str:
        if self.state.idr_rate:
            return f"{usdt:.4f} {self.quote} (~Rp {usdt * self.state.idr_rate:,.0f})"
        return f"{usdt:.4f} {self.quote} (kurs rupiah tidak terbaca)"

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
        """Sama dengan runner: niat ke jurnal (fsync) SEBELUM kirim; jawaban hilang dicari
        lewat client_order_id dan tidak pernah dikirim ulang."""
        now = self.now()
        cid = f"pt-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.state.client_ids.append(cid)
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
                self.out(f"order {cid} tidak ditemukan setelah jawaban hilang; TIDAK dikirim ulang")
                return None
            self.journal.record_reconciled(cid, "found", self.now(), order)
        else:
            self.journal.record_result(cid, order, self.now())
        if order.filled > 0:
            self.ledger.record_fill(order)
        return order

    def _open_orders(self) -> list[Order]:
        return self.adapter.fetch_open_orders(self.symbol)

    def _find_open(self, order: Order, attempts: int, expect_present: bool) -> Order | None:
        """Cari order di fetch_open_orders beberapa kali. Kembalikan order kalau terlihat."""
        seen = None
        for i in range(attempts):
            for open_order in self._open_orders():
                same_id = order.id is not None and open_order.id == order.id
                same_cid = (
                    order.client_order_id is not None
                    and open_order.client_order_id == order.client_order_id
                )
                if same_id or same_cid:
                    seen = open_order
                    break
            else:
                seen = None
            if (seen is not None) == expect_present:
                return seen
            if i + 1 < attempts:
                self._sleep(1.0)
        return seen

    def _stop_prices(self, entry_price: float) -> tuple[float, float]:
        cfg = self.settings.risk
        stop_price = entry_price * (1 - cfg.stop_loss_fraction * cfg.exchange_stop_multiplier)
        limit_price = stop_price * (1 - cfg.exchange_stop_limit_offset_fraction)
        step = (self.limits.price_step if self.limits else None) or 0.0
        if step:
            stop_price = quantize_down(stop_price, step)
            limit_price = quantize_down(limit_price, step)
        return stop_price, limit_price

    def _base_total(self) -> float:
        return self.adapter.fetch_balance().total(self.base)

    def _is_dust(self, base_amount: float, price: float) -> bool:
        step = (self.limits.amount_step if self.limits else None) or 0.0
        min_cost = (self.limits.min_cost if self.limits else None) or 0.0
        return base_amount < max(step, 1e-12) or base_amount * price < min_cost

    # ------------------------------------------------------------------ #
    # Langkah
    # ------------------------------------------------------------------ #

    def step1_preflight(self) -> bool:
        self.out("== 1/8 preflight")
        checks = run_preflight(self.settings, self.adapter, self.risk)
        self.out(format_preflight(checks, self.settings))
        if not all(c.ok for c in checks):
            self.out("PREFLIGHT GAGAL: tidak ada order yang dikirim.")
            return False
        self.limits = self.adapter.fetch_market_limits(self.symbol)
        if self.settings.stop_file_path.exists():
            raise PipeFailure(1, f"file STOP ada di {self.settings.stop_file_path}; hapus dulu")
        balance = self.adapter.fetch_balance()
        ticker = self.adapter.fetch_ticker(self.symbol)
        base_total = balance.total(self.base)
        if not self._is_dust(base_total, ticker.last):
            raise PipeFailure(
                1,
                f"akun sudah memegang {base_total} {self.base}; uji pipa harus mulai flat",
            )
        open_orders = self._open_orders()
        if open_orders:
            raise PipeFailure(
                1, f"ada {len(open_orders)} order terbuka di {self.symbol}; batalkan dulu"
            )
        self.state.quote_before = balance.total(self.quote)
        return True

    def step2_confirm(self) -> bool:
        self.out("== 2/8 ukuran dan konfirmasi")
        assert self.limits is not None
        costs = self.settings.costs
        ticker = self.adapter.fetch_ticker(self.symbol)
        ref_price = ticker.ask or ticker.last
        amount = minimum_order_amount(self.limits, ref_price * (1 + costs.slippage_rate))
        notional = amount * ref_price
        if self.state.idr_rate is None:
            try:
                self.state.idr_rate = self.adapter.fetch_ticker(IDR_SYMBOL).last
            except Exception as exc:  # noqa: BLE001 - kurs hanya untuk tampilan
                self.state.notes.append(f"kurs {IDR_SYMBOL} tidak terbaca: {exc}")
        fee_side = notional * costs.total_fee_rate
        slip_side = notional * costs.slippage_rate
        round_trip = 2 * (fee_side + slip_side)
        self.state.amount = amount
        self.out(
            f"order: BELI {amount} {self.base} market pada sekitar {ref_price} "
            f"(~{self._idr(notional)}); min_cost {self.limits.min_cost} {self.quote}, "
            f"step {self.limits.amount_step}"
        )
        self.out(
            f"perkiraan biaya bolak-balik: fee+pajak+bursa {2 * fee_side:.4f} {self.quote} "
            f"({costs.total_fee_rate:.4%} per sisi) + slippage {2 * slip_side:.4f} {self.quote} "
            f"({costs.slippage_rate:.2%} per sisi) = {self._idr(round_trip)}"
        )
        self.out(
            f"lalu stop lapis 2 di {self._stop_prices(ref_price)[0]}, tunggu "
            f"{self.hold_seconds} detik, batalkan stop, jual market. Satu putaran, uang asli."
        )
        answer = self.ask(f"Ketik {CONFIRM_PHRASE} persis untuk melanjutkan: ")
        if answer.strip() != CONFIRM_PHRASE:
            self.out("dibatalkan: tidak ada order yang dikirim.")
            return False
        return True

    def step3_buy(self) -> None:
        self.out("== 3/8 beli ukuran minimum")
        self.state.snapshot_before_buy = self.adapter.fetch_ticker(self.symbol)
        order = self._submit(OrderSide.BUY, self.state.amount, "pipe_test")
        if order is None:
            raise PipeFailure(3, "jawaban beli hilang dan order tidak ditemukan; tidak ada posisi")
        self.state.buy = order
        if order.filled <= 0:
            if order.is_open and order.id:
                try:
                    self.adapter.cancel_order(order.id, self.symbol)
                except ExchangeError as exc:
                    self.state.notes.append(f"pembatalan beli yang tidak terisi gagal: {exc}")
            raise PipeFailure(3, f"beli tidak terisi (status {order.status.value})")
        held = order.filled
        if order.fee_currency == self.base and order.fee:
            held -= order.fee
        free_base = self.adapter.fetch_balance().free(self.base)
        if free_base < held:
            self.state.notes.append(
                f"saldo bebas {free_base} {self.base} < fill {held}; pakai saldo"
            )
            held = free_base
        step = (self.limits.amount_step if self.limits else None) or 0.0
        self.state.held_amount = quantize_down(held, step) if step else held
        self.out(
            f"terisi {order.filled} {self.base} @ {order.average} (order {order.id}); "
            f"dipegang {self.state.held_amount} {self.base}"
        )

    def step4_stop(self) -> None:
        self.out("== 4/8 pasang stop lapis 2")
        assert self.state.buy is not None
        entry = self.state.buy.average or (
            self.state.snapshot_before_buy.last if self.state.snapshot_before_buy else 0.0
        )
        stop_price, limit_price = self._stop_prices(entry)
        try:
            order = self._submit(
                OrderSide.SELL,
                self.state.held_amount,
                "pipe_test_stop",
                order_type=OrderType.STOP_LOSS_LIMIT,
                price=limit_price,
                stop_price=stop_price,
            )
        except ExchangeError as exc:
            raise PipeFailure(4, f"stop lapis 2 gagal dipasang: {exc}") from exc
        if order is None or order.id is None:
            raise PipeFailure(4, "stop lapis 2: tidak ada id order dari exchange")
        self.state.stop = order
        seen = self._find_open(order, attempts=5, expect_present=True)
        if seen is None:
            try:
                self.adapter.cancel_order(order.id, self.symbol)
            except ExchangeError:
                pass
            raise PipeFailure(
                4,
                f"stop {order.id} tidak muncul di fetch_open_orders; respons create tidak "
                "dipercaya",
            )
        self.state.stop_seen = seen
        self.out(
            f"stop terpasang dan TERLIHAT di open orders: id {seen.id}, stop {seen.stop_price}, "
            f"limit {seen.price}, jumlah {seen.amount}, status {seen.status.value}"
        )

    def step5_wait(self) -> None:
        self.out(f"== 5/8 tunggu {self.hold_seconds} detik")
        self._show_position()
        if self.hold_seconds > 0:
            self._sleep(self.hold_seconds)
        self._show_position()

    def _show_position(self) -> None:
        ticker = self.adapter.fetch_ticker(self.symbol)
        stops = [o for o in self._open_orders() if o.stop_price is not None]
        entry = self.state.buy.average if self.state.buy else None
        pnl = (ticker.last - entry) * self.state.held_amount if entry else 0.0
        self.out(
            f"posisi {self.state.held_amount} {self.base} @ {entry}; harga sekarang {ticker.last} "
            f"(bid {ticker.bid}, ask {ticker.ask}); selisih kotor {pnl:+.4f} {self.quote}; "
            f"stop terbuka: {[(o.id, o.stop_price) for o in stops] or 'TIDAK ADA'}"
        )

    def step6_cancel_then_sell(self) -> None:
        self.out("== 6/8 batalkan stop DULU, verifikasi, baru jual")
        assert self.state.stop is not None and self.state.stop.id is not None
        stop = self.state.stop
        cid = stop.client_order_id or stop.id
        try:
            self.adapter.cancel_order(stop.id, self.symbol)
        except OrderNotFoundError:
            self.state.notes.append(f"stop {stop.id} sudah tidak ada saat dibatalkan")
        except ExchangeError as exc:
            self.journal.record_cancel(cid, stop.id, f"gagal: {exc}", self.now())
            raise PipeFailure(
                6, f"pembatalan stop {stop.id} gagal: {exc}; jual TIDAK dikirim"
            ) from exc
        self.journal.record_cancel(cid, stop.id, "canceled", self.now())
        still = self._find_open(stop, attempts=5, expect_present=False)
        if still is not None:
            raise PipeFailure(
                6,
                f"stop {stop.id} masih terbuka setelah dibatalkan; jual TIDAK dikirim "
                "(saldo terkunci)",
            )
        self.state.stop_gone_verified = True
        self.out(f"stop {stop.id} hilang dari open orders; mengirim jual")
        self.state.snapshot_before_sell = self.adapter.fetch_ticker(self.symbol)
        try:
            order = self._submit(OrderSide.SELL, self.state.held_amount, "pipe_test_exit")
        except ExchangeError as exc:
            raise PipeFailure(6, f"jual gagal: {exc}") from exc
        if order is None:
            raise PipeFailure(6, "jawaban jual hilang dan order tidak ditemukan")
        self.state.sell = order
        if order.filled + 1e-12 < self.state.held_amount:
            raise PipeFailure(
                6,
                f"jual terisi {order.filled} dari {self.state.held_amount} "
                f"(status {order.status.value})",
            )
        self.out(f"terjual {order.filled} {self.base} @ {order.average} (order {order.id})")

    def step7_verify_flat(self) -> None:
        self.out("== 7/8 verifikasi flat")
        balance = self.adapter.fetch_balance()
        ticker = self.adapter.fetch_ticker(self.symbol)
        base_total = balance.total(self.base)
        open_orders = self._open_orders()
        self.state.final_base = base_total
        self.state.final_open_orders = open_orders
        self.state.quote_after = balance.total(self.quote)
        if not self._is_dust(base_total, ticker.last):
            raise PipeFailure(7, f"masih ada {base_total} {self.base} di akun")
        if open_orders:
            raise PipeFailure(
                7, f"masih ada {len(open_orders)} order terbuka: {[o.id for o in open_orders]}"
            )
        self.out(
            f"flat: {base_total} {self.base}, 0 order terbuka, "
            f"{self.state.quote_after} {self.quote}"
        )

    def step8_reconcile(self) -> None:
        self.out("== 8/8 rekonsiliasi ledger")
        for attempt in range(6):
            self.ledger.reconcile(self.adapter, self.symbol)
            if self.ledger.is_complete():
                self.state.ledger_complete = True
                self.out("ledger LENGKAP: fee terisi dari fetch_my_trades")
                return
            self.out(f"fee belum terisi (percobaan {attempt + 1}/6); tunggu 2 detik")
            self._sleep(2.0)
        raise PipeFailure(8, "ledger masih pending: fee belum muncul di fetch_my_trades")

    # ------------------------------------------------------------------ #
    # Perapian saat gagal
    # ------------------------------------------------------------------ #

    def _tidy_after_failure(self, failed_step: int) -> None:
        """Utamakan akun flat dan bersih: batalkan stop kalau ada, jual kalau masih memegang."""
        try:
            open_orders = self._open_orders()
        except ExchangeError as exc:
            self.state.notes.append(f"fetch_open_orders gagal saat merapikan: {exc}")
            open_orders = []
        for order in open_orders:
            if order.id is None:
                continue
            try:
                self.adapter.cancel_order(order.id, self.symbol)
                self.journal.record_cancel(
                    order.client_order_id or order.id, order.id, "canceled", self.now()
                )
                self.state.notes.append(f"order terbuka {order.id} dibatalkan saat merapikan")
            except ExchangeError as exc:
                self.state.notes.append(f"pembatalan {order.id} gagal saat merapikan: {exc}")
        try:
            ticker = self.adapter.fetch_ticker(self.symbol)
            base_total = self._base_total()
        except ExchangeError as exc:
            self.state.notes.append(f"saldo tidak terbaca saat merapikan: {exc}")
            return
        if self._is_dust(base_total, ticker.last):
            return
        if failed_step >= 6 and self.state.sell is not None:
            # jual sudah dicoba dan gagal: jangan mengulang tanpa batas; pasang lagi stop
            self._rearm_stop(base_total)
            return
        step = (self.limits.amount_step if self.limits else None) or 0.0
        amount = quantize_down(base_total, step) if step else base_total
        try:
            order = self._submit(OrderSide.SELL, amount, "pipe_test_tidy")
        except ExchangeError as exc:
            self.state.notes.append(f"jual balik gagal: {exc}")
            self._rearm_stop(base_total)
            return
        if order is not None and order.filled > 0:
            self.state.sell = order
            self.state.notes.append(
                f"jual balik {order.filled} {self.base} @ {order.average} (order {order.id})"
            )
        else:
            self.state.notes.append("jual balik tidak terisi")
            self._rearm_stop(base_total)

    def _rearm_stop(self, base_total: float) -> None:
        entry = (
            self.state.buy.average if self.state.buy and self.state.buy.average else None
        ) or 0.0
        if not entry:
            return
        stop_price, limit_price = self._stop_prices(entry)
        step = (self.limits.amount_step if self.limits else None) or 0.0
        amount = quantize_down(base_total, step) if step else base_total
        try:
            order = self._submit(
                OrderSide.SELL,
                amount,
                "pipe_test_rearm_stop",
                order_type=OrderType.STOP_LOSS_LIMIT,
                price=limit_price,
                stop_price=stop_price,
            )
        except ExchangeError as exc:
            self.state.notes.append(f"stop pengaman gagal dipasang lagi: {exc}")
            return
        if order is not None and order.id:
            self.state.notes.append(
                f"stop pengaman dipasang lagi: order {order.id} stop {stop_price}"
            )

    def _final_state(self) -> None:
        try:
            ticker = self.adapter.fetch_ticker(self.symbol)
            balance = self.adapter.fetch_balance()
            self.state.final_base = balance.total(self.base)
            self.state.quote_after = balance.total(self.quote)
            self.state.final_open_orders = self._open_orders()
            dust = self._is_dust(self.state.final_base, ticker.last)
            self.state.notes.append(
                f"keadaan akhir: {self.state.final_base} {self.base} "
                f"({'debu' if dust else 'MASIH DIPEGANG'}), "
                f"{len(self.state.final_open_orders)} order terbuka"
            )
        except ExchangeError as exc:
            self.state.notes.append(f"keadaan akhir tidak terbaca: {exc}")

    # ------------------------------------------------------------------ #
    # Laporan
    # ------------------------------------------------------------------ #

    def report(self) -> str:
        s = self.state
        lines = ["", "================= LAPORAN UJI PIPA ================="]
        buy, sell = s.buy, s.sell
        if buy and buy.filled > 0:
            lines.append(f"beli : {buy.filled} {self.base} @ {buy.average} (order {buy.id})")
        else:
            lines.append("beli : tidak ada fill")
        if sell and sell.filled > 0:
            lines.append(f"jual : {sell.filled} {self.base} @ {sell.average} (order {sell.id})")
            if buy and buy.average and sell.average:
                diff = sell.average - buy.average
                lines.append(
                    f"selisih harga jual-beli: {diff:+.2f} {self.quote} ({diff / buy.average:+.3%})"
                )
        else:
            lines.append("jual : tidak ada fill")
        if s.snapshot_before_buy and buy and buy.average and s.snapshot_before_buy.ask:
            slip_buy = buy.average / s.snapshot_before_buy.ask - 1
            lines.append(f"slippage beli vs ask sebelum kirim: {slip_buy:+.3%}")
        if s.snapshot_before_sell and sell and sell.average and s.snapshot_before_sell.bid:
            slip_sell = 1 - sell.average / s.snapshot_before_sell.bid
            lines.append(f"slippage jual vs bid sebelum kirim: {slip_sell:+.3%}")

        # fee dari ledger (baris terakhir per order), dikonversi ke quote kalau dalam base
        ledger_rows = [r for r in self.ledger.rows() if r.get("client_order_id") in s.client_ids]
        latest: dict[str, dict[str, str]] = {}
        for row in ledger_rows:
            latest[row["order_id"]] = row
        fee_total = 0.0
        lines.append("fee terbayar per baris ledger:")
        for order_id, row in latest.items():
            comps = fee_components(row)
            price = float(row["price"]) if row.get("price") else 0.0
            parts = []
            for currency, value in comps:
                in_quote = value * price if currency == self.base else value
                fee_total += in_quote
                parts.append(
                    f"{value} {currency}"
                    + (f" (~{in_quote:.4f} {self.quote})" if currency == self.base else "")
                )
            lines.append(
                f"  order {order_id} {row['side']} {row['amount']} @ {row['price']}: "
                f"{', '.join(parts) or 'belum ada'} [{row['fee_status']}]"
            )
        costs = self.settings.costs
        if buy and buy.filled > 0:
            notional = (buy.cost or 0.0) + ((sell.cost or 0.0) if sell and sell.filled > 0 else 0.0)
            lines.append(
                f"perkiraan komponen menurut config pada notional {notional:.4f} {self.quote}: "
                f"taker {notional * costs.taker_fee_rate:.4f}, "
                f"PPh 22 {notional * costs.tax_rate:.4f}, "
                f"bursa {notional * costs.exchange_fee_rate:.4f} = "
                f"{notional * costs.total_fee_rate:.4f} {self.quote}"
            )
        lines.append(f"fee total terbayar (dari exchange): {self._idr(fee_total)}")
        if s.quote_before is not None and s.quote_after is not None:
            net = s.quote_after - s.quote_before
            lines.append(
                f"untung/rugi bersih (saldo {self.quote} sesudah dikurangi sebelum): "
                f"{self._idr(net)}"
            )
        if s.stop_seen is not None:
            seen = s.stop_seen
            lines.append(
                f"bukti stop lapis 2: order {seen.id} terlihat di fetch_open_orders "
                f"(stop {seen.stop_price}, limit {seen.price}, jumlah {seen.amount}); "
                f"dibatalkan dan hilang: {'ya' if s.stop_gone_verified else 'TIDAK'}"
            )
        elif s.stop is not None:
            lines.append(
                f"stop lapis 2: order {s.stop.id} dikirim tetapi TIDAK terlihat di open orders"
            )
        else:
            lines.append("stop lapis 2: tidak pernah dipasang")
        lines.append("baris jurnal terkait:")
        for entry in self.journal.entries():
            if entry.get("client_order_id") in s.client_ids:
                keys = (
                    "event",
                    "client_order_id",
                    "side",
                    "type",
                    "amount",
                    "order_id",
                    "status",
                    "outcome",
                )
                lines.append("  " + " ".join(f"{k}={entry[k]}" for k in keys if k in entry))
        lines.append("baris ledger terkait:")
        for row in ledger_rows:
            lines.append(
                f"  {row['timestamp']} {row['side']} {row['amount']} @ {row['price']} "
                f"fee={row['fee'] or '-'} {row['fee_currency'] or ''} [{row['fee_status']}] "
                f"order {row['order_id']}"
            )
        for note in s.notes:
            lines.append(f"catatan: {note}")
        if s.failed_step is None:
            lines.append(
                "VONIS: JALUR ORDER TERBUKTI (beli, stop lapis 2 terlihat, batal, jual, flat, "
                "ledger lengkap)"
            )
        else:
            lines.append(f"VONIS: GAGAL di langkah {s.failed_step}: {s.failure}")
            held = s.final_base is not None and not self._is_dust(
                s.final_base, (buy.average if buy and buy.average else 1.0)
            )
            holding = (
                f"SEDANG memegang {s.final_base} {self.base}"
                if held
                else f"TIDAK memegang {self.base} (flat)"
            )
            lines.append(f"  Anda {holding}")
            leftovers = [(o.id, o.type.value, o.stop_price) for o in s.final_open_orders]
            lines.append(f"  order terbuka tertinggal: {leftovers or 'tidak ada'}")
            manual = []
            if s.final_open_orders:
                manual.append(
                    "batalkan order terbuka itu di aplikasi Tokocrypto (Order > Open Orders)"
                )
            if held:
                manual.append(
                    f"jual {s.final_base} {self.base} di aplikasi (Spot, market sell) setelah "
                    "order terbuka dibatalkan"
                )
            if not s.ledger_complete and (buy and buy.filled > 0):
                manual.append(
                    "jalankan `tradebot ledger-status --i-know-what-im-doing` nanti sampai "
                    "tidak ada baris pending"
                )
            lines.append(
                "  yang harus dilakukan manual: " + ("; ".join(manual) if manual else "tidak ada")
            )
        lines.append("====================================================")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Jalankan
    # ------------------------------------------------------------------ #

    def run(self) -> int:
        code = EXIT_OK
        try:
            if not self.step1_preflight():
                return EXIT_PREFLIGHT_FAILED
            if not self.step2_confirm():
                return EXIT_CONFIG_ERROR
            self.step3_buy()
            self.step4_stop()
            self.step5_wait()
            self.step6_cancel_then_sell()
            self.step7_verify_flat()
            self.step8_reconcile()
        except PipeFailure as exc:
            self.state.failed_step, self.state.failure = exc.step, str(exc)
            self.out(f"GAGAL di langkah {exc.step}: {exc}")
            code = EXIT_EXCHANGE_ERROR if exc.step >= 3 else EXIT_CONFIG_ERROR
            if exc.step >= 3:
                self._tidy_after_failure(exc.step)
        except ExchangeError as exc:
            self.state.failed_step, self.state.failure = 0, f"error exchange: {exc}"
            self.out(f"GAGAL: {exc}")
            code = EXIT_EXCHANGE_ERROR
            self._tidy_after_failure(0)
        if self.state.failed_step is not None or self.state.quote_after is None:
            self._final_state()
        if self.state.buy is not None and not self.state.ledger_complete:
            try:
                self.ledger.reconcile(self.adapter, self.symbol)
                self.state.ledger_complete = self.ledger.is_complete()
            except ExchangeError as exc:
                self.state.notes.append(f"rekonsiliasi ledger gagal: {exc}")
        self.out(self.report())
        return code
