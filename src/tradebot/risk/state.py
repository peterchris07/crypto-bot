"""State harian risk manager, dipersist ke live.state_path.

Batas rugi harian dihitung dari equity awal hari (UTC). Angka itu harus selamat
dari restart proses: kalau bot mati dan dijalankan lagi di hari yang sama, equity
awal hari tetap yang tadi pagi, bukan equity saat restart, supaya kerugian yang
sudah terjadi tidak "terlupakan". Ditulis atomik seperti cache.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tradebot.data.cache import atomic_write


@dataclass(frozen=True)
class DailyState:
    day: str  # tanggal UTC, ISO (YYYY-MM-DD)
    start_equity: float
    updated_at: str  # ISO 8601 UTC, informasi untuk manusia

    @staticmethod
    def for_day(now: datetime, equity: float) -> DailyState:
        return DailyState(
            day=utc_day(now).isoformat(),
            start_equity=float(equity),
            updated_at=now.astimezone(UTC).isoformat(),
        )


def utc_day(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("waktu harus sadar zona (tz-aware); batas hari memakai UTC")
    return now.astimezone(UTC).date()


class DailyStateStore:
    """Baca dan tulis DailyState di satu file JSON. File rusak adalah error, bukan reset diam."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> DailyState | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return DailyState(
                day=str(raw["day"]),
                start_equity=float(raw["start_equity"]),
                updated_at=str(raw.get("updated_at", "")),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(
                f"state harian {self.path} rusak: {exc}. Hapus file itu hanya kalau Anda yakin "
                "equity awal hari boleh diambil dari equity sekarang."
            ) from exc

    def save(self, state: DailyState) -> None:
        text = json.dumps(asdict(state), indent=2) + "\n"
        atomic_write(self.path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
