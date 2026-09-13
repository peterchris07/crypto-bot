"""Replikasi independen skrining H1 (periode riset saja) dari cache CSV yang sama.

    uv run python research/h1_momentum/replikasi.py <keluaran.json> <folder cache>

Ditulis
dengan matematika portofolio yang berbeda dari skrining.py:

- NAV harian dalam satu periode = NAV awal periode x rata-rata (close_ff_t / close_ff_awal)
  atas simbol yang dipegang; close_ff adalah forward-fill per segmen, sehingga simbol yang
  berhenti otomatis "beku" di close terakhirnya (setara keluar di close terakhir).
- Return per periode dirantai; tidak ada unit, tidak ada kas beku eksplisit.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/h1_klines_cache")
DATA = Path(__file__).resolve().parent / "data"
END = pd.Timestamp("2022-12-31")
STABLE_OR_RWA = {
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "USD1", "RLUSD", "EUR", "EURI", "AEUR",
    "PAXG", "XAUT", "USDE", "USDS", "PYUSD", "GBP", "TRY", "BRL", "UST", "USTC", "USDD",
    "WBTC", "WBETH", "BFUSD", "XUSD", "SUSD", "GUSD", "USDSB", "USDX",
}  # fmt: skip


def base_of(s):
    return s[:-4]


def excluded(sym, first):
    b = base_of(sym)
    if b in STABLE_OR_RWA:
        return True
    if b.endswith(("UP", "DOWN", "BULL", "BEAR")) and b not in {"JUP", "SUP"}:
        return True
    return b.endswith("B") and first >= "2025-06"


def load():
    inv = json.loads((DATA / "usdt_inventory.json").read_text())
    frames = []
    for item in inv:
        sym, first = item["symbol"], item["first_month"]
        if not first or first > "2022-12" or excluded(sym, first):
            continue
        for f in sorted(CACHE.glob(f"{sym}-1d-*.csv")):
            month = f.stem.split("-1d-")[1]
            if month > "2022-12":
                raise SystemExit(f"data di luar periode riset di cache: {f}")
            text = f.read_text()
            if not text.strip():
                continue
            rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]
            ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
            ts = np.where(ts > 10**14, ts // 1000, ts)
            frames.append(
                pd.DataFrame(
                    {
                        "date": pd.to_datetime(ts, unit="ms").normalize(),
                        "close": [float(r[4]) for r in rows],
                        "qvol": [float(r[7]) for r in rows],
                        "symbol": sym,
                    }
                )
            )
    raw = pd.concat(frames).drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    # putus seri pada lompatan close > 10x
    parts = []
    for sym, g in raw.groupby("symbol"):
        g = g.reset_index(drop=True)
        jump = (g["close"] / g["close"].shift(1)) > 10
        seg_id = jump.cumsum()
        g["symbol"] = [sym if k == 0 else f"{sym}~{k + 1}" for k in seg_id]
        parts.append(g)
    data = pd.concat(parts)
    idx = pd.date_range(data["date"].min(), END, freq="D")
    close = data.pivot(index="date", columns="symbol", values="close").reindex(idx)
    qvol = data.pivot(index="date", columns="symbol", values="qvol").reindex(idx)
    return close, qvol


def run(close, qvol, step):
    close_ff = close.ffill()
    idx = close.index
    # kelayakan per tanggal: close di t dan t-30 ada, >= 27 hari volume di jendela 30 hari
    vol_days = qvol.notna().rolling(30).sum()
    liq = qvol.fillna(0.0).rolling(30).sum()
    mom = close / close.shift(30) - 1
    eligible = close.notna() & close.shift(30).notna() & (vol_days >= 27)
    counts = eligible.sum(axis=1)
    start = counts.index[counts >= 10][0]
    dates = [start]
    while dates[-1] + pd.Timedelta(days=step) <= END:
        dates.append(dates[-1] + pd.Timedelta(days=step))
    nav_top = pd.Series(np.nan, index=idx)
    nav_bas = pd.Series(np.nan, index=idx)
    nav_top[start] = 1.0
    nav_bas[start] = 1.0
    v_top = v_bas = 1.0
    prev = set()
    rotations = []
    luna_notes = []
    for i, t in enumerate(dates):
        elig = eligible.loc[t]
        syms = elig.index[elig]
        if len(syms) < 10:
            continue
        universe = liq.loc[t, syms].sort_values(ascending=False).index[:20]
        top = mom.loc[t, universe].sort_values(ascending=False).index[:5]
        if prev:
            rotations.append(len(set(top) - prev))
        prev = set(top)
        if any(s.startswith("LUNAUSDT") for s in top) and pd.Timestamp(
            "2022-03-01"
        ) <= t <= pd.Timestamp("2022-07-01"):
            luna_notes.append(str(t.date()))
        t_next = dates[i + 1] if i + 1 < len(dates) else END
        window = close_ff.loc[t:t_next]
        rel_top = (window[top] / window[top].iloc[0]).mean(axis=1)
        rel_bas = (window[universe] / window[universe].iloc[0]).mean(axis=1)
        nav_top.loc[t:t_next] = v_top * rel_top.values
        nav_bas.loc[t:t_next] = v_bas * rel_bas.values
        v_top = float(nav_top[t_next])
        v_bas = float(nav_bas[t_next])
    nav_top = nav_top.dropna()
    nav_bas = nav_bas.dropna()
    btc = close_ff["BTCUSDT"].loc[nav_top.index[0] :]
    nav_btc = btc / btc.iloc[0]

    def stats(nav):
        yrs = (nav.index[-1] - nav.index[0]).days / 365.25
        return {
            "cum": float(nav.iloc[-1] - 1),
            "cagr": float(nav.iloc[-1] ** (1 / yrs) - 1),
            "mdd": float((nav / nav.cummax() - 1).min()),
        }

    return {
        "start": str(start.date()),
        "rebalances": len(dates),
        "avg_rotation": float(np.mean(rotations)),
        "top5": stats(nav_top),
        "basket20": stats(nav_bas),
        "btc": stats(nav_btc),
        "luna_top5_dates": luna_notes,
    }


def main():
    close, qvol = load()
    print(f"panel {close.shape[0]} hari x {close.shape[1]} segmen")
    print("segmen putus:", [c for c in close.columns if "~" in c])
    out = {}
    for step in (14, 28):
        r = run(close, qvol, step)
        out[f"rebalance_{step}d"] = r
        t5, b20 = r["top5"], r["basket20"]
        print(
            f"{step:2d}d: mulai {r['start']}, {r['rebalances']} rebalance, "
            f"rotasi {r['avg_rotation']:.2f}"
        )
        print(f"  top5 CAGR {t5['cagr']:.1%} (kum {t5['cum']:.1%}, MDD {t5['mdd']:.1%})")
        print(f"  basket CAGR {b20['cagr']:.1%} (kum {b20['cum']:.1%}, MDD {b20['mdd']:.1%})")
        print(f"  BTC CAGR {r['btc']['cagr']:.1%}; selisih/thn {t5['cagr'] - b20['cagr']:+.1%}")
        print(f"  LUNA di top-5 pada {r['luna_top5_dates']}")
    Path(sys.argv[1] if len(sys.argv) > 1 else "replikasi_hasil.json").write_text(
        json.dumps(out, indent=1)
    )


if __name__ == "__main__":
    main()
