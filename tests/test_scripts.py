"""Skrip operasional (macOS/Finder) diuji tanpa launchd dan tanpa jaringan.

Yang bisa diuji di Linux: sintaks, penulisan .env tanpa pernah menampilkan nilai,
pemasang .command, dan logika supervisor dengan `uv` serta `caffeinate` palsu.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="butuh bash")


def _copy_scripts(target: Path) -> Path:
    dest = target / "scripts"
    shutil.copytree(SCRIPTS, dest)
    return dest


def _fake_bin(target: Path, exit_code: int) -> Path:
    """`uv` palsu mencatat argumennya dan keluar dengan kode tertentu; caffeinate meneruskan."""
    fake = target / "bin"
    fake.mkdir()
    (fake / "uv").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$FAKE_UV_LOG"\nexit "$FAKE_UV_EXIT"\n'
    )
    (fake / "caffeinate").write_text('#!/bin/bash\nshift 2\nexec "$@"\n')
    for name in ("uv", "caffeinate"):
        (fake / name).chmod(0o755)
    return fake


@pytest.mark.parametrize("script", sorted(p.name for p in SCRIPTS.glob("*.sh")))
def test_scripts_parse(script: str):
    subprocess.run(["bash", "-n", str(SCRIPTS / script)], check=True)


def test_every_script_is_executable():
    for path in SCRIPTS.glob("*.sh"):
        assert path.stat().st_mode & stat.S_IXUSR, f"{path.name} tidak executable"


def test_write_env_keeps_other_lines_and_never_prints_the_values(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# komentar\nBINANCE_TESTNET_API_KEY=bk\nBINANCE_TESTNET_API_SECRET=bs\n"
        "TRADING_MODE=paper\nTOKOCRYPTO_API_KEY=lama\n"
    )
    result = subprocess.run(
        ["bash", str(scripts / "write-env.sh"), "live"],
        input="KEYVALUE123\nSECRETVALUE456\n",
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    text = env_file.read_text()
    lines = text.splitlines()
    assert "# komentar" in lines
    assert "BINANCE_TESTNET_API_KEY=bk" in lines and "BINANCE_TESTNET_API_SECRET=bs" in lines
    assert lines.count("TRADING_MODE=live") == 1 and "TRADING_MODE=paper" not in lines
    assert "TOKOCRYPTO_API_KEY=KEYVALUE123" in lines and "TOKOCRYPTO_API_KEY=lama" not in lines
    assert "TOKOCRYPTO_API_SECRET=SECRETVALUE456" in lines
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    for stream in (result.stdout, result.stderr):
        assert "KEYVALUE123" not in stream and "SECRETVALUE456" not in stream
    assert not list(tmp_path.glob(".env.*")), "file sementara tertinggal"


def test_write_env_refuses_empty_or_multiword_values(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    for payload in ("\nsecret\n", "key\n\n", "key with space\nsecret\n"):
        result = subprocess.run(
            ["bash", str(scripts / "write-env.sh"), "live"],
            input=payload,
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 2, payload
        assert not (tmp_path / ".env").exists()


def test_install_commands_adds_flag_detection_only_where_needed(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = subprocess.run(
        ["bash", str(scripts / "install-commands.sh")], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in tmp_path.glob("*.command"))
    assert names == [
        "backtest.command",
        "compare-paper.command",
        "fetch-data.command",
        "live-setup.command",
        "live-size.command",
        "live-start.command",
        "live-stop.command",
        "paper-checklist.command",
        "paper-start.command",
        "paper-stop.command",
        "preflight.command",
        "status.command",
        "tests.command",
    ]
    status = (tmp_path / "status.command").read_text()
    assert "TRADING_MODE=live" in status and "--i-know-what-im-doing" in status
    assert "uv run tradebot status $FLAG" in status
    tests_cmd = (tmp_path / "tests.command").read_text()
    assert "--i-know-what-im-doing" not in tests_cmd
    assert "uv run pytest" in tests_cmd
    exclude = tmp_path / ".git" / "info" / "exclude"
    assert "*.command" in exclude.read_text().splitlines()
    for name in names:
        subprocess.run(["bash", "-n", str(tmp_path / name)], check=True)


def test_supervisor_live_passes_the_flag_and_does_not_restart_after_preflight_failure(
    tmp_path: Path,
):
    scripts = _copy_scripts(tmp_path)
    fake = _fake_bin(tmp_path, exit_code=9)
    log = tmp_path / "uv.log"
    env = {
        **os.environ,
        "PATH": f"{fake}:{os.environ['PATH']}",
        "FAKE_UV_LOG": str(log),
        "FAKE_UV_EXIT": "9",
        "MAX_RESTARTS": "3",
    }
    result = subprocess.run(
        ["bash", str(scripts / "bot-supervisor.sh"), "live"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ["run tradebot run --i-know-what-im-doing"]
    state = json.loads((tmp_path / "state" / "live_supervisor.json").read_text())
    assert state["restarts"] == 0 and state["last_exit_code"] == 9 and state["running"] is False
    assert not (tmp_path / "state" / "live_supervisor.pid").exists()
    out = (tmp_path / "logs" / "live.out").read_text()
    assert "tidak dimulai ulang (exit 9)" in out


def test_supervisor_paper_never_passes_the_flag_and_gives_up_after_max_restarts(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    fake = _fake_bin(tmp_path, exit_code=3)
    log = tmp_path / "uv.log"
    env = {
        **os.environ,
        "PATH": f"{fake}:{os.environ['PATH']}",
        "FAKE_UV_LOG": str(log),
        "FAKE_UV_EXIT": "3",
        "MAX_RESTARTS": "0",
    }
    result = subprocess.run(
        ["bash", str(scripts / "paper-supervisor.sh")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ["run tradebot run"]
    state = json.loads((tmp_path / "state" / "paper_supervisor.json").read_text())
    assert state["restarts"] == 1 and state["last_exit_code"] == 3
    assert "menyerah setelah 0 kali" in (tmp_path / "logs" / "paper.out").read_text()


def test_supervisor_rejects_unknown_mode(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    result = subprocess.run(
        ["bash", str(scripts / "bot-supervisor.sh"), "mainnet"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert "paper atau live" in result.stderr


def test_plist_template_has_placeholders_for_mode_and_label():
    text = (SCRIPTS / "com.tradebot.plist.template").read_text()
    for token in ("__REPO__", "__PATH__", "__LABEL__", "__MODE__"):
        assert token in text, token
    assert "bot-supervisor.sh" in text
    assert "TRADING_MODE" in text, "mode dipaksa lewat environment launchd, bukan dari .env"
