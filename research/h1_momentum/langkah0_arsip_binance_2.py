"""Langkah 0 H1, bagian 2: volume 30 hari dari bar harian, tanggal bar pertama, dan
ukuran bias survivorship pada semesta top-20 di beberapa tanggal lampau.

Membaca keluaran langkah0_arsip_binance.py (inventaris bulanan) dari folder data/.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from langkah0_arsip_binance import BASE, OUT, get  # noqa: E402

STABLE_OR_RWA = {
    "USDC",
    "BUSD",
    "TUSD",
    "USDP",
    "DAI",
    "FDUSD",
    "USD1",
    "RLUSD",
    "EUR",
    "EURI",
    "AEUR",
    "PAXG",
    "XAUT",
    "USDE",
    "USDS",
    "PYUSD",
    "GBP",
    "TRY",
    "BRL",
    "UST",
    "USTC",
    "USDD",
    "WBTC",
    "WBETH",
    "BFUSD",
    "XUSD",
    "SUSD",
    "GUSD",
    "USDSB",
    "USDX",
}
START = date(2026, 8, 13)
DAYS = 30
SNAPSHOTS = ["2021-12", "2022-12", "2023-12", "2024-12", "2025-12", "2026-06"]
CUTOFF_ACTIVE = "2026-08"


def base_of(symbol: str) -> str:
    return symbol[: -len("USDT")]


def is_leveraged(symbol: str) -> bool:
    b = base_of(symbol)
    return b.endswith(("UP", "DOWN", "BULL", "BEAR")) and b not in {"JUP", "SUP", "DOWN"}


def to_ms(raw: int) -> int:
    return raw // 1000 if raw > 10**14 else raw


def read_zip_rows(url: str) -> list[list[str]] | None:
    r = get(url)
    if r.status_code == 404:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")))
    return [row for row in rows if row and row[0].isdigit()]


def daily_quote_volume(symbol: str) -> dict:
    total = 0.0
    days = 0
    missing = []
    for i in range(DAYS):
        d = START + timedelta(days=i)
        url = f"{BASE}/data/spot/daily/klines/{symbol}/1d/{symbol}-1d-{d.isoformat()}.zip"
        rows = read_zip_rows(url)
        if not rows:
            missing.append(d.isoformat())
            continue
        total += float(rows[0][7])
        days += 1
    return {"symbol": symbol, "quote_volume_30d": total, "days": days, "missing": missing}


def first_bar(symbol: str, first_month: str) -> dict:
    url = f"{BASE}/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{first_month}.zip"
    rows = read_zip_rows(url) or []
    if not rows:
        return {"symbol": symbol, "first_bar": None}
    ms = to_ms(int(rows[0][0]))
    return {
        "symbol": symbol,
        "first_bar": datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat(),
    }


def month_volume(symbol: str, month: str) -> tuple[str, float, int]:
    url = f"{BASE}/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"
    rows = read_zip_rows(url) or []
    return symbol, sum(float(r[7]) for r in rows), len(rows)


def main() -> None:
    t0 = time.time()
    inventory = json.loads((OUT / "usdt_inventory.json").read_text())
    by_symbol = {i["symbol"]: i for i in inventory}
    aug = json.loads((OUT / "usdt_volume_2026-08.json").read_text())
    aug_ranked = sorted(aug, key=lambda v: -v["quote_volume"])
    candidates = [v["symbol"] for v in aug_ranked[:110]]

    with ThreadPoolExecutor(max_workers=16) as pool:
        vol30 = list(pool.map(daily_quote_volume, candidates))
    (OUT / "usdt_volume_30d.json").write_text(json.dumps(vol30, indent=1))
    ranked = sorted(vol30, key=lambda v: -v["quote_volume_30d"])
    end = START + timedelta(days=DAYS - 1)
    print(f"volume 30 hari ({START} s/d {end}) selesai {time.time() - t0:.0f}s")

    eligible = [
        v
        for v in ranked
        if base_of(v["symbol"]) not in STABLE_OR_RWA and not is_leveraged(v["symbol"])
    ]
    top = eligible[:40]
    with ThreadPoolExecutor(max_workers=16) as pool:
        firsts = list(
            pool.map(lambda v: first_bar(v["symbol"], by_symbol[v["symbol"]]["first_month"]), top)
        )
    first_by = {f["symbol"]: f["first_bar"] for f in firsts}
    print("\n30 teratas volume kuotasi 30 hari (tanpa stablecoin, RWA, token leverage):")
    head = ("#", "simbol", "vol 30h (juta USDT)", "hari", "bar harian pertama")
    print(f"{head[0]:>2} {head[1]:<12} {head[2]:>20} {head[3]:>4} {head[4]:>20}")
    for i, v in enumerate(top[:30], 1):
        mio = v["quote_volume_30d"] / 1e6
        print(f"{i:2d} {v['symbol']:<12} {mio:20.1f} {v['days']:4d} {first_by[v['symbol']]:>20}")
    print("\nDikeluarkan dari 40 teratas mentah karena stablecoin/RWA/leverage:")
    print(", ".join(v["symbol"] for v in ranked[:40] if v not in eligible[:40]))
    (OUT / "top30.json").write_text(
        json.dumps([{**v, "first_bar": first_by[v["symbol"]]} for v in top[:30]], indent=1)
    )

    # Survivorship: top-20 menurut volume bulanan pada beberapa tanggal lampau, lalu berapa
    # yang sudah tidak diperdagangkan per Agustus 2026.
    report = []
    for month in SNAPSHOTS:
        live_then = [
            s
            for s, i in by_symbol.items()
            if i["first_month"]
            and i["first_month"] <= month <= i["last_month"]
            and base_of(s) not in STABLE_OR_RWA
            and not is_leveraged(s)
        ]
        with ThreadPoolExecutor(max_workers=16) as pool:
            vols = list(pool.map(lambda s, m=month: month_volume(s, m), live_then))
        vols = [v for v in vols if v[2] > 0]
        vols.sort(key=lambda v: -v[1])
        top20 = [v[0] for v in vols[:20]]
        gone = [s for s in top20 if by_symbol[s]["last_month"] < CUTOFF_ACTIVE]
        top50 = [v[0] for v in vols[:50]]
        gone50 = [s for s in top50 if by_symbol[s]["last_month"] < CUTOFF_ACTIVE]
        all_gone = [s for s in live_then if by_symbol[s]["last_month"] < CUTOFF_ACTIVE]
        entry = {
            "month": month,
            "usdt_pairs_then": len(live_then),
            "delisted_since_all": len(all_gone),
            "top20": top20,
            "top20_delisted_since": gone,
            "top50_delisted_since": gone50,
        }
        report.append(entry)
        print(
            f"\n{month}: pasangan USDT aktif {len(live_then)}, sudah hilang per {CUTOFF_ACTIVE}: "
            f"{len(all_gone)} ({100 * len(all_gone) / max(1, len(live_then)):.0f}%); "
            f"top-20 yang hilang: {len(gone)} {gone}; top-50 yang hilang: {len(gone50)}"
        )
        print("  top-20 saat itu: " + ", ".join(top20))
    (OUT / "survivorship.json").write_text(json.dumps(report, indent=1))
    print(f"\nselesai {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
