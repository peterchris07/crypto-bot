"""Alat bantu test yang bergantung pada batas exchange, bukan angka ajaib."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal

from tradebot.exchange import MarketLimits


def quantize(value: float, step: float | None, rounding: str) -> float:
    if not step:
        return value
    quantum = Decimal(str(step))
    units = (Decimal(str(value)) / quantum).quantize(Decimal(1), rounding=rounding)
    return float(units * quantum)


def price_far_below_market(last: float, limits: MarketLimits, *, margin: float = 0.1) -> float:
    """Harga limit buy yang tidak akan terisi tapi masih di dalam band harga exchange.

    Band bawah = last * price_band_down (PERCENT_PRICE_BY_SIDE.bidMultiplierDown). Harga
    dipilih tepat di atas band dengan margin, dibulatkan ke atas ke tick size, supaya
    perubahan filter oleh exchange tidak mematahkan test. Tanpa band, dipakai setengah harga.
    """
    band = limits.price_band_down
    fraction = band * (1 + margin) if band else 0.5
    return quantize(last * fraction, limits.price_step, ROUND_UP)


__all__ = ["ROUND_DOWN", "ROUND_UP", "price_far_below_market", "quantize"]
