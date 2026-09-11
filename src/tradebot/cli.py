"""Titik masuk command line.

Tahap 1 hanya punya check-config. Perintah fetch-data, backtest, dan run
ditambahkan di tahap berikutnya. Flag --i-know-what-im-doing hanya ada pada
perintah yang bisa menyentuh exchange dengan kunci; keberadaannya tidak pernah
cukup sendiri, TRADING_MODE=live juga harus ada.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from tradebot import __version__
from tradebot.config import DEFAULT_CONFIG_PATH, LIVE_FLAG, ConfigError, describe, load_settings
from tradebot.logging_setup import log_startup_banner, setup_logging

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2


def _add_live_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        LIVE_FLAG,
        dest="i_know_what_im_doing",
        action="store_true",
        help="Syarat kedua mode live. Hanya berlaku bersama TRADING_MODE=live di .env.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradebot",
        description="Bot trading crypto spot Binance. Default mode: paper.",
    )
    parser.add_argument("--version", action="version", version=f"tradebot {__version__}")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path file YAML konfigurasi (default: config/default.yaml).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check-config",
        help="Muat config dan .env, tampilkan mode serta parameter. Tidak menyentuh exchange.",
    )
    _add_live_flag(check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings(
            args.config, i_know_what_im_doing=getattr(args, "i_know_what_im_doing", False)
        )
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    logger = setup_logging(settings)
    log_startup_banner(logger, settings)

    if args.command == "check-config":
        print(describe(settings))
        return EXIT_OK

    parser.error(f"perintah tidak dikenal: {args.command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
