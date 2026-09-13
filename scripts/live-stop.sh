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
  uv run tradebot local-set live.enabled=false --i-know-what-im-doing || true
fi
