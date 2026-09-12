#!/bin/bash
# Pasang dan mulai paper run sebagai LaunchAgent macOS. Aman dijalankan ulang.
# Urutan: fetch-data (cache OHLCV) -> pasang plist -> mulai supervisor.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.tradebot.paper
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
cd "$REPO"
if [ -f STOP ]; then
  echo "file STOP masih ada di $REPO; hapus dulu kalau memang mau bot jalan" >&2
  exit 2
fi
if [ -f state/paper_supervisor.pid ] && kill -0 "$(cat state/paper_supervisor.pid)" 2>/dev/null; then
  echo "supervisor sudah jalan (pid $(cat state/paper_supervisor.pid)); jalankan paper-stop.sh dulu" >&2
  exit 2
fi
mkdir -p logs state "$HOME/Library/LaunchAgents"
echo "== fetch-data"
uv run tradebot fetch-data
echo "== pasang $PLIST"
sed -e "s#__REPO__#$REPO#g" -e "s#__PATH__#$PATH#g" scripts/com.tradebot.paper.plist.template > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 3
echo "== status"
launchctl print "gui/$(id -u)/$LABEL" | grep -E "state|pid" | head -3 || true
echo "supervisor pid: $(cat state/paper_supervisor.pid 2>/dev/null || echo belum ada)"
echo "log bot: $REPO/logs/tradebot.log"
echo "log supervisor dan stdout: $REPO/logs/paper.out"
echo "cek keadaan: uv run tradebot status"
echo "hentikan: scripts/paper-stop.sh"
