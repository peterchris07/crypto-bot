"""Celah tercatat di jalur rekonsiliasi Tokocrypto. Butuh kunci Tokocrypto (hanya baca).

Tidak ada order yang dikirim. Test ini memeriksa apakah pencarian order lewat
client id masih menemukan order yang sudah keluar dari jendela riwayat
(ORDER_HISTORY_LIMIT order terakhir). Kondisi munculnya celah: bot mati beberapa
jam setelah mengirim order, sementara akun terus bertransaksi sehingga order itu
bukan lagi salah satu order terbaru. Selama kunci belum ada, test ini dilewati
dan tercatat di ringkasan skip; selama celahnya belum ditutup, test ini
diharapkan gagal pada akun dengan riwayat panjang.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tradebot.config import load_settings
from tradebot.exchange import OrderNotFoundError
from tradebot.exchange.factory import build_adapter
from tradebot.exchange.tokocrypto_adapter import ORDER_HISTORY_LIMIT

pytestmark = pytest.mark.tokocrypto

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def keyed():
    settings = load_settings(
        ROOT / "config" / "default.yaml",
        environ={**os.environ, "TRADING_MODE": "live"},
        i_know_what_im_doing=True,
    )
    adapter = build_adapter(settings)
    adapter.connect()
    return adapter, settings


def test_history_scan_finds_order_older_than_window(keyed):
    adapter, settings = keyed
    symbol = settings.exchange.symbol
    client = adapter._client  # akses langsung hanya untuk membaca riwayat panjang
    history = client.fetch_orders(symbol, None, ORDER_HISTORY_LIMIT * 3, {"type": -1})
    if len(history) <= ORDER_HISTORY_LIMIT:
        pytest.skip(
            f"riwayat order {symbol} hanya {len(history)} baris, belum melebihi jendela "
            f"{ORDER_HISTORY_LIMIT}; celah belum bisa diuji di akun ini"
        )
    timestamps = [o.get("timestamp") for o in history if o.get("timestamp")]
    monotonic = timestamps in (sorted(timestamps), sorted(timestamps, reverse=True))
    assert monotonic, "urutan hasil endpoint tidak monoton; catat di SPEC"
    oldest = min(history, key=lambda o: o.get("timestamp") or 0)
    client_id = oldest.get("clientOrderId")
    if not client_id:
        pytest.skip("order tertua tidak punya client id")
    try:
        found = adapter.fetch_order(symbol, client_order_id=client_id)
    except OrderNotFoundError as exc:
        pytest.fail(f"celah rekonsiliasi terbukti: {exc}")
    assert found.id == str(oldest["id"])
