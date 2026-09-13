"""Tahap 1: aturan keras 2 di tingkat repositori, bukan cuma di kode."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _gitignore_lines() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_env_is_in_gitignore():
    lines = _gitignore_lines()
    assert ".env" in lines
    assert ".env.*" in lines
    assert "!.env.example" in lines, ".env.example justru harus masuk git"


@pytest.mark.parametrize("folder", ["/data/", "/logs/", "/state/", "/trades/"])
def test_runtime_folders_are_ignored(folder: str):
    """Pola dijangkar ke root: 'data/' tanpa jangkar ikut mengabaikan src/tradebot/data/."""
    assert folder in _gitignore_lines()


def test_local_config_overlay_is_ignored():
    """config/local.yaml berisi live.enabled dan tanggal verifikasi milik pemilik, bukan repo."""
    assert "/config/local.yaml" in _gitignore_lines()
    assert not (ROOT / "config" / "local.yaml").exists() or (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", "config/local.yaml"],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        != 0
    ), "config/local.yaml ikut ter-commit"


def test_env_example_has_names_only():
    pattern = re.compile(r"^[A-Z][A-Z0-9_]*=$")
    lines = [
        line.strip()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines, ".env.example kosong"
    for line in lines:
        assert pattern.match(line), f"baris ini punya nilai atau format salah: {line!r}"
    names = {line.rstrip("=") for line in lines}
    assert {
        "TRADING_MODE",
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "TOKOCRYPTO_API_KEY",
        "TOKOCRYPTO_API_SECRET",
    } <= names


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def _in_git_repo() -> bool:
    return _git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def test_git_ignores_env_file():
    if not _in_git_repo():
        pytest.fail(
            "project belum berupa repositori git; .gitignore harus berlaku sejak commit pertama"
        )
    # check-ignore keluar 0 kalau path akan diabaikan, tidak peduli file-nya ada atau tidak.
    assert _git("check-ignore", "-q", ".env").returncode == 0
    assert _git("check-ignore", "-q", "state/orders.jsonl").returncode == 0
    assert _git("check-ignore", "-q", ".env.example").returncode == 1


def test_runtime_folders_ignored_only_at_root():
    """Folder runtime diabaikan di root, tapi paket sumber dengan nama sama tidak.

    Paket src/tradebot/data pernah hilang dari git karena pola 'data/' tanpa jangkar,
    sehingga clone baru gagal import padahal test di mesin pengembang hijau. File yang
    sudah dilacak tidak pernah dilaporkan check-ignore, jadi yang diuji adalah path
    HIPOTETIS yang belum ada, dengan --no-index, supaya regresi pola tetap terlihat.
    """
    if not _in_git_repo():
        pytest.fail("project belum berupa repositori git")
    lines = set(_gitignore_lines())
    unanchored = {"data/", "logs/", "state/", "trades/", "STOP"} & lines
    assert not unanchored, f"pola tanpa jangkar mengabaikan folder sumber juga: {unanchored}"
    assert not [line for line in lines if line.startswith("**/")]
    for path in ("data/x.parquet", "logs/tradebot.log", "state/risk_state.json", "trades/t.csv"):
        assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, (
            f"{path} harus diabaikan"
        )
    for path in (
        "src/tradebot/data/modul_baru.py",
        "src/tradebot/logs/x.py",
        "src/tradebot/state/x.py",
        "src/tradebot/trades/x.py",
        "tests/data/x.py",
    ):
        assert _git("check-ignore", "-q", "--no-index", path).returncode == 1, (
            f"{path} tidak boleh diabaikan"
        )
    tracked = _git("ls-files", "src/tradebot/data").stdout.splitlines()
    assert "src/tradebot/data/ohlcv.py" in tracked, "paket data harus dilacak git"
    assert "src/tradebot/data/__init__.py" in tracked


def test_env_file_is_never_tracked():
    if not _in_git_repo():
        pytest.fail("project belum berupa repositori git")
    tracked = _git("ls-files").stdout.splitlines()
    assert ".env" not in tracked
    assert not [p for p in tracked if p.startswith(".env.") and p != ".env.example"]


def test_no_secret_looking_values_in_tracked_files():
    """Cari nilai kunci di file yang dilacak git. Nama variabel boleh, nilainya tidak."""
    if not _in_git_repo():
        pytest.fail("project belum berupa repositori git")
    # Kunci Binance asli panjangnya 64 karakter alfanumerik. Nilai palsu di test jauh lebih pendek.
    pattern = re.compile(
        r"(?:BINANCE(?:_TESTNET)?|TOKOCRYPTO)_API_(?:KEY|SECRET)\s*=\s*['\"]?[A-Za-z0-9]{32,}"
    )
    hits = []
    for rel in _git("ls-files").stdout.splitlines():
        path = ROOT / rel
        if path.suffix in {".png", ".parquet", ".lock"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            hits.append(rel)
    assert not hits, f"nilai kunci ditemukan di: {hits}"
