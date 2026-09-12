# tradebot

Bot trading crypto spot untuk belajar engineering sistem yang bisa diuji. Tiga mode: paper (default, tanpa kunci API, harga asli Tokocrypto, order disimulasikan), testnet (Binance Spot Testnet, uang palsu, hanya untuk develop), dan live (Tokocrypto mainnet, uang asli). Spesifikasi lengkap dan semua keputusan desain ada di SPEC.md, protokol riset strategi ada di RESEARCH.md. File ini hanya menjelaskan cara memasang dan menjalankan.

Strategi awal diasumsikan rugi. Sistem ini dirancang supaya kerugian itu murah dan terukur, bukan supaya cepat untung. Hasil backtest tidak pernah menjadi dasar untuk menyebut strategi ini menguntungkan.

## Venue develop dan venue live berbeda

Develop dan test jalur order memakai Binance Spot Testnet. Eksekusi live memakai Tokocrypto. Venue live semula Binance mainnet, diganti karena api.binance.com diblokir ISP di level DNS pada jaringan tempat project ini dibangun; testnet Binance tidak diblokir. Tokocrypto tidak punya testnet, jadi satu-satunya cara melihat perilaku venue live sebelum uang asli masuk adalah mode paper, yang membaca harga Tokocrypto dan mensimulasikan eksekusi.

Kode yang lolos di testnet Binance tidak otomatis benar di Tokocrypto. Dua adapter terpisah menangani perbedaannya, dan mode paper wajib dijalankan beberapa hari sebelum tahap 8.

## Aturan keselamatan yang ditegakkan kode

Mode default adalah paper. Mode live hanya aktif kalau TRADING_MODE=live ada di .env dan flag --i-know-what-im-doing diberikan di command line. Salah satu saja, bot menolak jalan dan menyebut syarat mana yang kurang. Klien mainnet berkunci hanya bisa dibuat lewat jalur itu; konstruksi langsung ditolak sebelum klien sempat dibuat.

Kunci API hanya dibaca dari file .env lewat python-dotenv. .env ada di .gitignore sejak commit pertama, dan ada test yang memastikan git mengabaikannya. Setiap baris log melewati penyamar yang mengganti kunci dengan tanda bintang.

Kill switch (rugi harian, runaway order, gagal koneksi beruntun, file STOP) ada sebelum order pertama bisa dikirim ke uang asli. Detail per pemicu ada di SPEC.md dan di config/default.yaml bagian risk.

## Membuat API key

Bot ini tidak pernah boleh punya izin withdrawal, di venue mana pun.

Tokocrypto (venue live). Buat kunci di halaman API Management setelah login. Dokumentasi API Tokocrypto menyatakan kunci dapat dibatasi ke tipe endpoint tertentu dan secara default mengakses semua endpoint, tapi tidak merinci pilihan izinnya dan tidak menyebut pembatasan IP. Karena itu, sebelum tahap 8: buka halaman itu, matikan izin withdrawal kalau ada pilihannya, aktifkan pembatasan IP kalau tersedia, dan catat apa yang tersedia. Tidak ada endpoint API untuk memverifikasi izin kunci di Tokocrypto, jadi pemeriksaan ini manual dan menjadi kewajiban pengguna. Simpan di .env sebagai TOKOCRYPTO_API_KEY dan TOKOCRYPTO_API_SECRET.

Binance Spot Testnet (venue develop). Buat kunci di https://testnet.binance.vision dengan login GitHub. Uang di sana palsu. Simpan sebagai BINANCE_TESTNET_API_KEY dan BINANCE_TESTNET_API_SECRET.

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

Cek konfigurasi tanpa menyentuh exchange. Output menampilkan mode, venue, biaya per sisi, dan semua parameter, dengan kunci tersamar.

```bash
uv run tradebot check-config
```

Cek koneksi ke venue sesuai mode. Perintah ini membandingkan jam lokal dengan jam server, memuat dan memvalidasi pair, mengambil batas pasar, harga, dan tiga bar terakhir, dan di mode testnet atau live juga menampilkan saldo dan order terbuka. Perintah ini tidak pernah mengirim order. Di mode paper tidak butuh kunci sama sekali dan membaca data Tokocrypto.

```bash
uv run tradebot check-exchange
```

Kalau jam mesin melenceng lebih dari batas di config, perintah gagal dengan pesan yang menyebut selisihnya dalam milidetik; sinkronkan jam sistem lalu ulangi. Kalau pair di config tidak ada lagi di venue, perintah gagal dengan pesan yang menyebut pair dan jumlah pasar per quote yang tersedia.

Perintah fetch-data, backtest, backtest --stress, dan run ditambahkan di tahap berikutnya sesuai urutan di SPEC.md.

## Menjalankan test

```bash
uv run pytest
```

Test yang butuh kunci testnet ditandai testnet. Kalau kunci belum ada di .env, test itu dilewati, dan di akhir run selalu tercetak ringkasan berapa yang dilewati dan alasannya. Hasil hijau tanpa membaca ringkasan itu tidak berarti semuanya teruji. Salah satu test testnet memasang limit order jauh di bawah harga pasar lalu membatalkannya; uang testnet palsu, dan kalau test gagal di tengah order itu tetap dibersihkan.

Test yang memanggil endpoint publik Tokocrypto sungguhan ditandai network. Saat bekerja offline, lewati dengan:

```bash
uv run pytest -m "not network"
```

## Biaya

Default di config/default.yaml adalah biaya Tokocrypto untuk pasangan USDT, dari artikel resmi Informasi Biaya Transaksi di Tokocrypto yang berlaku sejak 18 Juni 2026: taker 0,15 persen, PPh 22 final 0,21 persen yang dipungut di kedua sisi, dan biaya bursa ICEx 0,0444 persen. Jumlahnya 0,4044 persen per sisi, ditambah asumsi slippage 0,15 persen. Satu putaran masuk-keluar sekitar 1,11 persen. Angka fee ini realistis dari exchange, bukan margin aman. Yang pesimistis adalah perintah backtest --stress, yang menggandakan fee, biaya bursa, dan slippage; pajak tidak digandakan karena angkanya pasti.

Komponen biaya bursa paling sering berubah, tiga kali dalam 2026 saja. Cek ulang setiap kali Tokocrypto mengumumkan penyesuaian biaya, dan perbarui komentar tanggal di config.

Semua backtest yang pernah memakai asumsi fee Binance (0,1 persen per sisi tanpa pajak dan biaya bursa) tidak berlaku lagi. Angka lamanya tinggal sebagai komentar di config.

## Jaringan

Pada jaringan tempat project ini dibangun (Biznet, September 2026), api.binance.com dan api.binance.me dijawab DNS ISP dengan alamat halaman blokir, sertifikatnya tidak cocok, dan mengganti resolver ke 1.1.1.1 tidak menolong karena permintaan DNS di port 53 ikut dibelokkan. Ini tidak lagi menjadi masalah untuk mode live karena venue live sekarang Tokocrypto. Yang dipakai bot dan semuanya bisa dijangkau: testnet.binance.vision untuk mode testnet, www.tokocrypto.com untuk endpoint akun dan order Tokocrypto, dan www.tokocrypto.site untuk data pasar Tokocrypto.

Satu hal penting soal ccxt: implementasi tokocrypto di ccxt 4.5.78 merutekan ticker, OHLCV, order book, dan trades untuk pasangan seperti BTC/USDT ke api.binance.com. Host itu bukan host resmi Tokocrypto untuk data tersebut; dokumentasi API Tokocrypto menyebut www.tokocrypto.site. Adapter mengarahkan rute itu ke host resmi lewat config exchange.live.market_data_url. Kalau field itu dikosongkan, bot memperingatkan dan data pasar akan gagal di jaringan ini.

## Struktur folder saat runtime

Folder data, logs, state, dan trades dibuat otomatis dan tidak masuk git. Folder state berisi jurnal order (ditulis sebelum order dikirim) dan state harian risk manager. Folder trades berisi CSV yang mencatat setiap trade untuk rekonsiliasi dan pajak; file ini hanya ditambah, tidak pernah ditimpa. Simpan cadangannya di luar mesin ini.

File bernama STOP di root project adalah kill switch manual. Membuat file itu menghentikan bot pada iterasi berikutnya.

## Status tahap

Tahap 1 selesai: setup project, config loader, logging, penanganan .env, gitignore.

Tahap 2 selesai: interface ExchangeAdapter, CcxtAdapter untuk Binance testnet, TokocryptoAdapter untuk venue live, dan factory yang memetakan mode ke venue. Adapter mencoba ulang gangguan jaringan dengan backoff dari config, tidak pernah mencoba ulang error autentikasi atau saldo, tidak pernah mengirim ulang order yang jawabannya hilang, memvalidasi pair saat connect, dan menolak membuat klien mainnet berkunci di luar jalur mode live. Test integrasi testnet, termasuk satu putaran order sungguhan, dilewati dengan ringkasan sampai kunci testnet ada di .env.

Tahap 3 sampai 8 menyusul berurutan, masing-masing dengan test yang lulus sebelum tahap berikutnya dimulai.
