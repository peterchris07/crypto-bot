#!/bin/bash
# Supervisor paper run. Dijalankan launchd (lihat paper-start.sh), bukan langsung.
#
# Menjalankan `tradebot run` di bawah caffeinate supaya Mac tidak idle-sleep, dan
# memulai ulang kalau prosesnya mati karena error sementara. TIDAK memulai ulang
# kalau bot berhenti karena kill switch (exit 6), selesai normal (0), atau config
# salah (2): itu keputusan yang harus dilihat manusia, bukan ditimpa supervisor.
set -u
cd "$(dirname "$0")/.." || exit 2
mkdir -p logs state
echo $$ > state/paper_supervisor.pid
OUT=logs/paper.out
MAX_RESTARTS=${MAX_RESTARTS:-10}
restarts=0
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
while true; do
  echo "$(stamp) supervisor: mulai tradebot run (pid supervisor $$)" >> "$OUT"
  # -i: cegah idle sleep; -s: cegah system sleep selama di listrik. Tutup tutup laptop
  # tetap membuat Mac tidur; lihat README bagian paper berhari-hari.
  caffeinate -i -s uv run tradebot run >> "$OUT" 2>&1
  code=$?
  echo "$(stamp) supervisor: tradebot run keluar dengan exit $code" >> "$OUT"
  case "$code" in
    0|2|6)
      echo "$(stamp) supervisor: tidak dimulai ulang (exit $code); jalankan paper-start.sh lagi kalau memang mau" >> "$OUT"
      break
      ;;
  esac
  restarts=$((restarts + 1))
  if [ "$restarts" -gt "$MAX_RESTARTS" ]; then
    echo "$(stamp) supervisor: menyerah setelah $MAX_RESTARTS kali mulai ulang" >> "$OUT"
    break
  fi
  echo "$(stamp) supervisor: mulai ulang ke-$restarts dalam 60 detik" >> "$OUT"
  sleep 60
done
rm -f state/paper_supervisor.pid
