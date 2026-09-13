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
    path = set_local_values(project_dir, {"live.enabled": False, "risk.position_fraction": 0.2})
    assert path == project_dir / "config" / LOCAL_CONFIG_NAME
    set_local_values(project_dir, {"live.api_key_verified_date": "2026-09-13"})
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
        set_local_values(project_dir, {"live.enabled": True})
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
    project_dir: Path, config_path: Path, capsys
):
    (project_dir / ".env").write_text(
        "TRADING_MODE=live\nTOKOCRYPTO_API_KEY=k1234567890\nTOKOCRYPTO_API_SECRET=s1234567890\n"
    )
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
