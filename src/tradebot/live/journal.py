"""Jurnal order write-ahead (Aturan Keras 5).

Setiap order ditulis ke jurnal SEBELUM dikirim, sebagai baris JSON dengan
event "intent" dan client_order_id buatan bot. Setelah ada jawaban, baris
"result" ditambahkan; kalau jawaban hilang di jaringan, baris "unknown". Saat
start, intent yang belum punya result atau reconciled adalah order yang mungkin
sudah masuk: runner mencarinya di exchange lewat client_order_id dan mencatat
hasilnya sebagai "reconciled". Order seperti itu TIDAK PERNAH dikirim ulang.

Append-only, satu baris per kejadian, fsync setiap baris. File ini bukan log
debug: isinya yang menentukan apa yang harus direkonsiliasi setelah crash.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradebot.exchange.base import Order

INTENT = "intent"
RESULT = "result"
UNKNOWN = "unknown"
RECONCILED = "reconciled"
CANCEL = "cancel"  # pembatalan stop lapis 2 sebelum order keluar, dengan hasilnya
CLOSING_EVENTS = {RESULT, RECONCILED}


class OrderJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _append(self, event: str, client_order_id: str, time: datetime, **fields: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "event": event,
                "client_order_id": client_order_id,
                "time": time.astimezone(UTC).isoformat(),
                **fields,
            },
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def record_intent(
        self,
        client_order_id: str,
        *,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        reason: str,
        time: datetime,
    ) -> None:
        self._append(
            INTENT,
            client_order_id,
            time,
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            reason=reason,
        )

    def record_result(self, client_order_id: str, order: Order, time: datetime) -> None:
        self._append(
            RESULT,
            client_order_id,
            time,
            order_id=order.id,
            status=order.status.value,
            filled=order.filled,
            average=order.average,
            cost=order.cost,
            fee=order.fee,
        )

    def record_unknown(self, client_order_id: str, error: str, time: datetime) -> None:
        self._append(UNKNOWN, client_order_id, time, error=error)

    def record_reconciled(
        self, client_order_id: str, outcome: str, time: datetime, order: Order | None = None
    ) -> None:
        fields: dict[str, Any] = {"outcome": outcome}
        if order is not None:
            fields.update(
                order_id=order.id,
                status=order.status.value,
                filled=order.filled,
                average=order.average,
                cost=order.cost,
                fee=order.fee,
            )
        self._append(RECONCILED, client_order_id, time, **fields)

    def record_cancel(
        self, client_order_id: str, order_id: str | None, outcome: str, time: datetime
    ) -> None:
        self._append(CANCEL, client_order_id, time, order_id=order_id, outcome=outcome)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"jurnal {self.path} baris {number} rusak: {exc}") from exc
        return rows

    def pending_intents(self) -> list[dict[str, Any]]:
        """Intent yang belum ditutup result atau reconciled: order yang MUNGKIN sudah masuk."""
        closed = {e["client_order_id"] for e in self.entries() if e["event"] in CLOSING_EVENTS}
        return [
            e for e in self.entries() if e["event"] == INTENT and e["client_order_id"] not in closed
        ]
