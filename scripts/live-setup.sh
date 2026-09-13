#!/bin/bash
# Persiapan trial live Tokocrypto dari Finder (live-setup.command), tanpa pernah menampilkan
# kunci. Urutan:
#   1. minta API key dan secret (ketikan tidak ditampilkan) -> ditulis hanya ke .env
#   2. check-config: kunci tersamar, mode live terbaca
#   3. konfirmasi pemeriksaan halaman API Management -> tanggal hari ini ke config/local.yaml
#   4. pecahan posisi untuk trial -> config/local.yaml
#   5. preflight (tanpa order): tanggal, saldo terbaca, pasar, sizing, jam, dukungan stop
# Tidak mengaktifkan live: live.enabled tetap false sampai live-start meminta "SAYA SIAP".
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
FLAG=--i-know-what-im-doing
echo "== tradebot live-setup"
echo "Kunci hanya ditulis ke .env di $REPO dan tidak pernah ditampilkan atau dicatat."
echo "Buat kunci baru khusus bot di Tokocrypto; kunci yang pernah ditempel ke chat atau"
echo "dokumen apa pun harus dihapus di Tokocrypto, bukan dipakai di sini."
echo
pid_file=state/live_supervisor.pid
if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
  echo "bot live sedang jalan (pid $(cat "$pid_file")); jalankan live-stop dulu" >&2
  exit 2
fi
if [ -f .env ] && grep -qsE '^TOKOCRYPTO_API_KEY=.+' .env; then
  read -r -p "Kunci Tokocrypto sudah ada di .env. Ganti dengan yang baru? (ketik YA, Enter = pakai yang ada): " ganti
else
  ganti=YA
fi
if [ "${ganti:-}" = "YA" ]; then
  IFS= read -r -s -p "API key Tokocrypto (ketikan tidak ditampilkan, lalu Enter): " KEY
  echo
  IFS= read -r -s -p "API secret Tokocrypto (ketikan tidak ditampilkan, lalu Enter): " SECRET
  echo
  printf '%s\n%s\n' "$KEY" "$SECRET" | scripts/write-env.sh live
  unset KEY SECRET
else
  # kunci tetap; pastikan modenya live
  grep -qsE '^TRADING_MODE=live$' .env || {
    echo "TRADING_MODE di .env bukan live; jalankan lagi dan ketik YA untuk menulis ulang .env" >&2
    exit 2
  }
fi
echo
echo "== check-config (kunci tersamar)"
uv run tradebot check-config $FLAG | grep -E '^(mode|venue|config lokal|credentials):'
echo
echo "== halaman API Management Tokocrypto untuk kunci yang dipakai bot"
echo "Buka halaman itu sekarang dan periksa. Jawab dengan mengetik YA persis; jawaban lain membatalkan."
read -r -p "1. Izin withdrawal untuk kunci ini MATI? " a1
[ "$a1" = "YA" ] || { echo "dibatalkan: withdrawal harus mati sebelum lanjut" >&2; exit 2; }
read -r -p "2. Izin hanya spot trading (tanpa margin/futures)? " a2
[ "$a2" = "YA" ] || { echo "dibatalkan: batasi izin ke spot trading" >&2; exit 2; }
read -r -p "3. Pembatasan IP sudah diisi IP mesin ini, ATAU halaman itu tidak menyediakannya dan Anda menerima risikonya? " a3
[ "$a3" = "YA" ] || { echo "dibatalkan" >&2; exit 2; }
TODAY="$(date -u +%Y-%m-%d)"
uv run tradebot local-set "live.api_key_verified_date=$TODAY" live.enabled=false $FLAG
echo
read -r -p "Pecahan equity per posisi untuk trial (Enter = 0.25, batas maksimum config): " frac
frac="${frac:-0.25}"
uv run tradebot local-set "risk.position_fraction=$frac" $FLAG
echo
echo "== preflight (tidak mengirim order)"
if uv run tradebot preflight $FLAG; then
  echo
  echo "Preflight lulus. Langkah berikutnya: live-start.command (akan minta ketik SAYA SIAP)."
else
  code=$?
  echo
  echo "Preflight gagal (exit $code). Perbaiki yang ditandai, lalu jalankan preflight.command lagi." >&2
  echo "Saldo USDT kurang? Beli USDT dengan IDR di aplikasi Tokocrypto (Spot, pasangan USDT/IDR) dulu." >&2
  exit "$code"
fi
