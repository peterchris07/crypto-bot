"""Harga order uji dihitung dari band exchange (PERCENT_PRICE_BY_SIDE), bukan faktor tetap."""

from __future__ import annotations

import pytest

from tests.helpers import price_far_below_market
from tradebot.exchange import MarketLimits


def limits(band: float | None) -> MarketLimits:
    return MarketLimits(
        symbol="BTC/USDT",
        base="BTC",
        quote="USDT",
        min_amount=1e-05,
        amount_step=1e-05,
        min_cost=5.0,
        price_step=0.01,
        price_band_down=band,
    )


@pytest.mark.parametrize("band", [0.5, 0.2, 0.8])
def test_price_sits_inside_band_with_margin(band):
    last = 77_000.0
    price = price_far_below_market(last, limits(band))
    assert price > last * band, "harus di atas batas bawah band"
    assert price <= last * band * 1.1 + 0.01
    assert round(price / 0.01) == pytest.approx(price / 0.01), "kelipatan tick size"


def test_without_band_uses_half_price():
    assert price_far_below_market(100.0, limits(None)) == 50.0
