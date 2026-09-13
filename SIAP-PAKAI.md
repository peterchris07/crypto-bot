# SIAP-PAKAI: keadaan bot dan apa yang menunggu Anda

Diperbarui 2026-09-13. Dokumen ini memberi tahu Anda apa yang sudah ada, apa yang
belum, dan apa yang hanya bisa Anda kerjakan. Mode live TIDAK aktif. Tidak ada
order asli yang pernah dikirim dari kode ini. Tidak ada nilai kunci di repo,
di dokumen ini, atau di file mana pun selain .env di Mac Anda.

## 0. Keputusan Anda: trial live kecil, bukan paper run

Anda memutuskan melewati paper run dan langsung mencoba di akun Tokocrypto asli
dengan modal sekitar dua juta rupiah. Kode sekarang menyediakan jalur itu dari
Finder (live-setup, live-start, live-stop). Dua konsekuensi yang harus Anda
terima secara sadar:

- Keempat butir checklist (restart di tengah posisi, gangguan jaringan, kill
  switch, trade tertelusuri) pertama kali teruji dengan uang sungguhan. Kerugian
  maksimum dibatasi saldo USDT yang Anda setor, bukan oleh kode.
- Kunci yang pernah Anda tempel ke chat dianggap bocor. Hapus kunci itu di
  Tokocrypto dan buat yang baru. Kunci saja tanpa secret memang tidak bisa
  menandatangani permintaan, tetapi tidak ada alasan memakainya lagi.

## 1. Keadaan sekarang

Tahap 1 sampai 8 selesai secara kode. Tahap 8 (mode live) diuji dengan klien
palsu, belum pernah dijalankan ke Tokocrypto asli, dan terkunci: `live.enabled`
false sampai `live-start.command` menuliskannya ke config/local.yaml setelah
preflight lulus dan Anda mengetik SAYA SIAP (perintah `tradebot local-set
live.enabled=true --i-know-what-im-doing` dari Terminal juga bisa, tanpa
pertanyaan; live-stop dan supervisor mengembalikannya ke false).

Hasil `uv run pytest` terakhir di mesin build (tanpa kunci, tanpa jaringan ke
Tokocrypto):

| Kategori | Jumlah | Alasan |
| --- | --- | --- |
| Lulus | 528 | test unit, integrasi dengan klien palsu, skrip shell dengan `uv`, `caffeinate`, dan `launchctl` palsu |
| Dilewati karena kunci testnet Binance | 8 | BINANCE_TESTNET_API_KEY dan BINANCE_TESTNET_API_SECRET tidak ada di .env |
| Dilewati karena kunci Tokocrypto | 1 | TOKOCRYPTO_API_KEY dan TOKOCRYPTO_API_SECRET tidak ada di .env; test ini mendokumentasikan celah startTime dan diharapkan GAGAL pada akun beriwayat panjang |
| Gagal dijalankan karena jaringan | 8 | www.tokocrypto.com tidak terjangkau dari mesin build (kebijakan jaringan sesi); di Mac Anda test ini jalan |

Test yang dilewati atau gagal dijalankan BUKAN test yang lulus; angka 528 tidak
mencakupnya. Di Mac Anda dengan jaringan, yang diharapkan 536 lulus dan 9
dilewati karena kunci. Setelah kunci Tokocrypto ada di .env, test berkunci itu
ikut jalan.

Yang baru sejak laporan sebelumnya:

- Catatan per mode: paper tetap di state/, trades/, logs/; live ke state/live/,
  trades/live/, logs/live/. Posisi atau jurnal simulasi tidak bisa terbaca
  sebagai uang asli.
- config/local.yaml (tidak di-commit) untuk live.enabled, tanggal verifikasi
  kunci, dan pecahan posisi trial, ditulis lewat `tradebot local-set` yang
  memvalidasi dan mengembalikan file kalau ditolak.
- Skrip live-setup, live-start, live-stop, supervisor per mode, dan file
  `.command` yang menambahkan flag live sendiri.

Cabang: `main` berisi semua pekerjaan. Cabang `tahap-2-tokocrypto` tercakup
penuh oleh main, tetapi penghapusannya di origin ditolak gateway git dari
lingkungan build; satu perintah dari Mac Anda (bagian 2a).

## 2. Hal yang hanya bisa Anda lakukan

Urutannya penting. Semuanya dari Finder kecuali 2a dan 2b.

### 2a. Hapus cabang lama di origin (sekali)

```bash
git fetch origin
git branch -r --merged origin/main
git push origin --delete tahap-2-tokocrypto
```

### 2b. Ambil kode terbaru dan pasang file .command

Di Terminal, di folder repo:

```bash
git pull origin main
scripts/install-commands.sh --force
```

`--force` perlu sekali ini: selain menambah live-setup.command,
live-start.command, dan live-stop.command, file status, preflight, checklist,
compare, dan live-size yang lama harus dibuat ulang supaya menambahkan flag
live sendiri setelah .env berisi TRADING_MODE=live.

Kalau paper run masih jalan dari sesi sebelumnya, hentikan dulu dengan
`paper-stop.command`; paper dan live tidak dijalankan bersamaan.

### 2c. Di aplikasi Tokocrypto

1. Hapus API key yang pernah ditempel ke chat.
2. Buat API key baru khusus bot: izin spot trading saja, withdrawal MATI,
   pembatasan IP diisi IP Mac Anda kalau halaman itu menyediakannya. Simpan key
   dan secret hanya untuk diketik ke live-setup; jangan ke chat, catatan, atau
   email.
3. Beli USDT dengan IDR sebesar modal trial di Spot, pasangan USDT/IDR (order
   market). Bot hanya memperdagangkan BTC/USDT dan tidak menyentuh IDR; dua juta
   rupiah menjadi sekitar seratus dua puluhan USDT tergantung kurs hari itu,
   dipotong biaya sekitar 0,4 persen sekali.

### 2d. `live-setup.command`

Jendela Terminal terbuka dan meminta:

1. API key, lalu secret. Ketikan tidak ditampilkan. Keduanya hanya ditulis ke
   .env (izin 600), tidak ke layar, log, atau argumen proses.
2. Tiga pertanyaan halaman API Management; jawab YA persis hanya kalau benar.
   Tanggal hari ini dicatat ke config/local.yaml sebagai
   live.api_key_verified_date; preflight menolak tanggal kosong atau lebih tua
   dari 90 hari.
3. Pecahan equity per posisi untuk trial; Enter berarti 0.25 (titik desimal;
   koma diubah otomatis), batas maksimum config. Dengan modal sekitar seratus
   dua puluh USDT, 0.10 menghasilkan sekitar dua belas USDT per posisi yang
   bisa jatuh di bawah minimum notional; 0.25 memberi ruang. Modal trial
   itulah batas kerugian, bukan pecahan ini.
4. Preflight tanpa order: tanggal verifikasi, kunci bisa membaca saldo (hanya
   itu; withdrawal tidak pernah dicoba), pasangan dan minimum notional, sizing
   dan ukuran minimum di atasnya, jam, dukungan stop order. Semua harus OK.
   Kalau saldo USDT kurang, kembali ke 2c.

### 2e. `live-start.command`

Preflight sekali lagi, data diunduh (kalau jaringan mati, berhenti di sini
sebelum apa pun diaktifkan), lalu diminta mengetik SAYA SIAP. Setelah itu
live.enabled menjadi true di config/local.yaml dan LaunchAgent
`com.tradebot.live` mulai menjalankan supervisor; kalau pemasangan agent
gagal, live.enabled dikembalikan ke false. Order pertama berukuran
minimum exchange, bukan hasil sizing. Stop lapis 1 di bot, lapis 2 di
exchange dengan jarak dua kali lipat.

### 2f. Selama trial

- `status.command` kapan saja: proses, restart supervisor, posisi, stop lapis
  2, tahap ukuran, trade, ledger, checklist, compare.
- `live-stop.command` untuk berhenti: file STOP, bot berhenti dengan exit 6,
  agent dilepas, live.enabled kembali false. Posisi yang dipegang tidak dijual
  otomatis; stop lapis 2 tetap di exchange.
- Setelah minimal 3 siklus masuk-keluar benar, `live-size.command` menunjukkan
  siklus itu. Naik ke ukuran normal hanya lewat Terminal:
  `uv run tradebot live-size --normal --i-know-what-im-doing`.
- Jangan pakai akun bot untuk transaksi manual selama bot jalan (celah
  startTime di bagian 3). Konversi IDR ke USDT sebelum live-start tidak
  masalah karena belum ada order bot yang menunggu jawaban.
- Kalau `ledger-status.command` menyebut baris pending, ledger belum lengkap;
  bot merekonsiliasinya saat start dan setelah tiap fill. Dari Terminal,
  setiap perintah yang membaca catatan live butuh flag:
  `uv run tradebot ledger-status --i-know-what-im-doing`.
- Jangan menjalankan `tradebot run` sendiri dari Terminal saat agent hidup;
  kunci file state/live/run.lock menolaknya supaya tidak ada dua bot pada bar
  yang sama.

### 2g. Kunci testnet Binance (opsional, untuk 8 test yang dilewati)

Buka https://testnet.binance.vision, masuk dengan GitHub, buat HMAC key, tulis
BINANCE_TESTNET_API_KEY dan BINANCE_TESTNET_API_SECRET ke .env (baris lain di
.env tidak diganggu), lalu `tests.command`. Ini menghijaukan test integrasi
testnet termasuk satu putaran order sungguhan di testnet. Tidak menjadi syarat
trial live karena Anda memilih menguji langsung di venue asli.

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
  jurnal; belum dikerjakan. Mitigasi: jangan memakai akun bot untuk transaksi
  manual selama bot jalan.
- **Trial live menggantikan paper run.** Keempat butir checklist belum pernah
  terlihat di venue asli; trial inilah ujinya, dengan uang sungguhan yang
  dibatasi saldo setoran. Muncul karena keputusan Anda melewati paper.
- **Konversi IDR manual.** Bot tidak punya jalur order USDT/IDR; jalur itu
  sengaja tidak ditulis supaya tidak ada order tanpa test di akun asli.
- **Backtest tanpa batas pasar.** Backtest dari CLI tidak membulatkan jumlah ke
  step exchange dan tidak mengecek minimum notional; runner paper dan live
  melakukannya. Muncul sebagai selisih kecil jumlah antara backtest dan paper.
- **Stop lapis 1 diisi di harga terlihat.** Paper dan live mengisi stop pada
  harga yang terlihat saat dicek, backtest di level stop. Selisihnya selalu ke
  arah merugikan dan terlihat di compare-paper; bukan bug, tapi bias yang harus
  diperhitungkan.
- **Lapis 2 belum pernah jalan di venue asli.** Jalur pasang-setelah-masuk,
  batalkan-sebelum-keluar, dan serap-eksekusi-saat-bangun hanya teruji dengan
  klien palsu. Kalau venue tidak melaporkan dukungan stop order, preflight dan
  mode live gagal keras; supervisor tidak memulai ulang setelah exit 9.
- **Data historis sejak September 2025.** Menarik lebih jauh menabrak jendela
  pemeliharaan lebih panjang dari `data.max_gap_bars` dan berhenti; daftar
  downtime terkonfirmasi belum ada. Backtest hanya mencakup satu rezim pasar.
- **Laptop tidur.** Menutup tutup MacBook membuat macOS tidur walau caffeinate
  jalan. Bot melanjutkan setelah bangun, bar yang terlewat dilewati sebagai
  stale, dan kill switch gagal koneksi bisa menyala kalau jaringan lambat
  pulih. Stop lapis 2 di exchange adalah jaring untuk kondisi ini.
- **Test yang bergantung lingkungan.** 8 test testnet, 1 test Tokocrypto
  berkunci, dan 8 test network hanya jalan di mesin dengan kunci dan jaringan
  yang sesuai. Ringkasan pytest menyebutnya terpisah; jangan membaca baris
  "skipped" pytest sebagai lulus.
- **Time drift diukur, bukan dijamin.** Satu panggilan pemanasan lalu minimal 5
  sampel; sampel dengan rtt di atas 300 ms dibuang, pengambilan diteruskan
  sampai 12 percobaan kalau belum ada yang cepat, dan yang dipakai sampel cepat
  dengan rtt terkecil. Tanpa satu pun sampel cepat, bot melaporkan pengukuran
  tidak konklusif (bukan jam melenceng, tidak pernah disebut "pengukuran
  valid") dan hanya melanjutkan kalau selisih ditambah setengah rtt masih di
  bawah recv_window 5000 ms. Log menyebut tiga estimator (awal kirim, titik
  tengah, akhir terima); pada hari pengukuran lambat Anda, estimator akhir
  terima yang stabil di sekitar -22 ms, tanda jedanya sebelum request sampai
  ke server.
- **Skrip macOS diuji di Linux dengan tiruan.** bash -n, penulisan .env,
  pemasang .command, dan logika supervisor diuji dengan `uv` dan `caffeinate`
  palsu; launchd sendiri dan perilaku Terminal saat `read -s` baru terlihat di
  Mac Anda.

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
menghasilkan justru itu. Dengan modal trial sekitar seratus dua puluh USDT dan
order minimum sekitar sepuluh USDT, satu putaran membayar sekitar sebelas sen
USDT; trial ini menguji operasi, bukan ekspektansi, karena beberapa minggu dan
belasan trade terlalu sedikit untuk membedakan edge dari keberuntungan.
Backtest yang ada mencakup kurang dari satu tahun data, satu rezim pasar,
tanpa holdout yang tak tersentuh, dan RESEARCH.md menetapkan bahwa hipotesis
yang gagal di holdout selesai tanpa putaran kedua. Semua infrastruktur di sini
(jurnal, ledger, kill switch, stop dua lapis, preflight, ukuran minimum) ada
untuk membatasi kerugian dari kesalahan operasional; tidak satu pun membuat
strateginya menguntungkan. Keputusan yang jujur: anggap ekspektansinya nol atau
negatif sampai backtest --stress dan catatan trade live berbulan-bulan
menunjukkan sebaliknya.
