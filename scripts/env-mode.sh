#!/bin/bash
# Cetak TRADING_MODE dari .env di root repo, dengan toleransi yang sama seperti bot
# (python-dotenv + resolve_mode): tanda kutip, spasi, huruf besar, awalan export, CRLF.
# Tanpa .env atau tanpa baris itu: paper. Tidak pernah membaca atau mencetak nilai kunci.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mode=paper
if [ -f "$REPO/.env" ]; then
  line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?TRADING_MODE[[:space:]]*=' "$REPO/.env" | tail -n 1 | tr -d '\r')"
  if [ -n "$line" ]; then
    value="${line#*=}"
    value="$(printf '%s' "$value" | sed -e "s/^[[:space:]]*//" -e "s/[[:space:]]*$//" -e "s/^[\"']//" -e "s/[\"']$//" | tr '[:upper:]' '[:lower:]')"
    [ -n "$value" ] && mode="$value"
  fi
fi
printf '%s\n' "$mode"
