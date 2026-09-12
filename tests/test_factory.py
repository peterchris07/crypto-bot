"""Gerbang dua kunci sampai ke factory: mode menentukan venue, kunci, dan izin mainnet."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from tests.fakes import FakeCcxtClient, FakeTokocryptoClient
from tradebot.config import ConfigError, TradingMode, load_settings
from tradebot.exchange import FatalExchangeError
from tradebot.exchange.ccxt_adapter import CcxtAdapter
from tradebot.exchange.factory import build_adapter
from tradebot.exchange.tokocrypto_adapter import TokocryptoAdapter

KEY = "factory-key-0123456789"
SECRET = "factory-secret-9876543210"


def test_paper_uses_live_venue_without_keys(config_path: Path):
    settings = load_settings(config_path, environ={})
    adapter = build_adapter(settings, client_factory=FakeTokocryptoClient)
    assert settings.mode is TradingMode.PAPER
    assert settings.venue.id == "tokocrypto"
    assert isinstance(adapter, TokocryptoAdapter)
    assert adapter.can_trade is False
    assert adapter.is_sandbox is False
    assert adapter.name == "tokocrypto-mainnet-public"


def test_testnet_uses_binance_sandbox(project_dir: Path, config_path: Path):
    (project_dir / ".env").write_text(
        f"TRADING_MODE=testnet\nBINANCE_TESTNET_API_KEY={KEY}\nBINANCE_TESTNET_API_SECRET={SECRET}\n"
    )
    settings = load_settings(config_path, environ={})
    adapter = build_adapter(settings, client_factory=FakeCcxtClient)
    assert isinstance(adapter, CcxtAdapter)
    assert adapter.is_sandbox is True
    assert adapter.can_trade is True
    assert adapter.name == "binance-testnet-trading"


def test_live_requires_env_and_flag_then_builds_tokocrypto_trading(
    project_dir: Path, config_path: Path, caplog
):
    """Aturan keras 1: config minta mainnet, tanpa flag tidak ada klien sama sekali."""
    (project_dir / ".env").write_text(
        f"TRADING_MODE=live\nTOKOCRYPTO_API_KEY={KEY}\nTOKOCRYPTO_API_SECRET={SECRET}\n"
    )
    with pytest.raises(ConfigError, match="menolak"):
        load_settings(config_path, environ={})

    caplog.set_level(logging.WARNING, logger="tradebot")
    settings = load_settings(config_path, environ={}, i_know_what_im_doing=True)
    adapter = build_adapter(settings, client_factory=FakeTokocryptoClient)
    assert isinstance(adapter, TokocryptoAdapter)
    assert adapter.name == "tokocrypto-mainnet-trading"
    assert adapter.can_trade is True
    assert "uang asli" in caplog.text


def test_live_with_binance_keys_only_is_refused(project_dir: Path, config_path: Path):
    """Kunci Binance tidak lagi membuka mode live; variabelnya sekarang TOKOCRYPTO_*."""
    (project_dir / ".env").write_text(
        f"TRADING_MODE=live\nBINANCE_API_KEY={KEY}\nBINANCE_API_SECRET={SECRET}\n"
    )
    with pytest.raises(ConfigError, match="TOKOCRYPTO_API_KEY"):
        load_settings(config_path, environ={}, i_know_what_im_doing=True)


def test_unknown_venue_id_is_fatal(project_dir: Path, config_path: Path):
    raw = yaml.safe_load(config_path.read_text())
    raw["exchange"]["live"]["id"] = "indodax"
    config_path.write_text(yaml.safe_dump(raw))
    settings = load_settings(config_path, environ={})
    with pytest.raises(FatalExchangeError, match="indodax"):
        build_adapter(settings)


def test_live_binance_venue_uses_binance_keys_not_tokocrypto(project_dir: Path, config_path: Path):
    raw = yaml.safe_load(config_path.read_text())
    raw["exchange"]["live"]["id"] = "binance"
    raw["exchange"]["live"]["market_data_url"] = ""
    config_path.write_text(yaml.safe_dump(raw))
    (project_dir / ".env").write_text(
        f"TRADING_MODE=live\nTOKOCRYPTO_API_KEY={KEY}\nTOKOCRYPTO_API_SECRET={SECRET}\n"
    )
    with pytest.raises(ConfigError, match="BINANCE_API_KEY"):
        load_settings(config_path, environ={}, i_know_what_im_doing=True)


def test_public_adapter_ignores_mode_and_keys(project_dir: Path, config_path: Path):
    """fetch-data memakai venue live tanpa kunci walau .env berisi mode testnet dan kunci."""
    from tradebot.exchange.factory import build_public_adapter

    env = {
        "TRADING_MODE": "testnet",
        "BINANCE_TESTNET_API_KEY": "testnet-key-0123456789",
        "BINANCE_TESTNET_API_SECRET": "testnet-secret-0123456789",
        "TOKOCRYPTO_API_KEY": "toko-key-0123456789",
        "TOKOCRYPTO_API_SECRET": "toko-secret-0123456789",
    }
    settings = load_settings(config_path, environ=env)
    assert settings.mode is TradingMode.TESTNET
    adapter = build_public_adapter(settings, client_factory=FakeTokocryptoClient)
    assert isinstance(adapter, TokocryptoAdapter)
    assert adapter.name == "tokocrypto-mainnet-public"
    assert not adapter.can_trade
    assert "apiKey" not in adapter._client.params
