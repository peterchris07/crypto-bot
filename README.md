# tradebot

Bot trading crypto spot Binance untuk belajar engineering sistem yang bisa diuji. Tiga mode: paper (default, tanpa kunci API, harga mainnet asli, order disimulasikan), testnet (Binance Spot Testnet, uang palsu), dan live (Binance Spot mainnet, uang asli). Spesifikasi lengkap dan semua keputusan desain ada di SPEC.md. File ini hanya menjelaskan cara memasang dan menjalankan.

Strategi awal diasumsikan rugi. Sistem ini dirancang supaya kerugian itu murah dan terukur, bukan supaya cepat untung. Hasil backtest tidak pernah menjadi dasar untuk menyebut strategi ini menguntungkan.

## Aturan keselamatan yang ditegakkan kode

Mode default adalah paper. Mode live hanya aktif kalau TRADING_MODE=live ada di .env dan flag --i-know-what-im-doing diberikan di command line. Salah satu saja, bot menolak jalan dan menyebut syarat mana yang kurang.

Kunci API hanya dibaca dari file .env lewat python-dotenv. .env ada di .gitignore sejak commit pertama, dan ada test yang memastikan git mengabaikannya. Setiap baris log melewati penyamar yang mengganti kunci dengan tanda bintang.

Kill switch (rugi harian, runaway order, gagal koneksi beruntun, file STOP) ada sebelum order pertama bisa dikirim. Detail per pemicu ada di SPEC.md dan di config/default.yaml bagian risk.

## Membuat API key Binance

Bot ini tidak pernah boleh punya izin withdrawal. Saat membuat kunci di Binance, aktifkan hanya Enable Reading dan Enable Spot & Margin Trading. Jangan aktifkan Enable Withdrawals, jangan aktifkan Futures, jangan aktifkan Universal Transfer. Batasi akses ke IP tepercaya saja dan masukkan IP publik mesin yang menjalankan bot. Tanpa IP whitelist, Binance sendiri mencabut izin trading kunci itu dalam 90 hari, dan yang lebih penting, kunci yang bocor bisa dipakai dari mana saja.

Untuk testnet, buat kunci di https://testnet.binance.vision dengan login GitHub. Kunci testnet dan kunci mainnet adalah dua pasang yang berbeda dan disimpan di variabel yang berbeda.

Di tahap 8 bot menambah pemeriksaan sebelum order pertama: memanggil endpoint api restrictions Binance dan menolak jalan kalau izin withdrawal ternyata aktif.

## Pemasangan

Butuh Python 3.11 atau lebih baru. Project ini memakai uv supaya Python dan dependensi terpasang di dalam folder project tanpa mengubah Python sistem.

```bash
brew install uv
```

```bash
uv python install 3.12 && uv venv --python 3.12 && uv sync
```

Salin contoh environment, lalu isi nilainya sendiri. Jangan pernah menempel kunci ke chat, ke commit, atau ke file selain .env.

```bash
cp .env.example .env
```

## Menjalankan

Cek konfigurasi tanpa menyentuh exchange. Output menampilkan mode yang terpilih dan semua parameter, dengan kunci tersamar.

```bash
uv run tradebot check-config
```

Cek koneksi ke exchange sesuai mode. Perintah ini membandingkan jam lokal dengan jam server, memuat batas pasar, mengambil harga dan tiga bar terakhir, dan di mode testnet atau live juga menampilkan saldo dan order terbuka. Perintah ini tidak pernah mengirim order. Di mode paper tidak butuh kunci sama sekali.

```bash
uv run tradebot check-exchange
```

Untuk testnet, isi TRADING_MODE=testnet dan kunci testnet di .env, lalu jalankan perintah yang sama. Kalau jam mesin melenceng lebih dari batas di config, perintah gagal dengan pesan yang menyebut selisihnya dalam milidetik; sinkronkan jam sistem lalu ulangi.

Perintah fetch-data, backtest, backtest --stress, dan run ditambahkan di tahap berikutnya sesuai urutan di SPEC.md.

## Menjalankan test

```bash
uv run pytest
```

Test yang butuh kunci testnet ditandai testnet. Kalau kunci belum ada di .env, test itu dilewati, dan di akhir run selalu tercetak ringkasan berapa yang dilewati dan alasannya. Hasil hijau tanpa membaca ringkasan itu tidak berarti semuanya teruji.

## Biaya di backtest

Default di config/default.yaml adalah fee taker 0,1 persen dan slippage 0,1 persen per sisi. Fee 0,1 persen adalah tarif taker standar Binance spot tanpa diskon BNB, jadi ini angka realistis, bukan margin aman. Slippage 0,1 persen untuk BTC/USDT ukuran retail sudah konservatif. Yang pesimistis adalah perintah backtest --stress, yang menggandakan keduanya. Kalau strategi hanya terlihat baik di angka default dan runtuh di --stress, itu informasi penting, bukan gangguan.

## Jaringan: api.binance.com diblokir ISP

Pada jaringan tempat project ini dibangun (Biznet, 11 September 2026), nama api.binance.com dijawab oleh DNS ISP dengan alamat halaman blokir (rpz.biznet), dan sertifikatnya tidak cocok sehingga koneksi TLS gagal. Mengganti resolver ke 1.1.1.1 tidak menolong karena permintaan DNS di port 53 ikut dibelokkan. Dua alamat lain tidak diblokir: testnet.binance.vision untuk Spot Testnet dan data-api.binance.vision, endpoint data pasar publik resmi Binance yang melayani data mainnet yang sama tanpa endpoint akun.

Karena itu adapter tanpa kunci (mode paper dan pengunduh data historis) mengambil data publik lewat data-api.binance.vision, diatur di config lewat exchange.public_market_data_url. Adapter berkunci tidak diarahkan ke sana karena endpoint akun dan order tidak ada di alamat itu. Konsekuensinya untuk tahap 8: mode live butuh api.binance.com yang bisa dijangkau, dan cara mencapainya, termasuk sisi kepatuhannya, adalah keputusan di luar kode ini. Jalankan check-exchange dengan TRADING_MODE=live sebelum tahap 8 dimulai untuk memastikan jalurnya ada.

## Struktur folder saat runtime

Folder data, logs, state, dan trades dibuat otomatis dan tidak masuk git. Folder state berisi jurnal order (ditulis sebelum order dikirim) dan state harian risk manager. Folder trades berisi CSV yang mencatat setiap trade untuk pelaporan pajak; file ini hanya ditambah, tidak pernah ditimpa. Simpan cadangannya di luar mesin ini.

File bernama STOP di root project adalah kill switch manual. Membuat file itu menghentikan bot pada iterasi berikutnya.

## Status tahap

Tahap 1 selesai: setup project, config loader, logging, penanganan .env, gitignore.

Tahap 2 selesai: interface ExchangeAdapter dan CcxtAdapter untuk testnet dan mainnet. Adapter mencoba ulang gangguan jaringan dengan backoff dari config, tidak pernah mencoba ulang error autentikasi atau saldo, tidak pernah mengirim ulang order yang jawabannya hilang, dan menolak membuat klien mainnet berkunci di luar jalur mode live. Test integrasi testnet ada di tests/test_testnet_integration.py dan dilewati dengan ringkasan kalau kunci belum ada.

Tahap 3 sampai 8 menyusul berurutan, masing-masing dengan test yang lulus sebelum tahap berikutnya dimulai.
