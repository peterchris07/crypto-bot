# File .command untuk Finder

Pemilik menjalankan bot dari Finder, bukan terminal. File `.command` di root repo
dikecualikan lewat `info/exclude` git dan TIDAK di-commit; yang di-commit adalah
pemasangnya. Pasang atau perbarui dengan:

```bash
scripts/install-commands.sh
```

Pemasang tidak menimpa file yang sudah ada kecuali diberi `--force`. Setiap
`.command` pindah ke folder repo, menjalankan satu perintah lewat `uv run`,
lalu menunggu tombol ditekan supaya jendelanya tidak langsung tertutup.

| File | Isi |
| --- | --- |
| status | `tradebot status`: proses, posisi, saldo, trade, ledger, checklist, compare |
| preflight | `tradebot preflight`: kesiapan live tanpa order |
| paper-start / paper-stop | LaunchAgent `com.tradebot.paper` |
| paper-checklist | empat butir bukti paper run |
| compare-paper | fill paper vs backtest, bias bertanda |
| live-setup | kunci ke .env tanpa ditampilkan, pertanyaan API Management, preflight |
| live-start | preflight, ketik SAYA SIAP, `live.enabled=true`, LaunchAgent `com.tradebot.live` |
| live-stop | STOP, lepas agent, `live.enabled=false` |
| live-size | penanda ukuran order live (minimum atau normal) |
| pipe-test | uji pipa sekali jalan dengan uang asli ukuran minimum: preflight, ketik UJI PIPA, beli, stop lapis 2, tunggu, batal, jual, flat, ledger |
| check-exchange / ledger-status | cek koneksi dan ledger per mode |
| backtest | backtest lalu backtest --stress |
| fetch-data | unduh OHLCV ke cache |
| tests | `uv run pytest` |

Perintah yang membaca catatan per mode (status, preflight, paper-checklist,
compare-paper, live-size) menambahkan flag `--i-know-what-im-doing` sendiri kalau
`scripts/env-mode.sh` membaca `TRADING_MODE=live` dari .env, dengan toleransi yang
sama seperti bot (tanda kutip, spasi, huruf besar, awalan export); yang dibaca
hanya baris itu, bukan nilai kunci. Tanpa baris itu, semuanya berjalan sebagai
paper.
