#!/bin/bash
# Hentikan paper run dengan rapi: file STOP -> bot berhenti di iterasi berikutnya
# (kill switch, exit 6, supervisor tidak memulai ulang) -> agent dilepas dari launchd.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.tradebot.paper
cd "$REPO"
touch STOP
echo "file STOP dibuat; menunggu bot berhenti (maksimal 90 detik)..."
for _ in $(seq 1 90); do
  if [ ! -f state/paper_supervisor.pid ]; then break; fi
  sleep 1
done
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f STOP
if [ -f state/paper_supervisor.pid ]; then
  echo "supervisor masih tercatat (pid $(cat state/paper_supervisor.pid)); dihentikan paksa" >&2
  kill "$(cat state/paper_supervisor.pid)" 2>/dev/null || true
  rm -f state/paper_supervisor.pid
fi
echo "berhenti. Baris terakhir log:"
tail -n 3 logs/tradebot.log 2>/dev/null || true
