#!/bin/bash
# Mulai trial live Tokocrypto dari Finder (live-start.command). Urutan:
#   1. syarat: live-setup sudah jalan (.env TRADING_MODE=live, config/local.yaml ada),
#      paper tidak sedang jalan, file STOP tidak ada
#   2. preflight tanpa order; gagal = berhenti di sini
#   3. konfirmasi ketik SAYA SIAP -> live.enabled=true di config/local.yaml
#   4. bot-start.sh live: fetch-data, LaunchAgent com.tradebot.live, supervisor
# Order pertama dipaksa ke ukuran minimum exchange sampai `tradebot live-size --normal`.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
FLAG=--i-know-what-im-doing
echo "== tradebot live-start"
grep -qsE '^TRADING_MODE=live$' .env || { echo "TRADING_MODE=live belum ada di .env; jalankan live-setup dulu" >&2; exit 2; }
[ -f config/local.yaml ] || { echo "config/local.yaml belum ada; jalankan live-setup dulu" >&2; exit 2; }
if [ -f STOP ]; then
  echo "file STOP masih ada; hapus dulu (paper-stop/live-stop yang normal sudah menghapusnya)" >&2
  exit 2
fi
for other in paper live; do
  pid_file="state/${other}_supervisor.pid"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "supervisor $other sedang jalan (pid $(cat "$pid_file")); jalankan ${other}-stop dulu" >&2
    exit 2
  fi
done
echo "== preflight (tidak mengirim order)"
uv run tradebot preflight $FLAG || { code=$?; echo "preflight gagal (exit $code); live tidak dimulai" >&2; exit "$code"; }
echo
echo "Mode live memakai uang asli di akun Tokocrypto Anda. Yang bisa hilang dibatasi saldo"
echo "USDT di akun itu. Order pertama berukuran minimum exchange; stop lapis 1 di bot dan"
echo "lapis 2 di exchange. Hentikan kapan saja dengan live-stop.command."
read -r -p "Ketik SAYA SIAP untuk mengaktifkan mode live: " jawab
[ "$jawab" = "SAYA SIAP" ] || { echo "dibatalkan; live.enabled tetap false" >&2; exit 2; }
uv run tradebot local-set live.enabled=true $FLAG
exec scripts/bot-start.sh live
