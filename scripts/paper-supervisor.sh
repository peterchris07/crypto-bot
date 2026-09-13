#!/bin/bash
# Kompatibilitas: plist lama memanggil skrip ini. Isinya sekarang bot-supervisor.sh paper.
exec "$(dirname "$0")/bot-supervisor.sh" paper
