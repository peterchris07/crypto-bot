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
    assert load_credentials(TradingMode.PAPER, {}, "tokocrypto") is None


def test_testnet_missing_keys_names_the_variables():
    with pytest.raises(ConfigError) as exc:
        load_credentials(TradingMode.TESTNET, {}, "binance")
    message = str(exc.value)
    assert "BINANCE_TESTNET_API_KEY" in message
    assert "BINANCE_TESTNET_API_SECRET" in message
    assert ".env" in message


def test_live_missing_secret_only_names_the_secret():
    env = {"TOKOCRYPTO_API_KEY": "k" * 20}
    with pytest.raises(ConfigError) as exc:
        load_credentials(TradingMode.LIVE, env, "tokocrypto")
    message = str(exc.value)
    assert "TOKOCRYPTO_API_SECRET" in message
    assert "TOKOCRYPTO_API_KEY " not in message


def test_testnet_and_live_use_different_variables():
    env = {
        "BINANCE_TESTNET_API_KEY": "testnet-key-1234567890",
        "BINANCE_TESTNET_API_SECRET": "testnet-secret-1234567890",
        "TOKOCRYPTO_API_KEY": "tokocrypto-key-1234567890",
        "TOKOCRYPTO_API_SECRET": "tokocrypto-secret-1234567890",
    }
    testnet = load_credentials(TradingMode.TESTNET, env, "binance")
    live = load_credentials(TradingMode.LIVE, env, "tokocrypto")
    assert testnet.api_key.startswith("testnet")
    assert live.api_key.startswith("tokocrypto")


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
    assert settings.venue.id == "tokocrypto"
    assert settings.exchange.testnet.id == "binance"
    assert settings.exchange.base == "BTC"
    assert settings.exchange.quote == "USDT"


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
        "TRADING_MODE=live\nTOKOCRYPTO_API_KEY=k1234567890\nTOKOCRYPTO_API_SECRET=s1234567890\n"
    )
    with pytest.raises(ConfigError, match="menolak"):
        load_settings(config_path, environ={})
    settings = load_settings(config_path, environ={}, i_know_what_im_doing=True)
    assert settings.mode is TradingMode.LIVE


def test_cost_components_sum_per_side(config_path: Path):
    costs = load_settings(config_path, environ={}).costs
    assert costs.total_fee_rate == pytest.approx(0.0015 + 0.0021 + 0.000444)
    assert costs.cost_per_side_rate == pytest.approx(0.004044 + 0.0015)
    assert costs.round_trip_rate == pytest.approx(2 * (0.004044 + 0.0015))


def test_stressed_costs_double_fee_exchange_fee_and_slippage_but_not_tax(config_path: Path):
    costs = load_settings(config_path, environ={}).costs
    stressed = costs.stressed()
    assert stressed.taker_fee_rate == pytest.approx(costs.taker_fee_rate * 2)
    assert stressed.exchange_fee_rate == pytest.approx(costs.exchange_fee_rate * 2)
    assert stressed.slippage_rate == pytest.approx(costs.slippage_rate * 2)
    assert stressed.tax_rate == pytest.approx(costs.tax_rate), "pajak adalah angka pasti"
    assert costs.taker_fee_rate == pytest.approx(0.0015), "objek asli tidak boleh berubah"


def test_real_default_yaml_loads_in_paper_mode():
    """config/default.yaml yang dipakai bot sungguhan harus valid, bukan cuma fixture."""
    real = Path(__file__).resolve().parent.parent / "config" / "default.yaml"
    settings = load_settings(real, environ={})
    assert settings.mode is TradingMode.PAPER
    assert settings.venue.id == "tokocrypto"
    assert settings.exchange.live.market_data_url.startswith("https://www.tokocrypto.site")
    assert settings.costs.total_fee_rate == pytest.approx(0.004044)
    assert settings.costs.slippage_rate == pytest.approx(0.0015)


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
        ("exchange", "symbol", "BTCUSDT", "BASE/QUOTE"),
        ("costs", "tax_rate", 0.5, "tax_rate"),
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


def test_venue_market_data_url_must_be_https(project_dir: Path):
    path = _write_config(
        project_dir,
        lambda raw: raw["exchange"]["live"].__setitem__("market_data_url", "http://plain.example"),
    )
    with pytest.raises(ConfigError, match="exchange.live.market_data_url"):
        load_settings(path, environ={})


def test_venue_id_must_not_be_empty(project_dir: Path):
    path = _write_config(project_dir, lambda raw: raw["exchange"]["testnet"].__setitem__("id", " "))
    with pytest.raises(ConfigError, match="exchange.testnet.id"):
        load_settings(path, environ={})


def test_credentials_follow_venue_not_mode():
    """Hipotesis review #1: kunci Tokocrypto tidak boleh terkirim ke Binance mainnet."""
    env = {
        "TOKOCRYPTO_API_KEY": "toko-key-1234567890",
        "TOKOCRYPTO_API_SECRET": "toko-secret-1234567890",
        "BINANCE_API_KEY": "bnb-key-1234567890",
        "BINANCE_API_SECRET": "bnb-secret-1234567890",
    }
    assert load_credentials(TradingMode.LIVE, env, "binance").api_key.startswith("bnb")
    assert load_credentials(TradingMode.LIVE, env, "tokocrypto").api_key.startswith("toko")
    with pytest.raises(ConfigError, match="BINANCE_API_KEY"):
        load_credentials(TradingMode.LIVE, {k: v for k, v in env.items() if "TOKO" in k}, "binance")
    with pytest.raises(ConfigError, match="tidak punya pemetaan"):
        load_credentials(TradingMode.LIVE, env, "indodax")


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (
            lambda raw: raw["exchange"]["live"].__setitem__(
                "market_data_url", "https://x.example/api/v3/"
            ),
            "berakhir",
        ),
        (lambda raw: raw["exchange"].__setitem__("timeframe", "1H"), "exchange.timeframe"),
        (lambda raw: raw["exchange"].__setitem__("timeframe", ""), "exchange.timeframe"),
        (lambda raw: raw["backtest"].__setitem__("bars_per_year", 365), "bars_per_year harus 8760"),
        (lambda raw: raw["costs"].__setitem__("stress_multiplier", 20.0), "setelah stress"),
        (lambda raw: raw["exchange"].__setitem__("recv_window_ms", 60000), "recv_window_ms"),
        (
            lambda raw: (
                raw["exchange"].__setitem__("recv_window_ms", 5000)
                or raw["exchange"].__setitem__("max_time_drift_ms", 4999)
                or raw["exchange"].__setitem__("timeframe", "4h")
            ),
            "bars_per_year harus 2190",
        ),
    ],
)
def test_review_validations(project_dir: Path, mutate, fragment):
    path = _write_config(project_dir, mutate)
    with pytest.raises(ConfigError, match=fragment):
        load_settings(path, environ={})


@pytest.mark.parametrize(
    ("value", "fragment"),
    [("kemarin", "data.history_start"), ("", "data.history_start"), ("2025-13-01", "ISO 8601")],
)
def test_history_start_must_be_iso_date(project_dir: Path, value, fragment):
    path = _write_config(project_dir, lambda raw: raw["data"].__setitem__("history_start", value))
    with pytest.raises(ConfigError, match=fragment):
        load_settings(path, environ={})


def test_cache_dir_must_not_be_empty(project_dir: Path):
    path = _write_config(project_dir, lambda raw: raw["data"].__setitem__("cache_dir", "  "))
    with pytest.raises(ConfigError, match="data.cache_dir"):
        load_settings(path, environ={})


def test_lookback_multiplier_must_be_positive(project_dir: Path):
    path = _write_config(
        project_dir, lambda raw: raw["strategy"].__setitem__("lookback_multiplier", 0)
    )
    with pytest.raises(ConfigError, match="strategy.lookback_multiplier"):
        load_settings(path, environ={})


# --------------------------------------------------------------------------- #
# Trial live: overlay config/local.yaml dan folder state per mode
# --------------------------------------------------------------------------- #

LIVE_ENV = {
    "TRADING_MODE": "live",
    "TOKOCRYPTO_API_KEY": "k1234567890",
    "TOKOCRYPTO_API_SECRET": "s1234567890",
}


def test_local_overlay_is_optional_and_merged_over_default(project_dir: Path, config_path: Path):
    base = load_settings(config_path, environ={})
    assert base.local_config_path is None
    assert base.live.enabled is False
    (project_dir / "config" / "local.yaml").write_text(
        "live:\n  enabled: true\n  api_key_verified_date: '2026-09-13'\nrisk:\n"
        "  position_fraction: 0.25\n",
        encoding="utf-8",
    )
    settings = load_settings(config_path, environ={})
    assert settings.local_config_path == project_dir / "config" / "local.yaml"
    assert settings.live.enabled is True
    assert settings.live.api_key_verified_date == "2026-09-13"
    assert settings.risk.position_fraction == 0.25
    # yang tidak disebut overlay tetap dari default.yaml
    assert settings.risk.max_position_fraction == 0.25
    assert settings.strategy.fast_period == 20
    assert "config lokal: " in describe(settings)


def test_local_overlay_unknown_key_is_refused_with_its_path(project_dir: Path, config_path: Path):
    (project_dir / "config" / "local.yaml").write_text("live:\n  enabld: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"local\.yaml.*live\.enabld"):
        load_settings(config_path, environ={})


def test_local_overlay_must_be_mapping_of_sections(project_dir: Path, config_path: Path):
    (project_dir / "config" / "local.yaml").write_text("- live\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local.yaml"):
        load_settings(config_path, environ={})
    (project_dir / "config" / "local.yaml").write_text("live: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local.yaml"):
        load_settings(config_path, environ={})


def test_local_overlay_cannot_add_sections(project_dir: Path, config_path: Path):
    (project_dir / "config" / "local.yaml").write_text("secrets:\n  a: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local.yaml.*secrets"):
        load_settings(config_path, environ={})


def test_mode_dir_placeholder_keeps_paper_in_place_and_separates_keyed_modes(
    project_dir: Path, config_path: Path
):
    text = (project_dir / "config" / "default.yaml").read_text(encoding="utf-8")
    text = (
        text.replace("state/orders.jsonl", "state/{mode_dir}orders.jsonl")
        .replace("state/risk_state.json", "state/{mode_dir}risk_state.json")
        .replace("state/position.json", "state/{mode_dir}position.json")
        .replace("state/paper_account.json", "state/{mode_dir}paper_account.json")
        .replace("trades/trades.csv", "trades/{mode_dir}trades.csv")
        .replace("state/live_stage.json", "state/{mode_dir}live_stage.json")
        .replace("dir: logs", "dir: logs/{mode_dir}")
    )
    (project_dir / "config" / "default.yaml").write_text(text, encoding="utf-8")

    paper = load_settings(config_path, environ={})
    assert paper.live.position_path == "state/position.json"
    assert paper.live.journal_path == "state/orders.jsonl"
    assert paper.live.trades_csv == "trades/trades.csv"
    assert paper.logging.dir == "logs/"

    live = load_settings(config_path, environ=LIVE_ENV, i_know_what_im_doing=True)
    assert live.live.position_path == "state/live/position.json"
    assert live.live.journal_path == "state/live/orders.jsonl"
    assert live.live.paper_account_path == "state/live/paper_account.json"
    assert live.live.trades_csv == "trades/live/trades.csv"
    assert live.live.stage_path == "state/live/live_stage.json"
    assert live.live.state_path == "state/live/risk_state.json"
    assert live.logging.dir == "logs/live/"

    testnet_env = {
        "TRADING_MODE": "testnet",
        "BINANCE_TESTNET_API_KEY": "k1234567890",
        "BINANCE_TESTNET_API_SECRET": "s1234567890",
    }
    testnet = load_settings(config_path, environ=testnet_env)
    assert testnet.live.position_path == "state/testnet/position.json"
    assert testnet.mode_dir == "testnet/"
    assert paper.mode_dir == ""


def test_unknown_path_placeholder_is_refused(project_dir: Path, config_path: Path):
    text = (project_dir / "config" / "default.yaml").read_text(encoding="utf-8")
    text = text.replace("state/position.json", "state/{venue}/position.json")
    (project_dir / "config" / "default.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"live\.position_path.*\{venue\}"):
        load_settings(config_path, environ={})


def test_real_default_yaml_separates_live_state_from_paper():
    root = Path(__file__).resolve().parent.parent
    paper = load_settings(root / "config" / "default.yaml", environ={}, root=root)
    live = load_settings(
        root / "config" / "default.yaml", environ=LIVE_ENV, i_know_what_im_doing=True, root=root
    )
    for section, key in (
        ("live", "journal_path"),
        ("live", "position_path"),
        ("live", "trades_csv"),
        ("live", "stage_path"),
        ("live", "state_path"),
        ("live", "paper_account_path"),
        ("logging", "dir"),
    ):
        paper_value = getattr(getattr(paper, section), key)
        live_value = getattr(getattr(live, section), key)
        assert paper_value != live_value, f"{section}.{key} sama di paper dan live"
        assert "/live/" in live_value or live_value.endswith("/live"), (section, key, live_value)
    # STOP tetap satu untuk semua mode: menghentikan apa pun yang jalan
    assert paper.stop_file_path == live.stop_file_path
