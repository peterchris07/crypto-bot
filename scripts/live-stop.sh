#!/bin/bash
# Hentikan trial live dengan rapi (live-stop.command): bot-stop.sh live, lalu live.enabled
# dikembalikan ke false supaya memulai lagi selalu lewat live-start dan "SAYA SIAP".
# Posisi yang sedang dipegang TIDAK dijual otomatis; stop lapis 2 di exchange tetap
# terpasang selama bot mati. Lihat status.command untuk posisi dan stop-nya.
# File STOP satu untuk semua mode: kalau paper juga jalan, ia ikut berhenti.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
scripts/bot-stop.sh live
if [ -f config/local.yaml ] && [ "$(scripts/env-mode.sh)" = live ]; then
  if ! uv run tradebot local-set live.enabled=false --i-know-what-im-doing; then
    echo "GAGAL mengembalikan live.enabled ke false (kunci di .env hilang atau config/local.yaml rusak)." >&2
    echo "Bot sudah berhenti, tetapi live masih tercatat aktif. Perbaiki lalu jalankan:" >&2
    echo "  uv run tradebot local-set live.enabled=false --i-know-what-im-doing" >&2
    exit 2
  fi
fi
echo "live.enabled kembali false; memulai lagi hanya lewat live-start.command."
