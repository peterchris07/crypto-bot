# SIAP-PAKAI: keadaan bot dan apa yang menunggu Anda

Ditulis 2026-09-12 setelah Langkah A sampai D dari prompt final selesai. Dokumen
ini memberi tahu Anda apa yang sudah ada, apa yang belum, dan apa yang hanya
bisa Anda kerjakan. Mode live TIDAK aktif. Tidak ada order asli yang pernah
dikirim dari kode ini.

## 1. Keadaan sekarang

Tahap 1 sampai 8 selesai secara kode, dengan catatan tahap 8 (mode live) ditulis
dan diuji dengan klien palsu tetapi belum pernah dijalankan ke Tokocrypto asli
dan masih terkunci: `live.enabled` di config bernilai false, dan perintah
`tradebot run` mode live menolak jalan sampai nilai itu diubah DAN preflight
lulus DAN dua kunci lain (TRADING_MODE=live di .env dan flag
`--i-know-what-im-doing`) diberikan.

Hasil `uv run pytest` terakhir, di mesin build (tanpa kunci, tanpa jaringan ke
Tokocrypto):

| Kategori | Jumlah | Alasan |
| --- | --- | --- |
| Lulus | 435 | test unit dan integrasi dengan klien palsu |
| Dilewati karena kunci testnet Binance | 8 | BINANCE_TESTNET_API_KEY dan BINANCE_TESTNET_API_SECRET tidak ada di .env |
| Dilewati karena kunci Tokocrypto | 1 | TOKOCRYPTO_API_KEY dan TOKOCRYPTO_API_SECRET tidak ada di .env; test ini juga mendokumentasikan celah startTime dan diharapkan GAGAL pada akun beriwayat panjang selama celah itu belum ditutup |
| Gagal dijalankan karena jaringan | 8 | www.tokocrypto.com tidak terjangkau dari mesin build; di Mac Anda test ini jalan dan pada run terakhir yang Anda laporkan semuanya hijau setelah perbaikan fixture |

Pytest menampilkan ketiga kategori itu terpisah di bagian merah sebelum ringkasan
akhir, karena baris terakhir pytest sendiri menggabungkan semuanya sebagai
"skipped". Test yang dilewati atau gagal dijalankan BUKAN test yang lulus;
angka 435 tidak mencakupnya. Di Mac Anda dengan jaringan, angka yang diharapkan
adalah 443 lulus dan 9 dilewati karena kunci.

Cabang: `main` sudah berisi semua pekerjaan (fast-forward ke commit yang sama
dengan `claude/elegant-pascal-x4swez`). Cabang `tahap-2-tokocrypto` sudah
tercakup penuh oleh main tetapi penghapusannya di origin tidak bisa dilakukan
dari lingkungan build; lihat bagian 2.

## 2. Hal yang hanya bisa Anda lakukan

Urutannya penting. Jangan lompat ke kunci live sebelum semua di atasnya OK.

### 2a. Hapus cabang lama di origin

Dari Terminal di folder repo (sekali saja):

```bash
git fetch origin
git branch --merged origin/main
git push origin --delete tahap-2-tokocrypto
```

Baris kedua memastikan cabang itu tercakup main sebelum dihapus.

### 2b. Kunci testnet Binance, untuk menghijaukan 8 test

1. Buka https://testnet.binance.vision, masuk dengan akun GitHub, buat HMAC key.
2. Tulis ke `.env` di root repo (file ini tidak pernah di-commit):

   ```
   BINANCE_TESTNET_API_KEY=...
   BINANCE_TESTNET_API_SECRET=...
   ```

3. Jalankan `tests.command` dari Finder (atau `uv run pytest`). Bagian
   "dilewati karena kunci" harus tinggal 1 (Tokocrypto), dan test
   `tests/test_testnet_integration.py` termasuk satu putaran order sungguhan di
   testnet harus hijau. Kalau salah satu merah, laporkan outputnya; jangan
   lanjut ke langkah berikutnya.

### 2c. Paper run sampai keempat butir checklist OK

Butuh hari, bukan menit. Bot paper membaca harga asli Tokocrypto dan
mensimulasikan akun.

1. `fetch-data.command`, lalu `paper-start.command`. Bot berjalan lewat launchd
   dan supervisor; `status.command` menunjukkan proses, restart supervisor, dan
   ringkasan lainnya kapan saja.
2. Biarkan berjalan sampai `paper-checklist.command` menyatakan keempat butir
   OK:
   - restart di tengah posisi tanpa order ganda (matikan dengan
     `paper-stop.command` saat posisi terbuka, lalu `paper-start.command`);
   - gangguan jaringan yang ditangani dan pulih (cabut Wi-Fi beberapa menit
     saat bot jalan);
   - kill switch berhenti dengan exit code 6 (buat file `STOP` di root repo,
     atau biarkan batas rugi harian tercapai);
   - minimal `live.checklist_min_fills` (4) fill yang bisa ditelusuri dari
     jurnal ke ledger.
3. Setelah itu `compare-paper.command`. Kalau keluar "BIAS SATU ARAH
   TERDETEKSI" (exit code 7) pada fill bersinyal, asumsi `costs.slippage_rate`
   terlalu longgar dan backtest terlalu optimis; jangan lanjut sebelum angkanya
   diperbaiki dan backtest diulang.

### 2d. Periksa halaman API Management Tokocrypto

Untuk kunci yang akan dipakai bot (buat kunci baru khusus bot):

1. Izin withdrawal MATI. Ini yang paling penting; bot tidak pernah dan tidak
   boleh bisa menarik dana.
2. Pembatasan IP aktif kalau halaman itu menyediakannya, diisi IP mesin bot.
   Kalau IP rumah berubah-ubah, catat bahwa pembatasan ini tidak tersedia
   untuk Anda dan terima risikonya secara sadar.
3. Izin trading spot saja; tidak ada margin atau futures.
4. Catat tanggal hari ini di `config/default.yaml` pada
   `live.api_key_verified_date` dengan format YYYY-MM-DD. Preflight menolak
   nilai kosong atau lebih tua dari `live.max_key_age_days` (90 hari); ulangi
   pemeriksaan ini setiap 90 hari.

### 2e. Kunci live Tokocrypto, hanya setelah 2b sampai 2d OK

1. Tulis ke `.env`:

   ```
   TOKOCRYPTO_API_KEY=...
   TOKOCRYPTO_API_SECRET=...
   ```

   Jangan tulis nilainya di tempat lain mana pun, termasuk chat.
2. Jalankan `tests.command`. Test Tokocrypto berkunci sekarang jalan; pada
   akun baru dengan riwayat pendek test itu hijau, pada akun beriwayat panjang
   ia gagal karena celah startTime (bagian 3) dan itu informasi, bukan alasan
   melonggarkan test.
3. Jalankan `preflight.command`. Semua baris harus OK: tanggal verifikasi
   kunci, kunci bisa membaca saldo (hanya itu yang dicoba; withdrawal tidak
   pernah dicoba), pasangan ada di load_markets, minimum notional terbaca dan
   ukuran sizing di atasnya, dukungan stop order di venue, dan time drift.
   Preflight tidak mengirim order.

### 2f. Menyatakan siap mengaktifkan live

Ini keputusan Anda dan tidak diambil oleh kode. Kalau semua di atas OK:

1. Isi saldo USDT di Tokocrypto secukupnya untuk minimum notional ditambah
   ruang fee, bukan seluruh modal.
2. Ubah `live.enabled: true` di config, pastikan `TRADING_MODE=live` di .env,
   lalu jalankan dari Terminal:

   ```bash
   uv run tradebot run --i-know-what-im-doing
   ```

3. Order pertama dipaksa ke ukuran minimum exchange, bukan hasil sizing.
   Setelah minimal `live.min_cycles_before_normal` (3) siklus masuk dan keluar
   benar, `status.command` menunjukkan siklus itu; naikkan ke ukuran normal
   secara sadar dengan `uv run tradebot live-size --normal`
   (`live-size.command` hanya menampilkan tahap saat ini). Kembalikan dengan
   `--minimum` kapan saja.
4. Ledger harus tanpa baris pending sebelum bot menyatakan lengkap; perintah
   `tradebot ledger-status` keluar dengan exit code 4 selama ada yang pending.

## 3. Celah yang diketahui

Masing-masing menyebut apa yang membuatnya muncul.

- **Rekonsiliasi order tanpa startTime.** Pencarian order lewat client order
  id di Tokocrypto memindai open orders lalu 200 order terbaru dari riwayat,
  tanpa startTime. Muncul kalau bot mati beberapa jam setelah mengirim order
  yang jawabannya hilang, sementara akun terus bertransaksi sehingga order itu
  bukan lagi salah satu dari 200 terbaru; rekonsiliasi lalu menyimpulkan order
  tidak pernah masuk, padahal jurnal write-ahead ada untuk mencegah kesimpulan
  itu. Urutan hasil endpoint riwayat (naik atau turun) belum terverifikasi
  dengan kunci asli. Penutupnya: meneruskan startTime dari waktu niat di
  jurnal; belum dikerjakan. Mitigasi sementara: jangan memakai akun bot untuk
  transaksi manual.
- **Backtest tanpa batas pasar.** Backtest dari CLI tidak membulatkan jumlah ke
  step exchange dan tidak mengecek minimum notional; runner paper dan live
  melakukannya. Muncul sebagai selisih kecil jumlah antara backtest dan paper.
- **Stop lapis 1 diisi di harga terlihat.** Paper dan live mengisi stop pada
  harga yang terlihat saat dicek, backtest di level stop. Selisihnya selalu ke
  arah merugikan dan terlihat di compare-paper; bukan bug, tapi bias yang harus
  diperhitungkan.
- **Lapis 2 tidak ada di paper.** PaperAdapter tidak mensimulasikan stop order
  di exchange, jadi jalur lapis 2 (pasang setelah masuk, batalkan sebelum
  keluar, serap eksekusi saat bot bangun) hanya teruji dengan klien palsu dan
  baru akan berjalan sungguhan di testnet dan live. Kalau venue tidak
  melaporkan dukungan stop order, mode live gagal keras.
- **Data historis sejak September 2025.** Menarik lebih jauh menabrak jendela
  pemeliharaan lebih panjang dari `data.max_gap_bars` dan berhenti; daftar
  downtime terkonfirmasi belum ada. Akibatnya backtest hanya mencakup satu
  rezim pasar.
- **Laptop tidur.** Menutup tutup MacBook membuat macOS tidur walau caffeinate
  jalan. Bot melanjutkan setelah bangun, bar yang terlewat dilewati sebagai
  stale, dan kill switch gagal koneksi bisa menyala kalau jaringan lambat
  pulih. Stop lapis 2 di exchange adalah jaring untuk kondisi ini, dan hanya
  ada di testnet dan live.
- **Test yang bergantung lingkungan.** 8 test testnet, 1 test Tokocrypto
  berkunci, dan 8 test network hanya jalan di mesin dengan kunci dan jaringan
  yang sesuai. Ringkasan pytest menyebutnya terpisah; jangan membaca baris
  "skipped" pytest sebagai lulus.
- **Time drift diukur, bukan dijamin.** Pengukuran memakai satu panggilan
  pemanasan lalu 5 sampel dengan rtt terkecil. Kalau rtt terbaik masih terlalu
  besar untuk memutuskan terhadap batas 1000 ms, bot melaporkan pengukuran
  tidak konklusif, bukan jam melenceng, dan hanya melanjutkan kalau selisih
  ditambah setengah rtt masih di bawah recv_window 5000 ms. Jaringan yang
  sangat lambat karena itu bisa menghentikan start dengan alasan pengukuran.

## 4. Yang belum dibuktikan sistem ini

Tidak ada apa pun di repo ini yang membuktikan strategi EMA crossover 20/50
pada BTC/USDT 1 jam punya ekspektansi positif setelah biaya. Biaya all-in di
Tokocrypto adalah 0,4044 persen per sisi (taker 0,15 persen, PPh 22 final 0,21
persen, biaya bursa 0,0444 persen) ditambah asumsi slippage 0,15 persen, jadi
sekitar 1,11 persen per putaran masuk-keluar; angka fee-nya realistis, bukan
margin aman, dan compare-paper bisa menunjukkan slippage aslinya lebih buruk.
Setiap putaran harus menghasilkan lebih dari 1,11 persen hanya untuk impas,
sebelum dibandingkan dengan buy-and-hold yang membayar biaya itu sekali.
Konsekuensinya, strategi dengan banyak sinyal mati oleh biaya: seratus putaran
setahun berarti lebih dari 100 persen biaya, dan crossover di pasar mendatar
menghasilkan justru itu. Backtest yang ada mencakup kurang dari satu tahun
data, satu rezim pasar, tanpa holdout yang tak tersentuh, dan RESEARCH.md
menetapkan bahwa hipotesis yang gagal di holdout selesai tanpa putaran kedua.
Semua infrastruktur di sini (jurnal, ledger, kill switch, stop dua lapis,
preflight, ukuran minimum) ada untuk membatasi kerugian dari kesalahan
operasional; tidak satu pun membuat strateginya menguntungkan. Sebelum uang
asli masuk, keputusan yang jujur adalah menganggap ekspektansinya nol atau
negatif sampai backtest --stress dan paper run beberapa minggu menunjukkan
sebaliknya.
