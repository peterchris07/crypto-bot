"""Taksonomi error exchange.

Dua cabang yang menentukan perilaku:

RetryableExchangeError  gangguan sementara (jaringan, timeout, rate limit,
                        exchange maintenance). Adapter sudah mencoba ulang
                        dengan backoff; kalau sampai ke pemanggil, semua
                        percobaan habis. Runner menghitungnya sebagai satu
                        kegagalan koneksi untuk kill switch.

FatalExchangeError      salah konfigurasi atau salah logika (autentikasi,
                        saldo, order tidak valid, jam melenceng). Tidak pernah
                        dicoba ulang. Bot harus berhenti dan menyebut sebabnya.

OrderStateUnknownError  kasus khusus: order sudah dikirim tapi jawabannya
                        tidak sampai. Order mungkin masuk, mungkin tidak.
                        Tidak boleh dikirim ulang begitu saja; pemanggil harus
                        rekonsiliasi lewat client_order_id (jurnal, tahap 7).
"""

from __future__ import annotations


class ExchangeError(Exception):
    """Induk semua error dari lapisan exchange."""


class RetryableExchangeError(ExchangeError):
    """Gangguan sementara. Semua percobaan ulang sudah habis saat error ini muncul."""


class FatalExchangeError(ExchangeError):
    """Tidak boleh dicoba ulang. Bot harus berhenti."""


class AuthenticationError(FatalExchangeError):
    """Kunci salah, signature salah, IP tidak di whitelist, atau izin kurang."""


class InsufficientFundsError(FatalExchangeError):
    """Saldo tidak cukup, termasuk saldo yang terkunci oleh stop order di exchange."""


class InvalidOrderError(FatalExchangeError):
    """Order ditolak exchange atau ditolak validasi lokal: ukuran, presisi, harga, notional."""


class OrderNotFoundError(FatalExchangeError):
    """Order yang dirujuk sudah tidak ada: sudah terisi, sudah dibatalkan, atau tidak pernah ada."""


class TimeDriftError(FatalExchangeError):
    """Jam lokal melenceng dari jam server melebihi batas config."""


class MainnetRefusedError(FatalExchangeError):
    """Klien mainnet berkunci diminta tanpa lewat jalur mode live yang sah."""


class OrderStateUnknownError(ExchangeError):
    """Order sudah dikirim, jawaban tidak sampai. Wajib rekonsiliasi, dilarang kirim ulang."""
