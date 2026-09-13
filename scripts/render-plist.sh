#!/bin/bash
# Cetak plist LaunchAgent untuk satu mode ke stdout dari scripts/com.tradebot.plist.template.
#   render-plist.sh <paper|live> <repo> <PATH>
# Nilai dimasukkan lewat penggantian string bash (bukan sed) dan di-escape untuk XML,
# jadi path repo yang berisi '&', '#', '<', atau '>' tidak merusak plist secara diam-diam.
set -eu
# bash 5.2+ menafsirkan '&' di sisi pengganti ${x//a/b} sebagai teks yang cocok; matikan
# supaya '&amp;' hasil escape tidak berubah. bash 3.2 macOS tidak punya opsi ini: abaikan.
shopt -u patsub_replacement 2>/dev/null || true
MODE="${1:?mode: paper atau live}"
REPO="${2:?path repo}"
PATH_VALUE="${3:?PATH untuk launchd}"
case "$MODE" in
  paper|live) ;;
  *) echo "mode harus paper atau live, dapat '$MODE'" >&2; exit 2 ;;
esac
case "$REPO$PATH_VALUE" in
  *$'\n'*) echo "path tidak boleh berisi baris baru" >&2; exit 2 ;;
esac
xml_escape() {
  # sed, bukan ${x//a/b}: perilaku '&' di sisi pengganti berbeda antar versi bash.
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}
TEMPLATE="$(cd "$(dirname "$0")" && pwd)/com.tradebot.plist.template"
text="$(cat "$TEMPLATE")"
repo_x="$(xml_escape "$REPO")"
path_x="$(xml_escape "$PATH_VALUE")"
text="${text//__LABEL__/com.tradebot.$MODE}"
text="${text//__MODE__/$MODE}"
text="${text//__REPO__/$repo_x}"
text="${text//__PATH__/$path_x}"
printf '%s\n' "$text"
