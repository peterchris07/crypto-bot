"""Kalimat dokumen yang menurut review lebih kuat dari kode, atau salah nama."""

from __future__ import annotations

from pathlib import Path

from tradebot.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent


def test_no_reference_to_dissolved_addendum_in_source():
    hits = [p for p in (ROOT / "src").rglob("*.py") if "addendum" in p.read_text().lower()]
    assert hits == []


def test_cli_and_package_do_not_claim_binance_only():
    description = build_parser().description
    assert "spot Binance" not in description
    assert "Tokocrypto" in description
    assert "spot Binance" not in (ROOT / "pyproject.toml").read_text()
    assert "spot Binance" not in (ROOT / "src" / "tradebot" / "__init__.py").read_text()


def test_spec_names_real_error_classes_and_avoids_overclaims():
    spec = (ROOT / "SPEC.md").read_text()
    assert "RetryableExchangeError" in spec and "FatalExchangeError" in spec
    assert "RetryableError vs FatalError" not in spec
    assert "sisanya tidak berubah" not in spec
    assert "hanya bisa dibuat lewat factory" not in spec
    assert "Sumber:" in spec, "catatan kepatuhan harus menyebut sumbernya"


def test_readme_fee_history_matches_config():
    readme = (ROOT / "README.md").read_text()
    assert "tiga kali dalam 2026" not in readme
