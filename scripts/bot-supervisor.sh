#!/bin/bash
# Supervisor bot per mode: `bot-supervisor.sh paper` atau `bot-supervisor.sh live`.
# Dijalankan launchd (lihat bot-start.sh), bukan langsung.
#
# Menjalankan `tradebot run` di bawah caffeinate supaya Mac tidak idle-sleep, dan
# memulai ulang kalau prosesnya mati karena error sementara. TIDAK memulai ulang
# kalau bot berhenti karena kill switch (exit 6), selesai normal (0), config salah
# (2), atau preflight live gagal (9): itu keputusan yang harus dilihat manusia,
# bukan ditimpa supervisor.
#
# Mode live menambahkan flag --i-know-what-im-doing; TRADING_MODE=live datang dari
# environment launchd dan .env. Kunci hanya dibaca bot sendiri dari .env.
set -u
MODE="${1:-paper}"
case "$MODE" in
  paper|live) ;;
  *) echo "mode harus paper atau live, dapat '$MODE'" >&2; exit 2 ;;
esac
cd "$(dirname "$0")/.." || exit 2
# logs/<mode>.out memuat stdout bot (banner dengan kunci tersamar, harga, saldo): jangan
# bisa dibaca akun lain di Mac yang sama.
umask 077
mkdir -p logs state
PID_FILE="state/${MODE}_supervisor.pid"
echo $$ > "$PID_FILE"
OUT="logs/${MODE}.out"
STATE="state/${MODE}_supervisor.json"
MAX_RESTARTS=${MAX_RESTARTS:-10}
FLAG=""
[ "$MODE" = live ] && FLAG="--i-know-what-im-doing"
restarts=0
last_restart=null
last_exit=null
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
started_at="$(stamp)"
# Keadaan supervisor untuk `tradebot status`: berapa kali mulai ulang dan kapan terakhir,
# supaya bug yang berulang tidak tersembunyi di balik mulai ulang otomatis.
write_state() {
  printf '{"mode": "%s", "started_at": "%s", "restarts": %d, "max_restarts": %d, "last_restart": %s, "last_exit_code": %s, "updated_at": "%s", "running": %s}\n' \
    "$MODE" "$started_at" "$restarts" "$MAX_RESTARTS" "$last_restart" "$last_exit" "$(stamp)" "$1" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}
write_state true
while true; do
  echo "$(stamp) supervisor[$MODE]: mulai tradebot run (pid supervisor $$)" >> "$OUT"
  # -i: cegah idle sleep; -s: cegah system sleep selama di listrik. Tutup tutup laptop
  # tetap membuat Mac tidur; lihat README bagian paper berhari-hari.
  # shellcheck disable=SC2086
  caffeinate -i -s uv run tradebot run $FLAG >> "$OUT" 2>&1
  code=$?
  last_exit=$code
  echo "$(stamp) supervisor[$MODE]: tradebot run keluar dengan exit $code" >> "$OUT"
  case "$code" in
    0|2|6|9)
      echo "$(stamp) supervisor[$MODE]: tidak dimulai ulang (exit $code); jalankan ${MODE}-start lagi kalau memang mau" >> "$OUT"
      break
      ;;
  esac
  restarts=$((restarts + 1))
  last_restart="\"$(stamp)\""
  if [ "$restarts" -gt "$MAX_RESTARTS" ]; then
    echo "$(stamp) supervisor[$MODE]: menyerah setelah $MAX_RESTARTS kali mulai ulang" >> "$OUT"
    break
  fi
  echo "$(stamp) supervisor[$MODE]: mulai ulang ke-$restarts dalam 60 detik" >> "$OUT"
  write_state true
  sleep 60
done
write_state false
rm -f "$PID_FILE"
# Berhenti tanpa mulai ulang (kill switch, config, preflight, menyerah): jangan biarkan
# launchd menghidupkan bot lagi di login berikutnya tanpa persetujuan. Live dimatikan lagi
# (live-start meminta SAYA SIAP untuk menyalakannya), lalu agent ini dilepas. bootout
# membunuh proses ini juga, jadi harus menjadi perintah terakhir.
if [ "$MODE" = live ]; then
  uv run tradebot local-set live.enabled=false --i-know-what-im-doing >> "$OUT" 2>&1 \
    || echo "$(stamp) supervisor[$MODE]: GAGAL mengembalikan live.enabled ke false; jalankan live-stop" >> "$OUT"
fi
echo "$(stamp) supervisor[$MODE]: melepas agent com.tradebot.$MODE dari launchd" >> "$OUT"
if command -v launchctl >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/com.tradebot.$MODE" 2>/dev/null || true
fi
