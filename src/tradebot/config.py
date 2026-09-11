"""Pemuat konfigurasi dan penegakan mode.

Sumber konfigurasi, dari prioritas terendah ke tertinggi:

1. config/default.yaml   semua angka strategi, risk, biaya, dan path
2. .env di root project  TRADING_MODE dan kunci API, dibaca lewat python-dotenv
3. environment proses    menimpa isi .env kalau variabel yang sama ada
4. flag command line     --i-know-what-im-doing

Aturan keras nomor 1 (default paper, live butuh dua syarat) dan nomor 2
(kunci hanya dari .env, tidak pernah masuk log) ditegakkan di modul ini.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

import yaml
from dotenv import dotenv_values

MODE_ENV_VAR = "TRADING_MODE"
LIVE_FLAG = "--i-know-what-im-doing"
DEFAULT_CONFIG_PATH = Path("config") / "default.yaml"


class ConfigError(ValueError):
    """Konfigurasi salah atau syarat mode tidak terpenuhi. Bot tidak boleh jalan."""


class TradingMode(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"

    @property
    def needs_credentials(self) -> bool:
        return self is not TradingMode.PAPER

    @property
    def is_sandbox(self) -> bool:
        return self is TradingMode.TESTNET


def resolve_mode(env_value: str | None, i_know_what_im_doing: bool) -> TradingMode:
    """Tentukan mode dari TRADING_MODE dan flag CLI.

    Tanpa TRADING_MODE, atau dengan nilai kosong, mode adalah paper.
    Mode live hanya kalau TRADING_MODE=live DAN flag diberikan. Salah satu saja
    ditolak, supaya tidak ada jalur di mana bot diam-diam jalan di mode yang
    tidak dimaksud pengguna.
    """
    raw = (env_value or "").strip().lower() or TradingMode.PAPER.value
    valid = ", ".join(m.value for m in TradingMode)
    try:
        mode = TradingMode(raw)
    except ValueError:
        raise ConfigError(f"{MODE_ENV_VAR}={raw!r} tidak dikenal. Pilihan: {valid}.") from None

    if mode is TradingMode.LIVE and not i_know_what_im_doing:
        raise ConfigError(
            f"{MODE_ENV_VAR}=live tapi flag {LIVE_FLAG} tidak diberikan. "
            "Mode live butuh keduanya. Bot menolak jalan."
        )
    if i_know_what_im_doing and mode is not TradingMode.LIVE:
        raise ConfigError(
            f"Flag {LIVE_FLAG} diberikan tapi {MODE_ENV_VAR}={mode.value}. "
            f"Mode live butuh keduanya. Hapus flag itu, atau set {MODE_ENV_VAR}=live."
        )
    return mode


CREDENTIAL_ENV_VARS: dict[TradingMode, tuple[str, str]] = {
    TradingMode.TESTNET: ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
    TradingMode.LIVE: ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
}


def mask_secret(value: str) -> str:
    """Tampilkan 4 karakter pertama saja: cukup untuk membedakan kunci, tidak untuk dipakai."""
    if len(value) <= 8:
        return "***"
    return value[:4] + "***"


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str = field(repr=False)

    def __repr__(self) -> str:
        return f"Credentials(api_key={mask_secret(self.api_key)!r}, api_secret='***')"

    __str__ = __repr__

    def secrets(self) -> tuple[str, ...]:
        """Semua string yang harus disamarkan di log."""
        return (self.api_key, self.api_secret)


def load_credentials(mode: TradingMode, env: Mapping[str, str]) -> Credentials | None:
    if not mode.needs_credentials:
        return None
    key_var, secret_var = CREDENTIAL_ENV_VARS[mode]
    key = (env.get(key_var) or "").strip()
    secret = (env.get(secret_var) or "").strip()
    missing = [name for name, value in ((key_var, key), (secret_var, secret)) if not value]
    if missing:
        raise ConfigError(
            f"Mode {mode.value} butuh {' dan '.join(missing)} di file .env. "
            "Nilainya tidak boleh ditulis di file lain mana pun."
        )
    return Credentials(api_key=key, api_secret=secret)


def read_env(root: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Gabungkan .env di root project dengan environment proses.

    Environment proses menang atas .env, sama dengan perilaku load_dotenv
    tanpa override. os.environ tidak pernah dimutasi, supaya test terisolasi.
    """
    env_file = root / ".env"
    from_file: dict[str, str] = {}
    if env_file.is_file():
        from_file = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    process = os.environ if environ is None else environ
    return {**from_file, **dict(process)}


# --------------------------------------------------------------------------- #
# Skema config/default.yaml
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float


@dataclass(frozen=True)
class ExchangeConfig:
    id: str
    symbol: str
    timeframe: str
    recv_window_ms: int
    max_time_drift_ms: int
    rate_limit: bool
    public_market_data_url: str
    retry: RetryConfig


@dataclass(frozen=True)
class DataConfig:
    cache_dir: str
    history_start: str
    max_gap_bars: int


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    fast_period: int
    slow_period: int


@dataclass(frozen=True)
class FlattenOn:
    daily_loss: bool
    runaway_orders: bool
    connection_failures: bool
    stop_file: bool


@dataclass(frozen=True)
class RiskConfig:
    position_fraction: float
    max_position_fraction: float
    daily_loss_limit_fraction: float
    stop_loss_fraction: float
    take_profit_fraction: float
    exchange_stop_multiplier: float
    max_orders_per_minute: int
    max_consecutive_failures: int
    stop_file: str
    flatten_on: FlattenOn


@dataclass(frozen=True)
class CostConfig:
    taker_fee_rate: float
    slippage_rate: float
    stress_multiplier: float

    def stressed(self) -> CostConfig:
        """Versi untuk backtest --stress: fee dan slippage dikalikan stress_multiplier."""
        return dataclasses.replace(
            self,
            taker_fee_rate=self.taker_fee_rate * self.stress_multiplier,
            slippage_rate=self.slippage_rate * self.stress_multiplier,
        )


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float
    bars_per_year: int


@dataclass(frozen=True)
class LiveConfig:
    loop_interval_seconds: int
    stale_bar_tolerance_seconds: int
    journal_path: str
    state_path: str
    trades_csv: str


@dataclass(frozen=True)
class LoggingConfig:
    dir: str
    level: str


@dataclass(frozen=True)
class Settings:
    root: Path
    config_path: Path
    mode: TradingMode
    credentials: Credentials | None
    exchange: ExchangeConfig
    data: DataConfig
    strategy: StrategyConfig
    risk: RiskConfig
    costs: CostConfig
    backtest: BacktestConfig
    live: LiveConfig
    logging: LoggingConfig

    @property
    def stop_file_path(self) -> Path:
        return self.root / self.risk.stop_file


T = TypeVar("T")

_SCALAR_TYPES: dict[type, tuple[type, ...]] = {
    bool: (bool,),
    int: (int,),
    float: (int, float),
    str: (str,),
}


def _build(cls: type[T], data: Any, path: str) -> T:
    """Bangun dataclass dari dict YAML. Key hilang, key asing, dan tipe salah semuanya error.

    Typo di YAML harus gagal keras, bukan diam-diam memakai nilai lain.
    """
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path}: harus berupa mapping, dapat {type(data).__name__}")
    hints = get_type_hints(cls)
    expected = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - expected)
    if unknown:
        raise ConfigError(f"{path}: key tidak dikenal: {', '.join(unknown)}")
    missing = sorted(expected - set(data))
    if missing:
        raise ConfigError(f"{path}: key wajib hilang: {', '.join(missing)}")

    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        hint = hints[f.name]
        value = data[f.name]
        child_path = f"{path}.{f.name}"
        if is_dataclass(hint):
            kwargs[f.name] = _build(hint, value, child_path)
            continue
        allowed = _SCALAR_TYPES[hint]
        # bool adalah subclass int di Python; jangan terima true di field angka.
        if isinstance(value, bool) and hint is not bool:
            raise ConfigError(f"{child_path}: harus {hint.__name__}, dapat bool")
        if not isinstance(value, allowed):
            raise ConfigError(
                f"{child_path}: harus {hint.__name__}, dapat {type(value).__name__} ({value!r})"
            )
        kwargs[f.name] = hint(value) if hint is float else value
    return cls(**kwargs)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def validate(settings: Settings) -> None:
    """Batas kewarasan. Setiap pelanggaran menyebut field-nya."""
    r, c, s, e, d, b, lv = (
        settings.risk,
        settings.costs,
        settings.strategy,
        settings.exchange,
        settings.data,
        settings.backtest,
        settings.live,
    )
    for name, value in (
        ("risk.position_fraction", r.position_fraction),
        ("risk.max_position_fraction", r.max_position_fraction),
        ("risk.daily_loss_limit_fraction", r.daily_loss_limit_fraction),
        ("risk.stop_loss_fraction", r.stop_loss_fraction),
        ("risk.take_profit_fraction", r.take_profit_fraction),
    ):
        _check(0 < value <= 1, f"{name} harus di antara 0 dan 1, dapat {value}")
    _check(
        r.position_fraction <= r.max_position_fraction,
        "risk.position_fraction tidak boleh lebih besar dari risk.max_position_fraction",
    )
    _check(
        r.exchange_stop_multiplier > 1,
        "risk.exchange_stop_multiplier harus > 1: stop di exchange wajib lebih lebar dari stop bot",
    )
    _check(r.max_orders_per_minute >= 1, "risk.max_orders_per_minute harus >= 1")
    _check(r.max_consecutive_failures >= 1, "risk.max_consecutive_failures harus >= 1")
    _check(bool(r.stop_file.strip()), "risk.stop_file tidak boleh kosong")

    for name, value in (
        ("costs.taker_fee_rate", c.taker_fee_rate),
        ("costs.slippage_rate", c.slippage_rate),
    ):
        _check(0 <= value < 0.1, f"{name} harus pecahan di antara 0 dan 0.1, dapat {value}")
    _check(c.stress_multiplier > 1, "costs.stress_multiplier harus > 1")

    _check(s.fast_period >= 1, "strategy.fast_period harus >= 1")
    _check(s.fast_period < s.slow_period, "strategy.fast_period harus lebih kecil dari slow_period")

    _check(e.retry.max_attempts >= 1, "exchange.retry.max_attempts harus >= 1")
    _check(e.retry.base_delay_seconds > 0, "exchange.retry.base_delay_seconds harus > 0")
    _check(
        e.retry.max_delay_seconds >= e.retry.base_delay_seconds,
        "exchange.retry.max_delay_seconds harus >= base_delay_seconds",
    )
    _check(
        e.public_market_data_url == "" or e.public_market_data_url.startswith("https://"),
        "exchange.public_market_data_url harus kosong atau diawali https://",
    )
    _check(e.recv_window_ms > 0, "exchange.recv_window_ms harus > 0")
    _check(e.max_time_drift_ms > 0, "exchange.max_time_drift_ms harus > 0")
    _check(
        e.max_time_drift_ms < e.recv_window_ms,
        "exchange.max_time_drift_ms harus lebih kecil dari recv_window_ms",
    )

    _check(d.max_gap_bars >= 0, "data.max_gap_bars harus >= 0")
    _check(b.initial_equity > 0, "backtest.initial_equity harus > 0")
    _check(b.bars_per_year >= 1, "backtest.bars_per_year harus >= 1")
    _check(lv.loop_interval_seconds >= 1, "live.loop_interval_seconds harus >= 1")
    _check(lv.stale_bar_tolerance_seconds >= 0, "live.stale_bar_tolerance_seconds harus >= 0")

    level = settings.logging.level.upper()
    _check(
        level in {"DEBUG", "INFO", "WARNING", "ERROR"},
        f"logging.level harus DEBUG, INFO, WARNING, atau ERROR, dapat {settings.logging.level!r}",
    )


def load_settings(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    i_know_what_im_doing: bool = False,
    environ: Mapping[str, str] | None = None,
    root: str | Path | None = None,
) -> Settings:
    """Muat dan validasi semua konfigurasi.

    root adalah folder project: tempat .env, file STOP, dan folder data/logs/state.
    Kalau tidak diberikan, diambil dari lokasi file config: <root>/config/default.yaml.
    """
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise ConfigError(f"File config tidak ditemukan: {config_path}")
    root_path = Path(root).resolve() if root is not None else config_path.parent.parent

    env = read_env(root_path, environ)
    mode = resolve_mode(env.get(MODE_ENV_VAR), i_know_what_im_doing)
    credentials = load_credentials(mode, env)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML tidak valid di {config_path}: {exc}") from None
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{config_path}: isi teratas harus mapping")

    sections = {
        "exchange": ExchangeConfig,
        "data": DataConfig,
        "strategy": StrategyConfig,
        "risk": RiskConfig,
        "costs": CostConfig,
        "backtest": BacktestConfig,
        "live": LiveConfig,
        "logging": LoggingConfig,
    }
    unknown = sorted(set(raw) - set(sections))
    if unknown:
        raise ConfigError(f"config: section tidak dikenal: {', '.join(unknown)}")
    missing = sorted(set(sections) - set(raw))
    if missing:
        raise ConfigError(f"config: section wajib hilang: {', '.join(missing)}")

    built = {name: _build(cls, raw[name], name) for name, cls in sections.items()}
    settings = Settings(
        root=root_path,
        config_path=config_path,
        mode=mode,
        credentials=credentials,
        **built,
    )
    validate(settings)
    return settings


def describe(settings: Settings) -> str:
    """Ringkasan yang aman dicetak: kunci API selalu tersamar."""
    lines = [
        f"mode: {settings.mode.value}",
        f"root: {settings.root}",
        f"config: {settings.config_path}",
        f"credentials: {settings.credentials!r}",
    ]
    for section in ("exchange", "data", "strategy", "risk", "costs", "backtest", "live", "logging"):
        lines.append(f"{section}:")
        for key, value in dataclasses.asdict(getattr(settings, section)).items():
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)
