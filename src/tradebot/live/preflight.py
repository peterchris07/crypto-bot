"""Pre-flight sebelum order pertama di live. Tidak pernah mengirim order.

Yang diperiksa, dengan bukti untuk tiap butir:

1. live.api_key_verified_date ada dan tidak lebih tua dari live.max_key_age_days.
   Pemilik harus memeriksa sendiri halaman API Management Tokocrypto: izin
   withdrawal MATI, pembatasan IP aktif kalau tersedia. Tidak ada endpoint untuk
   memverifikasi izin kunci, jadi ini kewajiban manusia, dan tanggalnya yang
   dicatat di config.
2. Kunci bisa membaca saldo. Itu saja; withdrawal TIDAK PERNAH dicoba.
3. Pasangan ada di load_markets, minimum notional terbaca, dan ukuran posisi
   hasil sizing (serta ukuran minimum untuk order pertama) di atas minimum itu.
4. Jam: pengukuran saat connect (pemanasan, sampel, rtt terkecil) valid dan
   selisihnya di bawah batas.
5. Venue mengiklankan STOP_LOSS_LIMIT untuk pair (lapis 2).

Di mode paper dan testnet butir yang butuh kunci live dilaporkan "tidak
berlaku", supaya perintah preflight tetap bisa dipakai kapan saja.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tradebot.config import Settings, TradingMode
from tradebot.exchange.base import ExchangeAdapter, MarketLimits
from tradebot.exchange.errors import ExchangeError
from tradebot.risk.manager import RiskError, RiskManager, quantize_down

API_MANAGEMENT_CHECKLIST = (
    "buka halaman API Management Tokocrypto untuk kunci yang dipakai bot: (1) pastikan izin "
    "withdrawal MATI, (2) aktifkan pembatasan IP kalau halaman itu menyediakannya dan isi IP "
    "mesin bot, (3) catat tanggal hari ini di config live.api_key_verified_date (YYYY-MM-DD)"
)


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    ok: bool
    detail: str
    applicable: bool = True


def check_key_verified_date(settings: Settings, today: datetime | None = None) -> Check:
    raw = settings.live.api_key_verified_date.strip()
    title = "izin kunci diperiksa manual di API Management (withdrawal mati, IP whitelist)"
    if not raw:
        return Check(
            "key_verified",
            title,
            False,
            "live.api_key_verified_date kosong; belum pernah diperiksa. "
            f"{API_MANAGEMENT_CHECKLIST}",
        )
    verified = datetime.fromisoformat(raw).replace(tzinfo=UTC)
    now = today or datetime.now(tz=UTC)
    age = now - verified
    limit = timedelta(days=settings.live.max_key_age_days)
    if age > limit:
        return Check(
            "key_verified",
            title,
            False,
            f"terakhir diperiksa {raw}, {age.days} hari lalu, melewati batas "
            f"{settings.live.max_key_age_days} hari. {API_MANAGEMENT_CHECKLIST}",
        )
    return Check("key_verified", title, True, f"diperiksa {raw} ({age.days} hari lalu)")


def minimum_order_amount(limits: MarketLimits, price: float) -> float:
    """Jumlah base terkecil yang diizinkan exchange pada harga ini (order pertama di live)."""
    amount = 0.0
    if limits.min_cost:
        amount = max(amount, limits.min_cost / price)
    if limits.min_amount:
        amount = max(amount, limits.min_amount)
    step = limits.amount_step or 0.0
    if step:
        units = int(amount / step)
        if units * step < amount - 1e-12:
            units += 1
        amount = round(units * step, 12)
    # sedikit di atas minimum supaya pembulatan harga tidak menjatuhkannya ke bawah min_cost
    if limits.min_cost and amount * price < limits.min_cost:
        amount = quantize_down(amount + (step or amount * 0.01), step) if step else amount * 1.01
    return amount


def run_preflight(
    settings: Settings,
    adapter: ExchangeAdapter,
    risk: RiskManager,
    *,
    today: datetime | None = None,
) -> list[Check]:
    checks: list[Check] = []
    live = settings.mode is TradingMode.LIVE
    symbol = settings.exchange.symbol
    base, quote = symbol.split("/", 1)

    checks.append(check_key_verified_date(settings, today))

    try:
        adapter.connect()
        offset = float(getattr(adapter, "server_offset_ms", 0.0))
        checks.append(
            Check(
                "clock",
                "jam lokal terhadap server (pemanasan, sampel, rtt terkecil)",
                True,
                f"selisih terbaik {offset:+.0f} ms, batas "
                f"{settings.exchange.max_time_drift_ms} ms; "
                "lihat log untuk rtt dan jumlah sampel",
            )
        )
    except ExchangeError as exc:
        checks.append(Check("clock", "koneksi dan jam", False, str(exc)))
        return checks

    try:
        limits = adapter.fetch_market_limits(symbol)
        ok = limits.min_cost is not None or limits.min_amount is not None
        checks.append(
            Check(
                "market",
                "pasangan ada di load_markets dan minimum notional terbaca",
                ok,
                f"min_cost={limits.min_cost} {quote}, min_amount={limits.min_amount} {base}, "
                f"amount_step={limits.amount_step}",
            )
        )
    except ExchangeError as exc:
        checks.append(Check("market", "pasangan dan batas pasar", False, str(exc)))
        return checks

    # Paper membungkus adapter publik venue: yang ditanya adalah venue-nya, bukan simulasinya.
    venue_adapter = getattr(adapter, "public", adapter)
    stops_ok = bool(venue_adapter.supports_exchange_stops)
    checks.append(
        Check(
            "stops",
            "venue mengiklankan STOP_LOSS_LIMIT untuk pair (lapis 2)",
            stops_ok,
            "didukung"
            if stops_ok
            else "tidak dilaporkan; di live ini fatal, di paper dan testnet peringatan",
        )
    )

    equity = settings.backtest.initial_equity
    if adapter.can_trade and live:
        try:
            balance = adapter.fetch_balance()
            ticker = adapter.fetch_ticker(symbol)
            equity = balance.total(quote) + balance.total(base) * ticker.last
            checks.append(
                Check(
                    "balance",
                    "kunci bisa membaca saldo (hanya baca; withdrawal tidak pernah dicoba)",
                    True,
                    f"{balance.total(quote):.4f} {quote}, {balance.total(base):.6f} {base}, "
                    f"equity {equity:.4f} {quote}",
                )
            )
        except ExchangeError as exc:
            checks.append(Check("balance", "kunci bisa membaca saldo", False, str(exc)))
            return checks
    else:
        checks.append(
            Check(
                "balance",
                "kunci bisa membaca saldo",
                True,
                f"tidak berlaku di mode {settings.mode.value}; equity dianggap "
                f"{equity:.2f} {quote} untuk cek sizing",
                applicable=False,
            )
        )

    try:
        ticker = adapter.fetch_ticker(symbol)
        fill = ticker.last * (1 + settings.costs.slippage_rate)
        sized = risk.size_position(equity, fill, limits)
        first = minimum_order_amount(limits, fill)
        checks.append(
            Check(
                "sizing",
                "ukuran posisi hasil sizing dan ukuran minimum di atas minimum exchange",
                True,
                f"sizing {sized} {base} (~{sized * fill:.2f} {quote}), order pertama live "
                f"{first} {base} (~{first * fill:.2f} {quote}) pada harga {ticker.last}",
            )
        )
    except (RiskError, ExchangeError) as exc:
        checks.append(Check("sizing", "ukuran posisi di atas minimum exchange", False, str(exc)))
    return checks


def format_preflight(checks: list[Check], settings: Settings) -> str:
    lines = [f"preflight {settings.exchange.symbol}, mode {settings.mode.value}:"]
    for check in checks:
        mark = "OK" if check.ok else "GAGAL"
        if not check.applicable:
            mark = "n/a"
        lines.append(f"[{mark:5s}] {check.title}")
        lines.append(f"        {check.detail}")
    ready = all(c.ok for c in checks)
    if ready and settings.mode is not TradingMode.LIVE:
        lines.append(
            "semua yang bisa dicek di mode ini lulus; butir kunci live baru teruji di mode live"
        )
    elif ready:
        lines.append("SIAP: semua pemeriksaan lulus. Order tetap belum dikirim oleh preflight.")
    else:
        lines.append("BELUM SIAP: ada pemeriksaan yang gagal; perbaiki dulu.")
    return "\n".join(lines)
