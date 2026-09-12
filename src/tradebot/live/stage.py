"""Penanda ukuran order di live: "minimum" atau "normal", dipersist di live.stage_path.

Order pertama di uang asli dipaksa ke ukuran minimum exchange, bukan hasil sizing.
Naik ke ukuran normal hanya lewat keputusan sadar pemilik (perintah live-size
--normal), dan perintah itu menolak sebelum sejumlah siklus masuk-keluar selesai
dengan benar di ukuran minimum. Siklus dihitung runner setiap posisi ditutup.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from tradebot.data.cache import atomic_write

MINIMUM = "minimum"
NORMAL = "normal"


@dataclass(frozen=True)
class LiveStage:
    stage: str  # minimum | normal
    cycles_completed: int  # siklus masuk-keluar yang selesai di ukuran minimum
    updated_at: str
    note: str = ""


class LiveStageStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> LiveStage:
        if not self.path.exists():
            return LiveStage(MINIMUM, 0, datetime.now(tz=UTC).isoformat(), "belum pernah live")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            stage = LiveStage(**raw)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"penanda ukuran live {self.path} rusak: {exc}") from exc
        if stage.stage not in (MINIMUM, NORMAL):
            raise ValueError(
                f"penanda ukuran live {self.path}: stage {stage.stage!r} tidak dikenal"
            )
        return stage

    def save(self, stage: LiveStage) -> None:
        text = json.dumps(asdict(stage), indent=2, ensure_ascii=False) + "\n"
        atomic_write(self.path, lambda tmp: tmp.write_text(text, encoding="utf-8"))

    def record_cycle(self) -> LiveStage:
        current = self.load()
        updated = LiveStage(
            current.stage,
            current.cycles_completed + 1,
            datetime.now(tz=UTC).isoformat(),
            current.note,
        )
        self.save(updated)
        return updated

    def set_stage(self, stage: str, *, min_cycles: int, note: str = "") -> LiveStage:
        """Ubah penanda secara sadar. Naik ke normal ditolak sebelum siklus minimum selesai."""
        current = self.load()
        if stage == NORMAL and current.cycles_completed < min_cycles:
            raise ValueError(
                f"belum boleh naik ke ukuran normal: baru {current.cycles_completed} siklus "
                f"masuk-keluar selesai di ukuran minimum, butuh {min_cycles}"
            )
        updated = LiveStage(stage, current.cycles_completed, datetime.now(tz=UTC).isoformat(), note)
        self.save(updated)
        return updated
