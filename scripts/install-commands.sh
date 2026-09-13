#!/bin/bash
# Pasang file .command (untuk dijalankan dari Finder) di root repo. Root .command dikecualikan
# dari git lewat info/exclude (bukan .gitignore) dan tidak pernah di-commit.
#
# Perintah yang membaca catatan per mode (status, checklist, compare, preflight, live-size)
# menambahkan flag --i-know-what-im-doing sendiri kalau .env berisi TRADING_MODE=live:
# aturan dua kunci tetap berlaku, tanpa pernah membaca nilai kunci.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
cd "$REPO"
EXCLUDE="$(git rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$EXCLUDE")"
grep -qx '\*.command' "$EXCLUDE" 2>/dev/null || echo '*.command' >> "$EXCLUDE"
make_command() {
  local name="$1" body="$2" needs_flag="${3:-no}" target="$REPO/$1.command" flag_block=""
  if [ -f "$target" ] && [ "$FORCE" -ne 1 ]; then
    echo "lewati $target (sudah ada; pakai --force untuk menimpa)"
    return
  fi
  if [ "$needs_flag" = "flag" ]; then
    flag_block='FLAG=""
if grep -qsE "^TRADING_MODE=live$" .env; then FLAG="--i-know-what-im-doing"; fi'
  fi
  cat > "$target" <<CMD
#!/bin/bash
cd "\$(dirname "\$0")"
export PATH="\$HOME/.local/bin:\$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
echo "== tradebot: $name"
$flag_block
$body
code=\$?
echo
echo "selesai dengan exit code \$code. Tekan tombol apa saja untuk menutup."
read -n 1 -s
CMD
  chmod +x "$target"
  echo "dipasang $target"
}
make_command "status"          'uv run tradebot status $FLAG' flag
make_command "preflight"       'uv run tradebot preflight $FLAG' flag
make_command "paper-checklist" 'uv run tradebot paper-checklist $FLAG' flag
make_command "compare-paper"   'uv run tradebot compare-paper $FLAG' flag
make_command "live-size"       'uv run tradebot live-size $FLAG' flag
make_command "paper-start"     'scripts/paper-start.sh'
make_command "paper-stop"      'scripts/paper-stop.sh'
make_command "live-setup"      'scripts/live-setup.sh'
make_command "live-start"      'scripts/live-start.sh'
make_command "live-stop"       'scripts/live-stop.sh'
make_command "backtest"        'uv run tradebot backtest && echo && uv run tradebot backtest --stress'
make_command "fetch-data"      'uv run tradebot fetch-data'
make_command "tests"           'uv run pytest'
echo "selesai. Buka file .command dari Finder; kalau macOS menolak, klik kanan > Open sekali."
