#!/bin/bash
# Pasang file .command (untuk dijalankan dari Finder) di root repo dari template di
# scripts/commands/. Root .command dikecualikan dari git lewat .git/info/exclude.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
cd "$REPO"
EXCLUDE="$(git rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$EXCLUDE")"
grep -qx '\*.command' "$EXCLUDE" 2>/dev/null || echo '*.command' >> "$EXCLUDE"
make_command() {
  local name="$1" body="$2" target="$REPO/$1.command"
  if [ -f "$target" ] && [ "$FORCE" -ne 1 ]; then
    echo "lewati $target (sudah ada; pakai --force untuk menimpa)"
    return
  fi
  cat > "$target" <<CMD
#!/bin/bash
cd "\$(dirname "\$0")"
export PATH="\$HOME/.local/bin:\$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
echo "== tradebot: $name"
$body
code=\$?
echo
echo "selesai dengan exit code \$code. Tekan tombol apa saja untuk menutup."
read -n 1 -s
CMD
  chmod +x "$target"
  echo "dipasang $target"
}
make_command "status"          'uv run tradebot status'
make_command "preflight"       'uv run tradebot preflight'
make_command "paper-start"     'scripts/paper-start.sh'
make_command "paper-stop"      'scripts/paper-stop.sh'
make_command "paper-checklist" 'uv run tradebot paper-checklist'
make_command "compare-paper"   'uv run tradebot compare-paper'
make_command "backtest"        'uv run tradebot backtest && echo && uv run tradebot backtest --stress'
make_command "fetch-data"      'uv run tradebot fetch-data'
make_command "tests"           'uv run pytest'
make_command "live-size"       'uv run tradebot live-size'
echo "selesai. Buka file .command dari Finder; kalau macOS menolak, klik kanan > Open sekali."
