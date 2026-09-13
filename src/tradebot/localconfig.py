"""config/local.yaml: overlay milik pemilik yang tidak di-commit.

Dipakai jalur trial live supaya live.enabled, tanggal verifikasi kunci, dan pecahan
posisi diubah lewat perintah `tradebot local-set`, bukan diedit tangan di file yang
di-commit. File ini TIDAK pernah berisi kunci API; kunci hanya di .env.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from tradebot.config import LOCAL_CONFIG_NAME, ConfigError

__all__ = ["LOCAL_CONFIG_NAME", "parse_assignment", "set_local_values"]


def parse_assignment(text: str) -> tuple[str, Any]:
    """Urai `section.key=nilai`. Nilai ditafsirkan seperti YAML: true, 0.25, 30, teks.

    Hanya dua tingkat (section.key) yang diterima: cukup untuk trial live dan
    mencegah overlay diam-diam mengganti venue atau struktur bersarang lain.
    """
    if "=" not in text:
        raise ValueError(f"{text!r}: bentuknya harus section.key=nilai")
    key, raw_value = text.split("=", 1)
    key = key.strip()
    parts = key.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"{key!r}: key harus persis dua tingkat, section.key")
    return key, _scalar(raw_value.strip())


def _scalar(text: str) -> Any:
    """Skalar sederhana: true/false, bilangan bulat, pecahan; sisanya teks apa adanya.

    Sengaja bukan yaml.safe_load: tanggal seperti 2026-09-13 harus tetap teks, karena
    live.api_key_verified_date bertipe str.
    """
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _read_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: YAML tidak valid: {exc}") from None
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ConfigError(f"{path.name}: isi teratas harus mapping; perbaiki atau hapus file itu")
    return dict(data)


def set_local_values(root: Path, values: Mapping[str, Any]) -> Path:
    """Tulis nilai `section.key` ke config/local.yaml secara atomik, key lain dipertahankan.

    Tidak memvalidasi terhadap skema; pemanggil (CLI) memuat ulang settings setelahnya
    dan mengembalikan file kalau gagal.
    """
    path = root / "config" / LOCAL_CONFIG_NAME
    data = _read_existing(path)
    for dotted, value in values.items():
        section, key = dotted.split(".", 1)
        current = data.get(section)
        if current is None:
            current = {}
        elif not isinstance(current, Mapping):
            raise ConfigError(f"{path.name}: {section} harus mapping, dapat {current!r}")
        section_data = dict(current)
        section_data[key] = value
        data[section] = section_data
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "# Overlay lokal, tidak di-commit. Ditulis oleh `tradebot local-set`.\n"
        "# Hanya menimpa key yang ada di config/default.yaml. Tidak pernah berisi kunci API.\n"
        + yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    )
    fd, tmp_name = tempfile.mkstemp(prefix=".local-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
