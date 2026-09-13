"""config/local.yaml: overlay milik pemilik yang tidak di-commit.

Dipakai jalur trial live supaya live.enabled, tanggal verifikasi kunci, dan pecahan
posisi diubah lewat perintah `tradebot local-set` / `local-unset`, bukan diedit tangan
di file yang di-commit. File ini TIDAK pernah berisi kunci API; kunci hanya di .env.
File ini selalu berada di folder yang sama dengan file config utama (config_dir).
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from tradebot.config import LOCAL_CONFIG_NAME, ConfigError

__all__ = ["LOCAL_CONFIG_NAME", "parse_assignment", "set_local_values", "unset_local_values"]

_INT = re.compile(r"^[+-]?[0-9]+$")
_FLOAT = re.compile(r"^[+-]?([0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)([eE][+-]?[0-9]{1,3})?$")


def parse_key(key: str) -> tuple[str, str]:
    """`section.key`, persis dua tingkat; cukup untuk trial live dan mencegah overlay
    diam-diam mengganti venue atau struktur bersarang lain."""
    key = key.strip()
    parts = key.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"{key!r}: key harus persis dua tingkat, section.key")
    return parts[0], parts[1]


def parse_assignment(text: str) -> tuple[str, Any]:
    """Urai `section.key=nilai`. Nilai: true/false, bilangan bulat, pecahan; sisanya teks."""
    if "=" not in text:
        raise ValueError(f"{text!r}: bentuknya harus section.key=nilai")
    key, raw_value = text.split("=", 1)
    section, name = parse_key(key)
    return f"{section}.{name}", _scalar(raw_value.strip())


def _scalar(text: str) -> Any:
    """Skalar sederhana dengan bentuk ASCII yang ketat.

    Sengaja bukan yaml.safe_load: tanggal seperti 2026-09-13 harus tetap teks (field-nya
    str), dan inf, nan, 1_000, 0x10, atau angka non-ASCII tidak boleh menjadi angka;
    mereka tetap teks lalu ditolak validasi tipe saat dimuat ulang.
    """
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        value = float(text)
        if value != value or value in (float("inf"), float("-inf")):
            return text
        return value
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


def _write_atomic(path: Path, data: Mapping[str, Any]) -> None:
    text = (
        "# Overlay lokal, tidak di-commit. Ditulis oleh `tradebot local-set`.\n"
        "# Hanya menimpa key yang ada di config/default.yaml. Tidak pernah berisi kunci API.\n"
        + yaml.safe_dump(dict(data), sort_keys=True, allow_unicode=True)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
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


def set_local_values(config_dir: Path, values: Mapping[str, Any]) -> Path:
    """Tulis nilai `section.key` ke <config_dir>/local.yaml secara atomik; key lain tetap.

    Tidak memvalidasi terhadap skema; pemanggil (CLI) memuat ulang settings setelahnya
    dan mengembalikan file kalau gagal.
    """
    path = config_dir / LOCAL_CONFIG_NAME
    data = _read_existing(path)
    for dotted, value in values.items():
        section, key = parse_key(dotted)
        current = data.get(section)
        if current is None:
            current = {}
        elif not isinstance(current, Mapping):
            raise ConfigError(f"{path.name}: {section} harus mapping, dapat {current!r}")
        section_data = dict(current)
        section_data[key] = value
        data[section] = section_data
    _write_atomic(path, data)
    return path


def unset_local_values(config_dir: Path, keys: Iterable[str]) -> Path:
    """Hapus `section.key` dari overlay; key yang tidak ada adalah error, bukan diam."""
    path = config_dir / LOCAL_CONFIG_NAME
    data = _read_existing(path)
    for dotted in keys:
        section, key = parse_key(dotted)
        current = data.get(section)
        if not isinstance(current, Mapping) or key not in current:
            raise ConfigError(f"{path.name}: {dotted} tidak ada di overlay")
        section_data = dict(current)
        del section_data[key]
        if section_data:
            data[section] = section_data
        else:
            del data[section]
    _write_atomic(path, data)
    return path
