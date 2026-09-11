"""Tahap 1: resolusi mode, kunci API, dan pemuatan config."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tradebot.config import (
    LIVE_FLAG,
    ConfigError,
    Credentials,
    TradingMode,
    describe,
    load_credentials,
    load_settings,
    mask_secret,
    read_env,
    resolve_mode,
)

# --------------------------------------------------------------------------- #
# Aturan keras 1: default paper, live butuh TRADING_MODE=live DAN flag
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("env_value", [None, "", "   ", "paper", "PAPER", " Paper "])
def test_default_mode_is_paper(env_value):
    assert resolve_mode(env_value, i_know_what_im_doing=False) is TradingMode.PAPER


def test_testnet_needs_no_flag():
    assert resolve_mode("testnet", i_know_what_im_doing=False) is TradingMode.TESTNET


def test_live_requires_both_env_and_flag():
    assert resolve_mode("live", i_know_what_im_doing=True) is TradingMode.LIVE


def test_live_env_without_flag_is_refused():
    with pytest.raises(ConfigError, match=LIVE_FLAG.replace("-", r"\-")):
        resolve_mode("live", i_know_what_im_doing=False)


@pytest.mark.parametrize("env_value", [None, "", "paper", "testnet"])
def test_flag_without_live_env_is_refused(env_value):
    """Flag sendirian tidak boleh diam-diam jadi paper: pengguna jelas bermaksud lain."""
    with pytest.raises(ConfigError, match="TRADING_MODE"):
        resolve_mode(env_value, i_know_what_im_doing=True)


@pytest.mark.parametrize("env_value", ["mainnet", "prod", "sandbox", "1", "true"])
def test_unknown_mode_is_refused(env_value):
    with pytest.raises(ConfigError, match="tidak dikenal"):
        resolve_mode(env_value, i_know_what_im_doing=False)


# --------------------------------------------------------------------------- #
# Aturan keras 2: kunci hanya dari .env, tidak pernah bocor lewat repr
# --------------------------------------------------------------------------- #


def test_paper_needs_no_credentials():
    assert load_credentials(TradingMode.PAPER, {}) is None


def test_testnet_missing_keys_names_the_variables():
    with pytest.raises(ConfigError) as exc:
        load_credentials(TradingMode.TESTNET, {})
    message = str(exc.value)
    assert "BINANCE_TESTNET_API_KEY" in message
    assert "BINANCE_TESTNET_API_SECRET" in message
    assert ".env" in message


def test_live_missing_secret_only_names_the_secret():
    env = {"BINANCE_API_KEY": "k" * 20}
    with pytest.raises(ConfigError) as exc:
        load_credentials(TradingMode.LIVE, env)
    message = str(exc.value)
    assert "BINANCE_API_SECRET" in message
    assert "BINANCE_API_KEY " not in message


def test_testnet_and_live_use_different_variables():
    env = {
        "BINANCE_TESTNET_API_KEY": "testnet-key-1234567890",
        "BINANCE_TESTNET_API_SECRET": "testnet-secret-1234567890",
        "BINANCE_API_KEY": "mainnet-key-1234567890",
        "BINANCE_API_SECRET": "mainnet-secret-1234567890",
    }
    testnet = load_credentials(TradingMode.TESTNET, env)
    live = load_credentials(TradingMode.LIVE, env)
    assert testnet.api_key.startswith("testnet")
    assert live.api_key.startswith("mainnet")


def test_credentials_repr_and_str_never_contain_secret():
    creds = Credentials(api_key="abcdEFGHijklMNOP1234", api_secret="SUPERSECRETVALUE9876")
    for text in (repr(creds), str(creds), f"{creds}", describe_like(creds)):
        assert "SUPERSECRETVALUE9876" not in text
        assert "abcdEFGHijklMNOP1234" not in text
        assert "abcd***" in text


def describe_like(creds: Credentials) -> str:
    return f"kunci: {creds!r}"


def test_mask_secret_short_values_are_fully_hidden():
    assert mask_secret("short") == "***"
    assert mask_secret("12345678") == "***"
    assert mask_secret("123456789") == "1234***"


def test_read_env_process_environment_wins_over_dotenv(tmp_path: Path):
    (tmp_path / ".env").write_text("TRADING_MODE=live\nONLY_IN_FILE=x\n")
    env = read_env(tmp_path, environ={"TRADING_MODE": "paper"})
    assert env["TRADING_MODE"] == "paper"
    assert env["ONLY_IN_FILE"] == "x"


def test_read_env_without_dotenv_file(tmp_path: Path):
    assert read_env(tmp_path, environ={}) == {}


# --------------------------------------------------------------------------- #
# Pemuatan YAML: nilai masuk ke dataclass, typo gagal keras
# --------------------------------------------------------------------------- #


def test_load_settings_defaults_to_paper_with_isolated_environment(config_path: Path):
    settings = load_settings(config_path, environ={})
    assert settings.mode is TradingMode.PAPER
    assert settings.credentials is None
    assert settings.root == config_path.parent.parent
    assert settings.exchange.symbol == "BTC/USDT"
    assert settings.strategy.fast_period == 20
    assert settings.strategy.slow_period == 50
    assert settings.risk.flatten_on.daily_loss is True
    assert settings.risk.flatten_on.runaway_orders is False
    assert settings.risk.flatten_on.connection_failures is False
    assert settings.risk.flatten_on.stop_file is False
    assert settings.stop_file_path == config_path.parent.parent / "STOP"


def test_load_settings_reads_mode_and_keys_from_dotenv_in_root(
    project_dir: Path, config_path: Path
):
    (project_dir / ".env").write_text(
        "TRADING_MODE=testnet\n"
        "BINANCE_TESTNET_API_KEY=filekey1234567890\n"
        "BINANCE_TESTNET_API_SECRET=filesecret1234567890\n"
    )
    settings = load_settings(config_path, environ={})
    assert settings.mode is TradingMode.TESTNET
    assert settings.credentials.api_key == "filekey1234567890"


def test_load_settings_live_from_dotenv_without_flag_is_refused(
    project_dir: Path, config_path: Path
):
    (project_dir / ".env").write_text(
        "TRADING_MODE=live\nBINANCE_API_KEY=k1234567890\nBINANCE_API_SECRET=s1234567890\n"
    )
    with pytest.raises(ConfigError, match="menolak"):
        load_settings(config_path, environ={})
    settings = load_settings(config_path, environ={}, i_know_what_im_doing=True)
    assert settings.mode is TradingMode.LIVE


def test_stressed_costs_double_fee_and_slippage(config_path: Path):
    costs = load_settings(config_path, environ={}).costs
    stressed = costs.stressed()
    assert stressed.taker_fee_rate == pytest.approx(costs.taker_fee_rate * 2)
    assert stressed.slippage_rate == pytest.approx(costs.slippage_rate * 2)
    assert costs.taker_fee_rate == pytest.approx(0.001), "objek asli tidak boleh berubah"


def test_real_default_yaml_loads_in_paper_mode():
    """config/default.yaml yang dipakai bot sungguhan harus valid, bukan cuma fixture."""
    real = Path(__file__).resolve().parent.parent / "config" / "default.yaml"
    settings = load_settings(real, environ={})
    assert settings.mode is TradingMode.PAPER
    assert settings.costs.taker_fee_rate == pytest.approx(0.001)


def _write_config(project_dir: Path, mutate) -> Path:
    import yaml

    path = project_dir / "config" / "default.yaml"
    raw = yaml.safe_load(path.read_text())
    mutate(raw)
    path.write_text(yaml.safe_dump(raw))
    return path


def test_unknown_key_is_refused_with_its_path(project_dir: Path):
    path = _write_config(project_dir, lambda raw: raw["risk"].__setitem__("stop_los_fraction", 0.1))
    with pytest.raises(ConfigError, match="risk: key tidak dikenal: stop_los_fraction"):
        load_settings(path, environ={})


def test_missing_key_is_refused_with_its_path(project_dir: Path):
    path = _write_config(project_dir, lambda raw: raw["costs"].pop("slippage_rate"))
    with pytest.raises(ConfigError, match="costs: key wajib hilang: slippage_rate"):
        load_settings(path, environ={})


def test_wrong_type_is_refused(project_dir: Path):
    path = _write_config(project_dir, lambda raw: raw["strategy"].__setitem__("fast_period", "20"))
    with pytest.raises(ConfigError, match="strategy.fast_period: harus int"):
        load_settings(path, environ={})


def test_bool_in_numeric_field_is_refused(project_dir: Path):
    path = _write_config(
        project_dir, lambda raw: raw["risk"].__setitem__("max_orders_per_minute", True)
    )
    with pytest.raises(ConfigError, match="dapat bool"):
        load_settings(path, environ={})


def test_missing_section_is_refused(project_dir: Path):
    path = _write_config(project_dir, lambda raw: raw.pop("live"))
    with pytest.raises(ConfigError, match="section wajib hilang: live"):
        load_settings(path, environ={})


def test_missing_config_file_is_refused(tmp_path: Path):
    with pytest.raises(ConfigError, match="tidak ditemukan"):
        load_settings(tmp_path / "nope.yaml", environ={})


def test_invalid_yaml_is_refused(project_dir: Path):
    path = project_dir / "config" / "default.yaml"
    path.write_text("exchange: [unclosed\n")
    with pytest.raises(ConfigError, match="YAML tidak valid"):
        load_settings(path, environ={})


@pytest.mark.parametrize(
    ("section", "key", "value", "fragment"),
    [
        ("risk", "position_fraction", 0.0, "position_fraction"),
        ("risk", "position_fraction", 1.5, "position_fraction"),
        ("risk", "position_fraction", 0.5, "max_position_fraction"),
        ("risk", "exchange_stop_multiplier", 1.0, "lebih lebar"),
        ("risk", "max_orders_per_minute", 0, "max_orders_per_minute"),
        ("costs", "taker_fee_rate", 0.5, "taker_fee_rate"),
        ("costs", "stress_multiplier", 1.0, "stress_multiplier"),
        ("strategy", "fast_period", 60, "lebih kecil"),
        ("exchange", "max_time_drift_ms", 9000, "recv_window_ms"),
        ("exchange", "public_market_data_url", "http://plain.example", "https"),
        ("backtest", "initial_equity", 0, "initial_equity"),
        ("logging", "level", "LOUD", "logging.level"),
    ],
)
def test_validation_rejects_out_of_range_values(project_dir, section, key, value, fragment):
    path = _write_config(project_dir, lambda raw: raw[section].__setitem__(key, value))
    with pytest.raises(ConfigError, match=fragment):
        load_settings(path, environ={})


def test_describe_masks_credentials(project_dir: Path, config_path: Path):
    (project_dir / ".env").write_text(
        "TRADING_MODE=testnet\n"
        "BINANCE_TESTNET_API_KEY=visiblekeyprefix9999\n"
        "BINANCE_TESTNET_API_SECRET=neverprintthis0000\n"
    )
    text = describe(load_settings(config_path, environ={}))
    assert "neverprintthis0000" not in text
    assert "visiblekeyprefix9999" not in text
    assert "visi***" in text
    assert "mode: testnet" in text


def test_settings_are_immutable(config_path: Path):
    settings = load_settings(config_path, environ={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.risk.position_fraction = 0.9  # type: ignore[misc]
