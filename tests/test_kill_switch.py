"""Tahap 6: setiap kill switch dipicu secara sintetis dan terbukti menghentikan bot.

Menghentikan bot = KillSwitchTriggered dilempar, membawa keputusan flatten dari
config risk.flatten_on. Batas rugi harian memakai equity awal hari UTC yang
dipersist dan selamat dari restart.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tradebot.config import load_settings
from tradebot.risk import (
    DailyState,
    DailyStateStore,
    KillSwitch,
    KillSwitchTriggered,
    RiskManager,
)

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
def settings(config_path):
    return load_settings(config_path, environ={})


def manager(settings, project_dir: Path, **overrides) -> RiskManager:
    config = dataclasses.replace(settings.risk, **overrides) if overrides else settings.risk
    return RiskManager(
        config,
        settings.costs,
        stop_file=project_dir / settings.risk.stop_file,
        state_store=DailyStateStore(project_dir / settings.live.state_path),
    )


# --------------------------------------------------------------------------- #
# Batas rugi harian
# --------------------------------------------------------------------------- #


def test_daily_loss_beyond_limit_triggers_with_flatten(settings, project_dir):
    risk = manager(settings, project_dir)
    assert risk.start_of_day_equity(1000.0, NOW) == 1000.0
    risk.check_daily_loss(971.0, NOW + timedelta(hours=1))  # rugi 2.9% < 3%
    risk.check_daily_loss(970.0, NOW + timedelta(hours=2))  # tepat 3%: belum melewati
    with pytest.raises(KillSwitchTriggered) as excinfo:
        risk.check_daily_loss(969.0, NOW + timedelta(hours=3))
    exc = excinfo.value
    assert exc.switch is KillSwitch.DAILY_LOSS
    assert exc.flatten is True, "risk.flatten_on.daily_loss: true"
    assert "3.10%" in str(exc) and "3.00%" in str(exc) and "flatten=True" in str(exc)


def test_daily_loss_uses_equity_at_start_of_utc_day_not_at_restart(settings, project_dir):
    risk = manager(settings, project_dir)
    risk.start_of_day_equity(1000.0, NOW)
    risk.check_daily_loss(980.0, NOW + timedelta(hours=1))
    # proses mati dan dijalankan lagi di hari yang sama dengan equity 980
    restarted = manager(settings, project_dir)
    assert restarted.daily is not None and restarted.daily.start_equity == 1000.0
    with pytest.raises(KillSwitchTriggered, match="equity awal hari 1000"):
        restarted.check_daily_loss(965.0, NOW + timedelta(hours=2))


def test_new_utc_day_resets_start_equity_and_persists(settings, project_dir):
    risk = manager(settings, project_dir)
    risk.start_of_day_equity(1000.0, NOW)
    tomorrow = datetime(2026, 9, 13, 0, 0, 1, tzinfo=UTC)
    risk.check_daily_loss(950.0, tomorrow)  # hari baru: 950 jadi equity awal, bukan rugi 5%
    assert risk.daily == DailyState(
        day="2026-09-13", start_equity=950.0, updated_at=tomorrow.isoformat()
    )
    on_disk = json.loads((project_dir / settings.live.state_path).read_text())
    assert on_disk["day"] == "2026-09-13" and on_disk["start_equity"] == 950.0
    # batas hari memakai UTC, bukan zona lokal
    late_wib = datetime(2026, 9, 13, 6, 59, tzinfo=UTC).astimezone(
        __import__("datetime").timezone(timedelta(hours=7))
    )
    assert risk.start_of_day_equity(940.0, late_wib) == 950.0


def test_daily_loss_rejects_naive_datetime(settings, project_dir):
    risk = manager(settings, project_dir)
    with pytest.raises(ValueError, match="UTC"):
        risk.check_daily_loss(1000.0, datetime(2026, 9, 12, 10, 0))


def test_corrupt_state_file_is_an_error_not_a_silent_reset(settings, project_dir):
    path = project_dir / settings.live.state_path
    path.parent.mkdir(parents=True)
    path.write_text("{bukan json", encoding="utf-8")
    with pytest.raises(ValueError, match="state harian"):
        manager(settings, project_dir)


def test_backtest_manager_without_store_keeps_state_in_memory(settings):
    risk = RiskManager(settings.risk, settings.costs)
    assert risk.daily is None
    risk.start_of_day_equity(1000.0, NOW)
    with pytest.raises(KillSwitchTriggered):
        risk.check_daily_loss(900.0, NOW + timedelta(hours=1))
    risk.check_stop_file()  # tanpa stop_file: tidak pernah terpicu


# --------------------------------------------------------------------------- #
# Runaway order
# --------------------------------------------------------------------------- #


def test_more_orders_than_limit_in_one_minute_stops_without_flatten(settings, project_dir):
    risk = manager(settings, project_dir)
    for k in range(5):  # batas 5
        risk.before_order(NOW + timedelta(seconds=k))
    with pytest.raises(KillSwitchTriggered) as excinfo:
        risk.before_order(NOW + timedelta(seconds=30))
    assert excinfo.value.switch is KillSwitch.RUNAWAY_ORDERS
    assert excinfo.value.flatten is False, "bug loop: menambah order memperparah"
    assert "tidak dikirim" in str(excinfo.value)


def test_order_window_slides_after_one_minute(settings, project_dir):
    risk = manager(settings, project_dir)
    for k in range(5):
        risk.before_order(NOW + timedelta(seconds=k))
    risk.before_order(NOW + timedelta(seconds=60))  # order pertama sudah keluar jendela
    with pytest.raises(KillSwitchTriggered):
        risk.before_order(NOW + timedelta(seconds=60, milliseconds=500))


# --------------------------------------------------------------------------- #
# Gagal koneksi beruntun
# --------------------------------------------------------------------------- #


def test_consecutive_failures_reaching_limit_stop_without_flatten(settings, project_dir):
    risk = manager(settings, project_dir)
    for _ in range(4):
        risk.record_connection_failure("timeout")
    with pytest.raises(KillSwitchTriggered) as excinfo:
        risk.record_connection_failure("timeout lagi")
    assert excinfo.value.switch is KillSwitch.CONNECTION_FAILURES
    assert excinfo.value.flatten is False
    assert "5 kegagalan" in str(excinfo.value) and "timeout lagi" in str(excinfo.value)


def test_one_success_resets_the_failure_streak(settings, project_dir):
    risk = manager(settings, project_dir)
    for _ in range(4):
        risk.record_connection_failure("x")
    risk.record_connection_success()
    for _ in range(4):
        risk.record_connection_failure("x")  # tidak terpicu: hitungan mulai dari nol lagi
    assert risk.consecutive_failures == 4


# --------------------------------------------------------------------------- #
# File STOP
# --------------------------------------------------------------------------- #


def test_stop_file_stops_without_flatten_until_removed(settings, project_dir):
    risk = manager(settings, project_dir)
    risk.check_stop_file()
    (project_dir / "STOP").write_text("", encoding="utf-8")
    with pytest.raises(KillSwitchTriggered) as excinfo:
        risk.check_stop_file()
    assert excinfo.value.switch is KillSwitch.STOP_FILE
    assert excinfo.value.flatten is False, "pengguna yang intervensi, pengguna yang memutuskan"
    assert "hapus file itu" in str(excinfo.value)
    (project_dir / "STOP").unlink()
    risk.check_stop_file()


def test_flatten_decision_follows_config_per_trigger(settings, project_dir):
    risk = manager(
        settings,
        project_dir,
        flatten_on=dataclasses.replace(settings.risk.flatten_on, daily_loss=False, stop_file=True),
    )
    (project_dir / "STOP").write_text("", encoding="utf-8")
    with pytest.raises(KillSwitchTriggered) as stop:
        risk.check_stop_file()
    assert stop.value.flatten is True
    risk.start_of_day_equity(1000.0, NOW)
    with pytest.raises(KillSwitchTriggered) as loss:
        risk.check_daily_loss(900.0, NOW + timedelta(hours=1))
    assert loss.value.flatten is False
