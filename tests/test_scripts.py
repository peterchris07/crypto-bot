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
    """`uv` palsu mencatat argumennya; `tradebot run` keluar dengan kode tertentu, perintah
    lain sukses. caffeinate meneruskan; launchctl hanya mencatat argumennya."""
    fake = target / "bin"
    fake.mkdir()
    (fake / "uv").write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$FAKE_UV_LOG"\n'
        'case "$*" in "run tradebot run"*) exit "$FAKE_UV_EXIT" ;; esac\n'
        "exit 0\n"
    )
    (fake / "caffeinate").write_text('#!/bin/bash\nshift 2\nexec "$@"\n')
    (fake / "launchctl").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "${FAKE_LAUNCHCTL_LOG:-/dev/null}"\nexit 0\n'
    )
    for name in ("uv", "caffeinate", "launchctl"):
        (fake / name).chmod(0o755)
    return fake


def _fake_env(tmp_path: Path, fake: Path, exit_code: int, max_restarts: int) -> dict:
    return {
        **os.environ,
        "PATH": f"{fake}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "FAKE_LAUNCHCTL_LOG": str(tmp_path / "launchctl.log"),
        "FAKE_UV_EXIT": str(exit_code),
        "MAX_RESTARTS": str(max_restarts),
    }


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
        "check-exchange.command",
        "compare-paper.command",
        "fetch-data.command",
        "ledger-status.command",
        "live-setup.command",
        "live-size.command",
        "live-start.command",
        "live-stop.command",
        "paper-checklist.command",
        "paper-start.command",
        "paper-stop.command",
        "pipe-test.command",
        "preflight.command",
        "status.command",
        "tests.command",
    ]
    status = (tmp_path / "status.command").read_text()
    # deteksi mode lewat satu helper, bukan grep ketat yang tidak tahan tanda kutip/spasi
    assert "scripts/env-mode.sh" in status and "--i-know-what-im-doing" in status
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
    env = _fake_env(tmp_path, fake, exit_code=9, max_restarts=3)
    result = subprocess.run(
        ["bash", str(scripts / "bot-supervisor.sh"), "live"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    # setelah berhenti tanpa mulai ulang: live dimatikan lagi dan agent dilepas dari launchd,
    # supaya login berikutnya tidak menghidupkan bot live tanpa persetujuan
    assert (tmp_path / "uv.log").read_text().splitlines() == [
        "run tradebot run --i-know-what-im-doing",
        "run tradebot local-set live.enabled=false --i-know-what-im-doing",
    ]
    launchctl = (tmp_path / "launchctl.log").read_text().splitlines()
    assert launchctl == [f"bootout gui/{os.getuid()}/com.tradebot.live"]
    state = json.loads((tmp_path / "state" / "live_supervisor.json").read_text())
    assert state["restarts"] == 0 and state["last_exit_code"] == 9 and state["running"] is False
    assert not (tmp_path / "state" / "live_supervisor.pid").exists()
    out = (tmp_path / "logs" / "live.out").read_text()
    assert "tidak dimulai ulang (exit 9)" in out
    assert stat.S_IMODE((tmp_path / "logs" / "live.out").stat().st_mode) == 0o600


def test_supervisor_paper_never_passes_the_flag_and_gives_up_after_max_restarts(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    fake = _fake_bin(tmp_path, exit_code=3)
    env = _fake_env(tmp_path, fake, exit_code=3, max_restarts=0)
    result = subprocess.run(
        ["bash", str(scripts / "paper-supervisor.sh")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "uv.log").read_text().splitlines() == ["run tradebot run"]
    launchctl = (tmp_path / "launchctl.log").read_text().splitlines()
    assert launchctl == [f"bootout gui/{os.getuid()}/com.tradebot.paper"]
    state = json.loads((tmp_path / "state" / "paper_supervisor.json").read_text())
    assert state["restarts"] == 1 and state["last_exit_code"] == 3
    assert "menyerah setelah 0 kali" in (tmp_path / "logs" / "paper.out").read_text()


def test_bot_start_live_installs_the_agent_and_unloads_the_other_mode(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    fake = _fake_bin(tmp_path, exit_code=0)
    env = _fake_env(tmp_path, fake, exit_code=0, max_restarts=0)
    (tmp_path / ".env").write_text("TRADING_MODE=live\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.yaml").write_text("live:\n  enabled: true\n")
    result = subprocess.run(
        ["bash", str(scripts / "bot-start.sh"), "live"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / "com.tradebot.live.plist"
    assert plist.exists()
    assert "<string>live</string>" in plist.read_text() and "bot-supervisor.sh" in plist.read_text()
    uid = os.getuid()
    launchctl = (tmp_path / "launchctl.log").read_text().splitlines()
    # agent mode lain dilepas dulu (paper dan live tidak bersamaan, juga setelah login berikutnya),
    # lalu agent ini dilepas dan dipasang; bootstrap dengan RunAtLoad sudah memulainya
    assert launchctl[:3] == [
        f"bootout gui/{uid}/com.tradebot.paper",
        f"bootout gui/{uid}/com.tradebot.live",
        f"bootstrap gui/{uid} {plist}",
    ]
    assert not any(line.startswith("kickstart") for line in launchctl)
    assert (tmp_path / "uv.log").read_text().splitlines() == ["run tradebot fetch-data"]


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


def test_write_env_keeps_a_secret_without_trailing_newline(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    result = subprocess.run(
        ["bash", str(scripts / "write-env.sh"), "live"],
        input="KEYVALUE123\nSECRETVALUE456",
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "TOKOCRYPTO_API_SECRET=SECRETVALUE456" in (tmp_path / ".env").read_text().splitlines()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("TRADING_MODE=live", "live"),
        ('TRADING_MODE="live"', "live"),
        ("TRADING_MODE='live'", "live"),
        ("TRADING_MODE=Live ", "live"),
        ("export TRADING_MODE=live", "live"),
        ("TRADING_MODE=live\r", "live"),
        ("TRADING_MODE=paper", "paper"),
        ("TRADING_MODE=", "paper"),
        ("# TRADING_MODE=live", "paper"),
        ("", "paper"),
    ],
)
def test_env_mode_helper_reads_trading_mode_like_the_bot_does(tmp_path: Path, line, expected):
    """python-dotenv + resolve_mode menerima kutip, spasi, dan huruf besar; helper shell juga."""
    scripts = _copy_scripts(tmp_path)
    (tmp_path / ".env").write_text(f"BINANCE_TESTNET_API_KEY=x\n{line}\n")
    result = subprocess.run(
        ["bash", str(scripts / "env-mode.sh")], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert "x" not in result.stdout.replace("paper", "").replace("live", "")


def test_env_mode_helper_without_env_file_is_paper(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    result = subprocess.run(
        ["bash", str(scripts / "env-mode.sh")], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0 and result.stdout.strip() == "paper"


def test_render_plist_escapes_repo_path_and_path_for_xml(tmp_path: Path):
    from xml.etree import ElementTree as ET

    scripts = _copy_scripts(tmp_path)
    repo = "/Users/x/Crypto & Bot#1/<repo>"
    path = "/opt/homebrew/bin:/usr/bin:/a&b"
    result = subprocess.run(
        ["bash", str(scripts / "render-plist.sh"), "live", repo, path],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    root = ET.fromstring(result.stdout)
    keys = [el.text for el in root.find("dict").findall("key")]
    values = list(root.find("dict"))
    by_key = {values[i].text: values[i + 1] for i in range(0, len(values), 2)}
    assert by_key["Label"].text == "com.tradebot.live"
    args = [s.text for s in by_key["ProgramArguments"].findall("string")]
    assert args == ["/bin/bash", f"{repo}/scripts/bot-supervisor.sh", "live"]
    env = by_key["EnvironmentVariables"]
    env_pairs = list(env)
    env_map = {env_pairs[i].text: env_pairs[i + 1].text for i in range(0, len(env_pairs), 2)}
    assert env_map == {"PATH": path, "TRADING_MODE": "live"}
    assert by_key["WorkingDirectory"].text == repo
    assert "Label" in keys and by_key["RunAtLoad"].tag == "true"


def test_live_setup_refuses_while_paper_supervisor_is_alive(tmp_path: Path):
    scripts = _copy_scripts(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paper_supervisor.pid").write_text(f"{os.getpid()}\n")
    result = subprocess.run(
        ["bash", str(scripts / "live-setup.sh")],
        input="",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 2
    assert "paper-stop" in result.stderr
    assert not (tmp_path / ".env").exists()


def test_bot_start_does_not_restart_the_job_it_just_bootstrapped():
    text = (SCRIPTS / "bot-start.sh").read_text()
    assert "kickstart -k" not in text, "bootstrap dengan RunAtLoad sudah memulai job"
    assert "render-plist.sh" in text and "sed -e" not in text


def test_secret_handling_scripts_disable_xtrace():
    for name in ("write-env.sh", "live-setup.sh"):
        text = (SCRIPTS / name).read_text()
        assert "set +o xtrace" in text, name


def test_plist_template_has_placeholders_for_mode_and_label():
    text = (SCRIPTS / "com.tradebot.plist.template").read_text()
    for token in ("__REPO__", "__PATH__", "__LABEL__", "__MODE__"):
        assert token in text, token
    assert "bot-supervisor.sh" in text
    assert "TRADING_MODE" in text, "mode dipaksa lewat environment launchd, bukan dari .env"
