"""Interface Strategy dan tipe Signal.

Satu method: terima DataFrame OHLCV, kembalikan Signal. Signal adalah STATE
target (LONG atau FLAT), bukan event; runner dan backtest membandingkannya
dengan posisi nyata dan membuat order hanya kalau berbeda. SHORT ada di enum
supaya interface tidak berubah nanti, tapi bot spot memperlakukannya sebagai
FLAT dengan peringatan (urusan lapisan di atas strategi, bukan di sini).

Jendela tetap. Setiap strategi menyatakan lookback_bars, dan signal() adalah
fungsi murni dari lookback_bars bar TERAKHIR yang diberikan: bar sebelum itu
diabaikan, dan kalau bar yang tersedia kurang dari itu hasilnya FLAT. Dengan
begitu backtest (yang punya seluruh histori) dan runner live (yang mengambil
sejumlah bar dari exchange) menghasilkan sinyal yang identik untuk bar yang
sama, syarat Aturan Keras 6.

Strategi tidak boleh tahu soal ukuran posisi, saldo, atau exchange. Itu urusan
RiskManager.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

import pandas as pd


class Signal(StrEnum):
    LONG = "long"
    FLAT = "flat"
    SHORT = "short"


class Strategy(ABC):
    name: str

    @property
    @abstractmethod
    def lookback_bars(self) -> int:
        """Jumlah bar terakhir yang dibaca signal(). Kurang dari ini, sinyal selalu FLAT."""

    @abstractmethod
    def signal(self, bars: pd.DataFrame) -> Signal:
        """State target untuk bar berikutnya, dihitung dari bar yang SUDAH TUTUP.

        Pemanggil bertanggung jawab tidak menyertakan bar yang masih berjalan; di
        backtest ini berarti keputusan untuk bar N dihitung dari bar sampai N-1.
        """
