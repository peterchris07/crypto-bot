"""Langkah 0 H1, sisi Tokocrypto: jalankan di Mac (host Tokocrypto tidak terjangkau dari
lingkungan build). Memeriksa apakah kandidat semesta dari arsip Binance memang
terdaftar di Tokocrypto sekarang, dan sejak kapan bar harian tersedia di host data
Tokocrypto.

    uv run python research/h1_momentum/langkah0_tokocrypto.py

Keluaran: research/h1_momentum/data/tokocrypto_check.json dan tabel di layar.
Tidak butuh kunci; hanya endpoint publik.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
MARKET_DATA_URL = "https://www.tokocrypto.site/api/v3"


def get(path: str, **params) -> object:
    for attempt in range(5):
        try:
            r = requests.get(f"{MARKET_DATA_URL}/{path}", params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            err: object = f"status {r.status_code}: {r.text[:200]}"
        except requests.RequestException as exc:
            err = exc
        time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"gagal {path}: {err}")


def main() -> int:
    candidates = json.loads((DATA / "top30.json").read_text(encoding="utf-8"))
    info = get("exchangeInfo")
    symbols = info["symbols"] if isinstance(info, dict) else []
    listed = {
        s["symbol"]: s
        for s in symbols
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
    }
    print(f"pasangan USDT berstatus TRADING di Tokocrypto sekarang: {len(listed)}")
    out = []
    header = ("simbol", "terdaftar", "bar harian pertama di Tokocrypto", "arsip Binance")
    print(f"{header[0]:<12} {header[1]:>9} {header[2]:>34} {header[3]:>14}")
    for c in candidates:
        sym = c["symbol"]
        first = None
        if sym in listed:
            rows = get("klines", symbol=sym, interval="1d", startTime=0, limit=1)
            if rows:
                ms = int(rows[0][0])
                ms = ms // 1000 if ms > 10**14 else ms
                first = datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()
        out.append(
            {
                "symbol": sym,
                "listed_now": sym in listed,
                "first_bar_tokocrypto": first,
                "first_bar_binance_archive": c.get("first_bar"),
            }
        )
        listed_text = "ya" if sym in listed else "TIDAK"
        print(f"{sym:<12} {listed_text:>9} {first or '-':>34} {c.get('first_bar') or '-':>14}")
    missing = [o["symbol"] for o in out if not o["listed_now"]]
    print(f"\nkandidat yang TIDAK terdaftar di Tokocrypto: {len(missing)} {missing}")
    DATA.mkdir(exist_ok=True)
    (DATA / "tokocrypto_check.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"ditulis {DATA / 'tokocrypto_check.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
