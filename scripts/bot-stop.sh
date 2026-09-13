#!/bin/bash
# Hentikan bot dengan rapi: `bot-stop.sh paper` atau `bot-stop.sh live`.
# File STOP -> bot berhenti di iterasi berikutnya (kill switch, exit 6, supervisor tidak
# memulai ulang) -> agent dilepas dari launchd. File STOP satu untuk semua mode: kalau
# keduanya jalan, keduanya berhenti.
set -u
MODE="${1:?mode: paper atau live}"
case "$MODE" in
  paper|live) ;;
  *) echo "mode harus paper atau live, dapat '$MODE'" >&2; exit 2 ;;
esac
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tradebot.$MODE"
PID_FILE="state/${MODE}_supervisor.pid"
cd "$REPO"
touch STOP
echo "file STOP dibuat; menunggu bot $MODE berhenti (maksimal 90 detik)..."
for _ in $(seq 1 90); do
  if [ ! -f "$PID_FILE" ]; then break; fi
  sleep 1
done
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f STOP
if [ -f "$PID_FILE" ]; then
  echo "supervisor masih tercatat (pid $(cat "$PID_FILE")); dihentikan paksa" >&2
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  rm -f "$PID_FILE"
fi
echo "berhenti. Baris terakhir log:"
if [ "$MODE" = live ]; then
  tail -n 3 logs/live/tradebot.log 2>/dev/null || true
else
  tail -n 3 logs/tradebot.log 2>/dev/null || true
fi
