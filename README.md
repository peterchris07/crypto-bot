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

Perintah fetch-data, backtest, backtest --stress, dan run ditambahkan di tahap berikutnya sesuai urutan di SPEC.md.

## Menjalankan test

```bash
uv run pytest
```

Test yang butuh kunci testnet ditandai testnet. Kalau kunci belum ada di .env, test itu dilewati, dan di akhir run selalu tercetak ringkasan berapa yang dilewati dan alasannya. Hasil hijau tanpa membaca ringkasan itu tidak berarti semuanya teruji.

## Biaya di backtest

Default di config/default.yaml adalah fee taker 0,1 persen dan slippage 0,1 persen per sisi. Fee 0,1 persen adalah tarif taker standar Binance spot tanpa diskon BNB, jadi ini angka realistis, bukan margin aman. Slippage 0,1 persen untuk BTC/USDT ukuran retail sudah konservatif. Yang pesimistis adalah perintah backtest --stress, yang menggandakan keduanya. Kalau strategi hanya terlihat baik di angka default dan runtuh di --stress, itu informasi penting, bukan gangguan.

## Struktur folder saat runtime

Folder data, logs, state, dan trades dibuat otomatis dan tidak masuk git. Folder state berisi jurnal order (ditulis sebelum order dikirim) dan state harian risk manager. Folder trades berisi CSV yang mencatat setiap trade untuk pelaporan pajak; file ini hanya ditambah, tidak pernah ditimpa. Simpan cadangannya di luar mesin ini.

File bernama STOP di root project adalah kill switch manual. Membuat file itu menghentikan bot pada iterasi berikutnya.

## Status tahap

Tahap 1 selesai: setup project, config loader, logging, penanganan .env, gitignore. Tahap 2 sampai 8 menyusul berurutan, masing-masing dengan test yang lulus sebelum tahap berikutnya dimulai.
