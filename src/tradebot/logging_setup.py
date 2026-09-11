"""Logging: ke console dan ke file berputar di logs/, waktu selalu UTC.

Setiap baris log melewati MaskingFormatter yang mengganti kunci API dengan ***.
Ini lapisan terakhir aturan keras nomor 2: sekalipun ada bug yang mencetak
objek berisi kunci, yang sampai ke file dan layar sudah tersamar.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

from tradebot.config import Settings

APP_LOGGER = "tradebot"
LOG_FILE = "tradebot.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
MASK = "***"


class MaskingFormatter(logging.Formatter):
    """Formatter biasa, tapi hasil akhirnya dibersihkan dari string rahasia."""

    def __init__(self, fmt: str, secrets: Iterable[str] = ()) -> None:
        super().__init__(fmt)
        # Yang terpanjang dulu, supaya rahasia yang mengandung rahasia lain tetap tersamar penuh.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        stamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
        return f"{stamp}.{int(record.msecs):03d}Z"

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return text


def setup_logging(
    settings: Settings,
    *,
    stream: IO[str] | None = None,
    to_file: bool = True,
) -> logging.Logger:
    """Pasang handler pada logger 'tradebot'. Idempoten: aman dipanggil ulang."""
    secrets = settings.credentials.secrets() if settings.credentials else ()
    formatter = MaskingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", secrets)

    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(settings.logging.level.upper())
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if to_file:
        log_dir = settings.root / settings.logging.dir
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def log_startup_banner(logger: logging.Logger, settings: Settings) -> None:
    """Baris pertama yang harus terbaca tanpa buka kode: mode apa, pair apa, biaya berapa."""
    logger.info(
        "start mode=%s exchange=%s symbol=%s timeframe=%s config=%s",
        settings.mode.value,
        settings.exchange.id,
        settings.exchange.symbol,
        settings.exchange.timeframe,
        settings.config_path,
    )
    logger.info(
        "biaya: taker_fee_rate=%s slippage_rate=%s (stress x%s)",
        settings.costs.taker_fee_rate,
        settings.costs.slippage_rate,
        settings.costs.stress_multiplier,
    )
    logger.info(
        "risk: position=%s max_position=%s daily_loss_limit=%s stop_loss=%s take_profit=%s "
        "max_orders_per_minute=%s max_consecutive_failures=%s stop_file=%s",
        settings.risk.position_fraction,
        settings.risk.max_position_fraction,
        settings.risk.daily_loss_limit_fraction,
        settings.risk.stop_loss_fraction,
        settings.risk.take_profit_fraction,
        settings.risk.max_orders_per_minute,
        settings.risk.max_consecutive_failures,
        settings.stop_file_path,
    )
    if settings.mode.needs_credentials:
        logger.info("kunci API: %r", settings.credentials)
    if settings.mode is settings.mode.LIVE:
        logger.warning("MODE LIVE: order akan dikirim ke Binance mainnet dengan uang asli.")


def log_file_path(settings: Settings) -> Path:
    return settings.root / settings.logging.dir / LOG_FILE
