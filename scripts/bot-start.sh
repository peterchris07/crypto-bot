#!/bin/bash
# Pasang dan mulai bot sebagai LaunchAgent macOS: `bot-start.sh paper` atau `bot-start.sh live`.
# Aman dijalankan ulang. Urutan: fetch-data (cache OHLCV) -> pasang plist -> mulai supervisor.
# Untuk live, jangan panggil langsung: live-start.sh yang menjalankan preflight dan meminta
# konfirmasi sebelum sampai ke sini.
set -eu
MODE="${1:?mode: paper atau live}"
case "$MODE" in
  paper|live) ;;
  *) echo "mode harus paper atau live, dapat '$MODE'" >&2; exit 2 ;;
esac
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tradebot.$MODE"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
cd "$REPO"
if [ -f STOP ]; then
  echo "file STOP masih ada di $REPO; hapus dulu kalau memang mau bot jalan" >&2
  exit 2
fi
for other in paper live; do
  pid_file="state/${other}_supervisor.pid"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "supervisor $other sudah jalan (pid $(cat "$pid_file")); jalankan ${other}-stop dulu. Paper dan live tidak dijalankan bersamaan." >&2
    exit 2
  fi
done
if [ "$MODE" = live ]; then
  grep -qsE '^TRADING_MODE=live$' .env || { echo "TRADING_MODE=live belum ada di .env; jalankan live-setup dulu" >&2; exit 2; }
  [ -f config/local.yaml ] || { echo "config/local.yaml belum ada; jalankan live-setup dulu" >&2; exit 2; }
fi
mkdir -p logs state "$HOME/Library/LaunchAgents"
echo "== fetch-data"
uv run tradebot fetch-data
echo "== pasang $PLIST"
sed -e "s#__REPO__#$REPO#g" -e "s#__PATH__#$PATH#g" -e "s#__LABEL__#$LABEL#g" -e "s#__MODE__#$MODE#g" \
  scripts/com.tradebot.plist.template > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 3
echo "== status"
launchctl print "gui/$(id -u)/$LABEL" | grep -E "state|pid" | head -3 || true
echo "supervisor pid: $(cat "state/${MODE}_supervisor.pid" 2>/dev/null || echo belum ada)"
if [ "$MODE" = live ]; then
  echo "log bot: $REPO/logs/live/tradebot.log"
  echo "cek keadaan: status.command (atau uv run tradebot status --i-know-what-im-doing)"
  echo "hentikan: live-stop.command"
else
  echo "log bot: $REPO/logs/tradebot.log"
  echo "cek keadaan: status.command (atau uv run tradebot status)"
  echo "hentikan: paper-stop.command"
fi
echo "log supervisor dan stdout: $REPO/logs/$MODE.out"
