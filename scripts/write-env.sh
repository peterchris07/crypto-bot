#!/bin/bash
# Tulis kunci Tokocrypto dan TRADING_MODE ke .env di root repo, satu-satunya tempat kunci
# boleh berada. Nilai dibaca dari stdin (baris 1: API key, baris 2: API secret) supaya tidak
# pernah muncul di argumen proses, di layar, atau di log. Baris .env lain dipertahankan.
#
# Pemakaian: printf '%s\n%s\n' "$KEY" "$SECRET" | scripts/write-env.sh live
set -eu
MODE="${1:?mode: live}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
IFS= read -r KEY || KEY=""
IFS= read -r SECRET || SECRET=""
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
  grep -vE '^(TRADING_MODE|TOKOCRYPTO_API_KEY|TOKOCRYPTO_API_SECRET)=' .env > "$TMP" || true
fi
{
  printf 'TRADING_MODE=%s\n' "$MODE"
  printf 'TOKOCRYPTO_API_KEY=%s\n' "$KEY"
  printf 'TOKOCRYPTO_API_SECRET=%s\n' "$SECRET"
} >> "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" .env
trap - EXIT
echo ".env diperbarui (TRADING_MODE=$MODE, kunci Tokocrypto terpasang; nilainya tidak ditampilkan)"
