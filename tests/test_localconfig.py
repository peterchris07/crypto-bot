"""Trial live: config/local.yaml ditulis lewat perintah, bukan diedit tangan."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tradebot import cli
from tradebot.config import ConfigError, load_settings
from tradebot.localconfig import LOCAL_CONFIG_NAME, parse_assignment, set_local_values


@pytest.mark.parametrize(
    ("text", "key", "value"),
    [
        ("live.enabled=true", "live.enabled", True),
        ("live.enabled=false", "live.enabled", False),
        ("risk.position_fraction=0.25", "risk.position_fraction", 0.25),
        ("live.max_key_age_days=30", "live.max_key_age_days", 30),
        ("live.api_key_verified_date=2026-09-13", "live.api_key_verified_date", "2026-09-13"),
        ("live.api_key_verified_date=", "live.api_key_verified_date", ""),
    ],
)
def test_parse_assignment_types_scalars_like_yaml(text, key, value):
    assert parse_assignment(text) == (key, value)


@pytest.mark.parametrize("text", ["live.enabled", "=true", "enabled=true", "a.b.c=1"])
def test_parse_assignment_rejects_malformed(text):
    with pytest.raises(ValueError):
        parse_assignment(text)


def test_set_local_values_deep_merges_and_keeps_other_keys(project_dir: Path):
    config_dir = project_dir / "config"
    path = set_local_values(config_dir, {"live.enabled": False, "risk.position_fraction": 0.2})
    assert path == config_dir / LOCAL_CONFIG_NAME
    set_local_values(config_dir, {"live.api_key_verified_date": "2026-09-13"})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data == {
        "live": {"enabled": False, "api_key_verified_date": "2026-09-13"},
        "risk": {"position_fraction": 0.2},
    }
    # tulisannya atomik: tidak ada file sementara tertinggal
    assert sorted(p.name for p in (project_dir / "config").iterdir()) == [
        "default.yaml",
        LOCAL_CONFIG_NAME,
    ]


def test_set_local_values_refuses_to_clobber_a_broken_file(project_dir: Path):
    path = project_dir / "config" / LOCAL_CONFIG_NAME
    path.write_text("- bukan mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="local.yaml"):
        set_local_values(project_dir / "config", {"live.enabled": True})
    assert path.read_text(encoding="utf-8") == "- bukan mapping\n"


def test_cli_local_set_validates_by_reloading_and_reverts_on_error(
    project_dir: Path, config_path: Path, capsys
):
    code = cli.main(
        ["--config", str(config_path), "local-set", "live.api_key_verified_date=2026-09-13"]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK, out
    assert "live.api_key_verified_date: 2026-09-13" in out
    assert load_settings(config_path, environ={}).live.api_key_verified_date == "2026-09-13"

    # nilai yang tidak lolos validasi tidak boleh tertinggal di file
    code = cli.main(["--config", str(config_path), "local-set", "risk.position_fraction=0.9"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_CONFIG_ERROR
    assert "max_position_fraction" in err
    data = yaml.safe_load((project_dir / "config" / LOCAL_CONFIG_NAME).read_text())
    assert data == {"live": {"api_key_verified_date": "2026-09-13"}}

    # kunci yang tidak dikenal juga ditolak dan tidak tertinggal
    code = cli.main(["--config", str(config_path), "local-set", "live.enabld=true"])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "live.enabld" in capsys.readouterr().err
    data = yaml.safe_load((project_dir / "config" / LOCAL_CONFIG_NAME).read_text())
    assert data == {"live": {"api_key_verified_date": "2026-09-13"}}


def test_cli_local_set_in_live_mode_needs_the_flag_like_any_other_command(
    project_dir: Path, config_path: Path, capsys, monkeypatch
):
    # lewat environment proses, bukan .env, supaya tidak bergantung pada shell pengembang
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("TOKOCRYPTO_API_KEY", "k1234567890")
    monkeypatch.setenv("TOKOCRYPTO_API_SECRET", "s1234567890")
    code = cli.main(["--config", str(config_path), "local-set", "live.enabled=true"])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "menolak" in capsys.readouterr().err
    assert not (project_dir / "config" / LOCAL_CONFIG_NAME).exists()
    code = cli.main(
        ["--config", str(config_path), "local-set", "live.enabled=true", "--i-know-what-im-doing"]
    )
    assert code == cli.EXIT_OK, capsys.readouterr()
    settings = load_settings(config_path, i_know_what_im_doing=True)
    assert settings.live.enabled is True
    written = (project_dir / "config" / LOCAL_CONFIG_NAME).read_text()
    assert "k1234567890" not in written and "s1234567890" not in written

    # kunci API tidak pernah diterima oleh local-set, apa pun namanya
    code = cli.main(
        [
            "--config",
            str(config_path),
            "local-set",
            "exchange.api_secret=abc",
            "--i-know-what-im-doing",
        ]
    )
    err = capsys.readouterr().err
    assert code == cli.EXIT_CONFIG_ERROR
    assert "hanya boleh di .env" in err and "abc" not in err
    assert "abc" not in (project_dir / "config" / LOCAL_CONFIG_NAME).read_text()


def test_cli_local_set_never_echoes_a_rejected_value(project_dir: Path, config_path: Path, capsys):
    """Nilai yang ditolak bisa saja kunci yang salah tempel: tidak boleh muncul di layar."""
    code = cli.main(
        ["--config", str(config_path), "local-set", "live.api_key_verified_date=RAHASIA1234567890"]
    )
    captured = capsys.readouterr()
    assert code == cli.EXIT_CONFIG_ERROR
    assert "RAHASIA1234567890" not in captured.err and "RAHASIA1234567890" not in captured.out
    assert "live.api_key_verified_date" in captured.err
    assert not (project_dir / "config" / LOCAL_CONFIG_NAME).exists()


def test_cli_local_set_follows_the_config_file_location(tmp_path: Path, capsys):
    """--config di luar folder config/: overlay ditulis di sebelah file itu dan tetap divalidasi."""
    from tests.conftest import MINIMAL_CONFIG

    alt = tmp_path / "alt"
    alt.mkdir()
    config_path = alt / "default.yaml"
    config_path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    code = cli.main(["--config", str(config_path), "local-set", "risk.position_fraction=0.9"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_CONFIG_ERROR, err
    assert "max_position_fraction" in err
    assert not (alt / LOCAL_CONFIG_NAME).exists()
    assert not (tmp_path / "config").exists()
    code = cli.main(["--config", str(config_path), "local-set", "risk.position_fraction=0.2"])
    assert code == cli.EXIT_OK, capsys.readouterr()
    assert (alt / LOCAL_CONFIG_NAME).exists()
    assert load_settings(config_path, environ={}).risk.position_fraction == 0.2


@pytest.mark.parametrize("text", ["inf", "-inf", "nan", "NaN", "1_000", "0x10", "1e400", "٣"])
def test_parse_assignment_keeps_odd_numerics_as_text_or_rejects(text):
    key, value = parse_assignment(f"risk.position_fraction={text}")
    # bukan angka yang diterima: tetap teks, lalu ditolak validasi tipe saat dimuat ulang
    assert isinstance(value, str), (text, value)


def test_cli_local_set_works_even_when_local_yaml_is_broken_and_can_unset(
    project_dir: Path, config_path: Path, capsys
):
    path = project_dir / "config" / LOCAL_CONFIG_NAME
    path.write_text("live:\n  kunci_lama: 1\n  enabled: true\n", encoding="utf-8")
    # perintah lain gagal karena overlay rusak ...
    assert cli.main(["--config", str(config_path), "check-config"]) == cli.EXIT_CONFIG_ERROR
    assert "kunci_lama" in capsys.readouterr().err
    # ... tapi local-unset bisa memperbaikinya
    code = cli.main(["--config", str(config_path), "local-unset", "live.kunci_lama"])
    assert code == cli.EXIT_OK, capsys.readouterr()
    assert yaml.safe_load(path.read_text()) == {"live": {"enabled": True}}
    assert cli.main(["--config", str(config_path), "check-config"]) == cli.EXIT_OK
    capsys.readouterr()
    code = cli.main(["--config", str(config_path), "local-unset", "live.enabled"])
    assert code == cli.EXIT_OK, capsys.readouterr()
    assert yaml.safe_load(path.read_text()) in ({}, {"live": {}}, None)
    code = cli.main(["--config", str(config_path), "local-unset", "live.tidak_ada"])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "tidak ada" in capsys.readouterr().err


def test_set_local_values_is_atomic_when_the_final_replace_fails(project_dir: Path, monkeypatch):
    import os

    path = set_local_values(project_dir / "config", {"live.enabled": False})
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk penuh")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        set_local_values(project_dir / "config", {"live.enabled": True})
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in (project_dir / "config").iterdir()) == [
        "default.yaml",
        LOCAL_CONFIG_NAME,
    ]
