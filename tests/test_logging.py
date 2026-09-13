"""Tahap 1: logging ke console dan file, waktu UTC, rahasia tersamar."""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from tradebot.config import load_settings
from tradebot.logging_setup import APP_LOGGER, log_file_path, log_startup_banner, setup_logging

UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ")


def test_logs_go_to_console_and_rotating_file(project_dir: Path, config_path: Path):
    settings = load_settings(config_path, environ={})
    stream = io.StringIO()
    logger = setup_logging(settings, stream=stream)
    logging.getLogger(f"{APP_LOGGER}.child").info("halo dari child")

    console = stream.getvalue()
    assert "halo dari child" in console
    assert UTC_STAMP.match(console), console
    assert "tradebot.child" in console

    on_disk = log_file_path(settings).read_text()
    assert "halo dari child" in on_disk
    assert log_file_path(settings).parent == project_dir / "logs"
    assert logger.propagate is False


def test_setup_logging_is_idempotent(config_path: Path):
    settings = load_settings(config_path, environ={})
    stream = io.StringIO()
    setup_logging(settings, stream=stream, to_file=False)
    setup_logging(settings, stream=stream, to_file=False)
    logging.getLogger(APP_LOGGER).info("sekali saja")
    assert stream.getvalue().count("sekali saja") == 1


def test_secrets_are_masked_in_console_and_file(project_dir: Path, config_path: Path):
    key = "AKIAEXAMPLEKEY1234567890"
    secret = "VERYSECRETVALUE0987654321"
    (project_dir / ".env").write_text(
        f"TRADING_MODE=testnet\nBINANCE_TESTNET_API_KEY={key}\nBINANCE_TESTNET_API_SECRET={secret}\n"
    )
    settings = load_settings(config_path, environ={})
    stream = io.StringIO()
    logger = setup_logging(settings, stream=stream)

    # Tiga jalur kebocoran: string langsung, argumen %s, dan objek di dalam exception.
    logger.info("bug: mencetak kunci %s dan rahasia %s", key, secret)
    logger.info(f"bug lain: {secret}")
    try:
        raise RuntimeError(f"exchange menolak kunci {key} / {secret}")
    except RuntimeError:
        logger.exception("gagal")
    log_startup_banner(logger, settings)

    for text in (stream.getvalue(), log_file_path(settings).read_text()):
        assert key not in text
        assert secret not in text
        assert "***" in text
        assert "AKIA" not in text and "***(24 karakter)" in text, (
            "banner tidak menampilkan satu karakter pun dari kunci, hanya panjangnya"
        )


def test_banner_states_mode_symbol_and_costs(config_path: Path):
    settings = load_settings(config_path, environ={})
    stream = io.StringIO()
    logger = setup_logging(settings, stream=stream, to_file=False)
    log_startup_banner(logger, settings)
    text = stream.getvalue()
    assert "mode=paper" in text
    assert "symbol=BTC/USDT" in text
    assert "taker_fee_rate=0.0015" in text
    assert "venue=tokocrypto" in text
    assert "MODE LIVE" not in text
