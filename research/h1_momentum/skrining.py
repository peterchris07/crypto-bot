"""Skrining kotor H1 sesuai PRAREGISTRASI.md: top-5 momentum 30 hari vs keranjang bobot
sama 20 koin paling likuid, tanpa biaya, tanpa engine. HANYA periode riset (sampai
2022-12-31); validasi (2023-2024) dan holdout (2025 ke atas) tidak diunduh dan tidak
dihitung.

    uv run python research/h1_momentum/skrining.py [--cache DIR]

Keluaran: data/skrining_hasil.json dan tabel di layar. Klines mentah disimpan di --cache
(default: folder sementara di luar repo), bukan di repo.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from langkah0_arsip_binance import BASE, OUT, get  # noqa: E402

RESEARCH = (pd.Timestamp("2017-08-17"), pd.Timestamp("2022-12-31"))
VALIDATION = (pd.Timestamp("2023-01-01"), pd.Timestamp("2024-12-31"))
LAST_MONTH = "2022-12"  # riset saja: validasi dan holdout tidak diunduh
STABLE_OR_RWA = {
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "USD1", "RLUSD", "EUR", "EURI", "AEUR",
    "PAXG", "XAUT", "USDE", "USDS", "PYUSD", "GBP", "TRY", "BRL", "UST", "USTC", "USDD",
    "WBTC", "WBETH", "BFUSD", "XUSD", "SUSD", "GUSD", "USDSB", "USDX",
}  # fmt: skip
UNIVERSE_N = 20
TOP_M = 5
LOOKBACK_DAYS = 30
MIN_ELIGIBLE = 10
REBALANCE_DAYS = (14, 28)
BREAK_RATIO = 10.0


def base_of(symbol: str) -> str:
    return symbol[: -len("USDT")]


def is_leveraged(symbol: str) -> bool:
    b = base_of(symbol)
    return b.endswith(("UP", "DOWN", "BULL", "BEAR")) and b not in {"JUP", "SUP"}


def is_tokenized_stock(symbol: str, first_month: str) -> bool:
    return base_of(symbol).endswith("B") and first_month >= "2025-06"


def month_range(first: str, last: str) -> list[str]:
    start = datetime.strptime(first, "%Y-%m")
    end = datetime.strptime(last, "%Y-%m")
    months = []
    cur = start
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def fetch_month(cache: Path, symbol: str, month: str) -> pd.DataFrame | None:
    target = cache / f"{symbol}-1d-{month}.csv"
    if not target.exists():
        r = get(f"{BASE}/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip")
        if r.status_code == 404:
            target.write_text("")
        else:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            target.write_bytes(z.read(z.namelist()[0]))
    text = target.read_text()
    if not text.strip():
        return None
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]
    if not rows:
        return None
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    ts = np.where(ts > 10**14, ts // 1000, ts)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(ts, unit="ms", utc=True).tz_localize(None).normalize(),
            "close": [float(r[4]) for r in rows],
            "qvol": [float(r[7]) for r in rows],
        }
    ).assign(symbol=symbol)


def load_panel(cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    inventory = json.loads((OUT / "usdt_inventory.json").read_text())
    excluded = {
        "stable_rwa": [],
        "leverage": [],
        "tokenized_stock": [],
        "no_data_before_holdout": [],
    }
    jobs: list[tuple[str, str]] = []
    for item in inventory:
        sym, first, last = item["symbol"], item["first_month"], item["last_month"]
        if not first:
            continue
        if base_of(sym) in STABLE_OR_RWA:
            excluded["stable_rwa"].append(sym)
            continue
        if is_leveraged(sym):
            excluded["leverage"].append(sym)
            continue
        if is_tokenized_stock(sym, first):
            excluded["tokenized_stock"].append(sym)
            continue
        if first > LAST_MONTH:
            excluded["no_data_before_holdout"].append(sym)
            continue
        jobs.extend((sym, m) for m in month_range(first, min(last, LAST_MONTH)))
    print(f"simbol dipakai: {len({s for s, _ in jobs})}, file bulanan: {len(jobs)}", flush=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        frames = [f for f in pool.map(lambda j: fetch_month(cache, *j), jobs) if f is not None]
    raw = pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])
    raw = raw.drop_duplicates(["symbol", "date"])

    # Putus seri pada lompatan close > BREAK_RATIO dalam satu hari (relisting token baru
    # dengan simbol lama, misalnya LUNA setelah Mei 2022): segmen lama berakhir di close
    # sebelum lompatan, segmen baru berdiri sendiri.
    segments = []
    breaks = []
    for sym, g in raw.groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        ratio = g["close"] / g["close"].shift(1)
        cut_points = list(g.index[ratio > BREAK_RATIO])
        starts = [0, *cut_points]
        ends = [*cut_points, len(g)]
        for k, (a, b) in enumerate(zip(starts, ends, strict=True)):
            name = sym if k == 0 else f"{sym}~{k + 1}"
            seg = g.iloc[a:b].copy()
            seg["symbol"] = name
            segments.append(seg)
            if k > 0:
                breaks.append((sym, str(g.loc[a, "date"].date()), name))
    data = pd.concat(segments, ignore_index=True)
    close = data.pivot(index="date", columns="symbol", values="close").sort_index()
    qvol = data.pivot(index="date", columns="symbol", values="qvol").sort_index()
    full_index = pd.date_range(close.index.min(), close.index.max(), freq="D")
    close = close.reindex(full_index)
    qvol = qvol.reindex(full_index)
    meta = {"excluded": excluded, "series_breaks": breaks, "n_segments": int(close.shape[1])}
    return close, qvol, meta


def max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    return float((nav / peak - 1).min())


def cagr(nav: pd.Series) -> float:
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0


def simulate(close: pd.DataFrame, qvol: pd.DataFrame, step_days: int, end: pd.Timestamp):
    """NAV harian untuk top-5 momentum, keranjang-20, dan BTC, dari awal efektif sampai end."""
    dates = close.index
    lookback = LOOKBACK_DAYS
    rebalance_dates = []
    nav_top = pd.Series(np.nan, index=dates)
    nav_basket = pd.Series(np.nan, index=dates)
    rotations = []
    start_effective = None
    prev_top: set[str] = set()
    # posisi: {segment: unit} di mana nilai posisi = unit * close (unit tetap sampai rebalance)
    top_units: dict[str, float] = {}
    top_frozen = 0.0  # kas dari segmen yang berakhir
    basket_units: dict[str, float] = {}
    basket_frozen = 0.0
    value_top = 1.0
    value_basket = 1.0

    close_ff = close.ffill()  # hari tanpa bar (pemeliharaan) dinilai di close terakhir
    last_valid = {seg: close[seg].last_valid_index() for seg in close.columns}

    def portfolio_value(
        units: dict[str, float], frozen: float, t: pd.Timestamp
    ) -> tuple[float, dict, float]:
        total = frozen
        live_units = {}
        for seg, u in units.items():
            px = float(close_ff.at[t, seg])
            if t > last_valid[seg]:
                frozen += u * px  # segmen berakhir (delisting/putus seri): keluar di close terakhir
                total += u * px
            else:
                total += u * px
                live_units[seg] = u
        return total, live_units, frozen

    next_rebalance = None
    for t in dates:
        if t > end:
            break
        # nilai hari ini
        if start_effective is not None:
            value_top, top_units, top_frozen = portfolio_value(top_units, top_frozen, t)
            value_basket, basket_units, basket_frozen = portfolio_value(
                basket_units, basket_frozen, t
            )
            nav_top.at[t] = value_top
            nav_basket.at[t] = value_basket
        if next_rebalance is not None and t < next_rebalance:
            continue
        t0 = t - pd.Timedelta(days=lookback)
        if t0 < dates[0]:
            continue
        px_now = close.loc[t]
        px_then = close.loc[t0]
        window = qvol.loc[t0 + pd.Timedelta(days=1) : t]
        eligible = px_now.notna() & px_then.notna() & (window.notna().sum() >= lookback - 3)
        eligible_syms = list(px_now.index[eligible])
        if len(eligible_syms) < MIN_ELIGIBLE:
            next_rebalance = t + pd.Timedelta(days=step_days) if start_effective else None
            continue
        liquidity = window[eligible_syms].sum().sort_values(ascending=False)
        universe = list(liquidity.index[:UNIVERSE_N])
        momentum = (px_now[universe] / px_then[universe] - 1).sort_values(ascending=False)
        top = list(momentum.index[:TOP_M])
        if start_effective is None:
            start_effective = t
            nav_top.at[t] = 1.0
            nav_basket.at[t] = 1.0
            value_top = value_basket = 1.0
        rebalance_dates.append(t)
        if prev_top:
            rotations.append(len(set(top) - prev_top))
        prev_top = set(top)
        top_units = {s: (value_top / TOP_M) / float(px_now[s]) for s in top}
        top_frozen = 0.0
        basket_units = {s: (value_basket / len(universe)) / float(px_now[s]) for s in universe}
        basket_frozen = 0.0
        next_rebalance = t + pd.Timedelta(days=step_days)

    nav_top = nav_top.dropna()
    nav_basket = nav_basket.dropna()
    btc = close["BTCUSDT"].loc[nav_top.index[0] : nav_top.index[-1]].ffill()
    nav_btc = btc / btc.iloc[0]
    return {
        "start_effective": str(start_effective.date()),
        "rebalances": len(rebalance_dates),
        "avg_rotation": float(np.mean(rotations)) if rotations else 0.0,
        "nav_top": nav_top,
        "nav_basket": nav_basket,
        "nav_btc": nav_btc,
    }


def period_stats(nav: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict | None:
    part = nav.loc[start:end]
    if len(part) < 2:
        return None
    part = part / part.iloc[0]
    return {
        "from": str(part.index[0].date()),
        "to": str(part.index[-1].date()),
        "cumulative": float(part.iloc[-1] - 1),
        "cagr": cagr(part),
        "max_drawdown": max_drawdown(part),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=None, help="folder klines mentah (di luar repo)")
    args = parser.parse_args()
    cache = Path(args.cache) if args.cache else Path(tempfile.gettempdir()) / "h1_klines_cache"
    cache.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    close, qvol, meta = load_panel(cache)
    print(
        f"panel: {close.shape[0]} hari x {close.shape[1]} segmen; putus seri: "
        f"{meta['series_breaks']} ({time.time() - t0:.0f}s)",
        flush=True,
    )
    results = {"meta": meta, "configs": {}}
    for step in REBALANCE_DAYS:
        sim = simulate(close, qvol, step, RESEARCH[1])
        entry = {
            "start_effective": sim["start_effective"],
            "rebalances": sim["rebalances"],
            "avg_rotation_of_5": sim["avg_rotation"],
            "periods": {},
        }
        for name, (a, b) in (("riset", RESEARCH),):
            top = period_stats(sim["nav_top"], a, b)
            basket = period_stats(sim["nav_basket"], a, b)
            btc = period_stats(sim["nav_btc"], a, b)
            if top is None or basket is None:
                entry["periods"][name] = None
                continue
            entry["periods"][name] = {
                "top5": top,
                "basket20": basket,
                "btc": btc,
                "spread_cagr_top5_minus_basket": top["cagr"] - basket["cagr"],
                "spread_cagr_top5_minus_btc": top["cagr"] - (btc["cagr"] if btc else float("nan")),
            }
        results["configs"][f"rebalance_{step}d"] = entry

    (OUT / "skrining_hasil.json").write_text(json.dumps(results, indent=1, default=str))
    print()
    print(
        f"{'konfigurasi':<14} {'periode':<9} {'top5 CAGR':>10} {'basket20':>10} {'BTC':>8} "
        f"{'selisih/thn':>12} {'MDD top5':>9} {'MDD basket':>10}"
    )
    for cfg, entry in results["configs"].items():
        for name, p in entry["periods"].items():
            if p is None:
                print(f"{cfg:<14} {name:<9} (tidak ada data)")
                continue
            print(
                f"{cfg:<14} {name:<9} {p['top5']['cagr']:10.1%} {p['basket20']['cagr']:10.1%} "
                f"{(p['btc']['cagr'] if p['btc'] else float('nan')):8.1%} "
                f"{p['spread_cagr_top5_minus_basket']:12.1%} {p['top5']['max_drawdown']:9.1%} "
                f"{p['basket20']['max_drawdown']:10.1%}"
            )
        print(
            f"  awal efektif {entry['start_effective']}, {entry['rebalances']} rebalance, "
            f"rata-rata rotasi {entry['avg_rotation_of_5']:.2f} dari 5"
        )
    print(f"selesai {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
