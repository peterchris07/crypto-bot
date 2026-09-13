#!/bin/bash
# Tulis kunci Tokocrypto dan TRADING_MODE ke .env di root repo, satu-satunya tempat kunci
# boleh berada. Nilai dibaca dari stdin (baris 1: API key, baris 2: API secret) supaya tidak
# pernah muncul di argumen proses, di layar, atau di log. Baris .env lain dipertahankan.
#
# Pemakaian: printf '%s\n%s\n' "$KEY" "$SECRET" | scripts/write-env.sh live
set +o xtrace +o verbose  # bash -x atau SHELLOPTS=xtrace dari luar tidak boleh mencetak nilai
set -eu
MODE="${1:?mode: live}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# `|| [ -n ... ]` menjaga baris terakhir tanpa newline: read mengembalikan 1 di EOF walau
# variabelnya sudah terisi.
IFS= read -r KEY || [ -n "${KEY:-}" ] || KEY=""
IFS= read -r SECRET || [ -n "${SECRET:-}" ] || SECRET=""
KEY="${KEY%$'\r'}"
SECRET="${SECRET%$'\r'}"
if [ -z "$KEY" ] || [ -z "$SECRET" ]; then
  echo "API key dan secret tidak boleh kosong; .env tidak diubah" >&2
  exit 2
fi
case "$KEY$SECRET" in
  *[[:space:]]*) echo "API key atau secret mengandung spasi; .env tidak diubah" >&2; exit 2 ;;
esac
umask 077
TMP="$(mktemp "$REPO/.env.tmp.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
if [ -f .env ]; then
  grep -vE '^[[:space:]]*(export[[:space:]]+)?(TRADING_MODE|TOKOCRYPTO_API_KEY|TOKOCRYPTO_API_SECRET)[[:space:]]*=' .env > "$TMP" || true
fi
{
  printf 'TRADING_MODE=%s\n' "$MODE"
  printf 'TOKOCRYPTO_API_KEY=%s\n' "$KEY"
  printf 'TOKOCRYPTO_API_SECRET=%s\n' "$SECRET"
} >> "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" .env
trap - EXIT
unset KEY SECRET
echo ".env diperbarui (TRADING_MODE=$MODE, kunci Tokocrypto terpasang, izin 600; nilainya tidak ditampilkan)"
