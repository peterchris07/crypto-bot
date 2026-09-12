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
STATE=state/paper_supervisor.json
MAX_RESTARTS=${MAX_RESTARTS:-10}
restarts=0
last_restart=null
last_exit=null
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
started_at="$(stamp)"
# Keadaan supervisor untuk `tradebot status`: berapa kali mulai ulang dan kapan terakhir,
# supaya bug yang berulang tidak tersembunyi di balik mulai ulang otomatis.
write_state() {
  printf '{"started_at": "%s", "restarts": %d, "max_restarts": %d, "last_restart": %s, "last_exit_code": %s, "updated_at": "%s", "running": %s}\n' \
    "$started_at" "$restarts" "$MAX_RESTARTS" "$last_restart" "$last_exit" "$(stamp)" "$1" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}
write_state true
while true; do
  echo "$(stamp) supervisor: mulai tradebot run (pid supervisor $$)" >> "$OUT"
  # -i: cegah idle sleep; -s: cegah system sleep selama di listrik. Tutup tutup laptop
  # tetap membuat Mac tidur; lihat README bagian paper berhari-hari.
  caffeinate -i -s uv run tradebot run >> "$OUT" 2>&1
  code=$?
  last_exit=$code
  echo "$(stamp) supervisor: tradebot run keluar dengan exit $code" >> "$OUT"
  case "$code" in
    0|2|6)
      echo "$(stamp) supervisor: tidak dimulai ulang (exit $code); jalankan paper-start.sh lagi kalau memang mau" >> "$OUT"
      break
      ;;
  esac
  restarts=$((restarts + 1))
  last_restart="\"$(stamp)\""
  if [ "$restarts" -gt "$MAX_RESTARTS" ]; then
    echo "$(stamp) supervisor: menyerah setelah $MAX_RESTARTS kali mulai ulang" >> "$OUT"
    break
  fi
  echo "$(stamp) supervisor: mulai ulang ke-$restarts dalam 60 detik" >> "$OUT"
  write_state true
  sleep 60
done
write_state false
rm -f state/paper_supervisor.pid
