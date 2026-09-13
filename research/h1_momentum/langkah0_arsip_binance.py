"""Langkah 0 H1: inventaris pasangan USDT di arsip publik Binance (buku yang dibagi Tokocrypto).

Sumber: bucket S3 data.binance.vision, dijangkau lewat host S3 (CDN-nya diblokir di
lingkungan build).
Keluaran: JSON inventaris per simbol (bulan pertama, bulan terakhir, jumlah bulan) dan
volume kuotasi Agustus 2026 untuk simbol yang masih aktif.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)
SESSION = requests.Session()
CA = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"
if os.path.exists(CA):
    SESSION.verify = CA


def get(url: str, tries: int = 5) -> requests.Response:
    for attempt in range(tries):
        try:
            r = SESSION.get(url, timeout=60)
            if r.status_code in (200, 404):
                return r
        except requests.RequestException as exc:  # noqa: PERF203
            err = exc
        else:
            err = RuntimeError(f"status {r.status_code}")
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"gagal {url}: {err}")


def list_all(prefix: str) -> tuple[list[str], list[str]]:
    """Kembalikan (common prefixes, keys) untuk satu prefix, dengan paginasi marker."""
    prefixes: list[str] = []
    keys: list[str] = []
    marker = ""
    while True:
        url = f"{BASE}?delimiter=/&prefix={prefix}&max-keys=1000"
        if marker:
            url += f"&marker={marker}"
        root = ET.fromstring(get(url).text)
        for cp in root.findall("s3:CommonPrefixes/s3:Prefix", NS):
            prefixes.append(cp.text or "")
        for k in root.findall("s3:Contents/s3:Key", NS):
            keys.append(k.text or "")
        truncated = (root.findtext("s3:IsTruncated", default="false", namespaces=NS) or "").lower()
        if truncated != "true":
            break
        marker = root.findtext("s3:NextMarker", default="", namespaces=NS) or (
            (keys or prefixes)[-1] if (keys or prefixes) else ""
        )
        if not marker:
            break
    return prefixes, keys


def symbol_months(symbol: str) -> dict:
    _, keys = list_all(f"data/spot/monthly/klines/{symbol}/1d/")
    months = sorted(
        k.rsplit("-", 2)[-2] + "-" + k.rsplit("-", 2)[-1].replace(".zip", "")
        for k in keys
        if k.endswith(".zip")
    )
    return {
        "symbol": symbol,
        "first_month": months[0] if months else None,
        "last_month": months[-1] if months else None,
        "n_months": len(months),
    }


def month_quote_volume(symbol: str, month: str) -> dict:
    url = f"{BASE}/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"
    r = get(url)
    if r.status_code == 404:
        return {"symbol": symbol, "month": month, "days": 0, "quote_volume": 0.0, "missing": True}
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = z.namelist()[0]
    rows = list(csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8")))
    rows = [row for row in rows if row and row[0].isdigit()]
    qv = sum(float(row[7]) for row in rows)
    first_open = int(rows[0][0]) if rows else None
    return {
        "symbol": symbol,
        "month": month,
        "days": len(rows),
        "quote_volume": qv,
        "first_open_raw": first_open,
        "missing": False,
    }


def main() -> None:
    t0 = time.time()
    prefixes, _ = list_all("data/spot/monthly/klines/")
    symbols = sorted(p.rstrip("/").rsplit("/", 1)[-1] for p in prefixes)
    usdt = [s for s in symbols if s.endswith("USDT")]
    print(f"simbol total {len(symbols)}, USDT {len(usdt)} ({time.time() - t0:.0f}s)", flush=True)
    (OUT / "symbols_all.json").write_text(json.dumps(symbols))

    with ThreadPoolExecutor(max_workers=16) as pool:
        inventory = list(pool.map(symbol_months, usdt))
    (OUT / "usdt_inventory.json").write_text(json.dumps(inventory, indent=1))
    active = [i["symbol"] for i in inventory if i["last_month"] and i["last_month"] >= "2026-08"]
    print(
        f"inventaris selesai: {len(inventory)} USDT, aktif per Agustus 2026: {len(active)} "
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=16) as pool:
        volumes = list(pool.map(lambda s: month_quote_volume(s, "2026-08"), active))
    (OUT / "usdt_volume_2026-08.json").write_text(json.dumps(volumes, indent=1))
    ranked = sorted(volumes, key=lambda v: -v["quote_volume"])
    print("40 teratas volume kuotasi USDT Agustus 2026:")
    for i, v in enumerate(ranked[:40], 1):
        print(
            f"{i:2d} {v['symbol']:<14} {v['quote_volume'] / 1e6:12.1f} juta USDT  hari={v['days']}"
        )
    print(f"selesai {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
