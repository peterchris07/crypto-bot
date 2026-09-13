# Pra-registrasi H1: cross-sectional momentum, skrining kotor

Dikunci 2026-09-13, SEBELUM skrining dijalankan. Perubahan setelah melihat hasil
tidak sah. Ini percobaan ke-2 dan ke-3 dalam hitungan riset (baseline EMA ke-1).

## Amandemen 2026-09-13 (sebelum hasil riset dilihat), dan satu pelanggaran

Pemilik menegaskan: skrining berjalan di PERIODE RISET SAJA (2017-08-17 sampai
2022-12-31). Validasi (2023-2024) tidak dihitung dan tidak dilihat; holdout
(2025 ke atas) tetap dikunci. Versi pertama dokumen ini menyebut riset dan
validasi; skrip pun mengunduh data sampai 2024-12 dan menghitung keduanya.

Pelanggaran yang harus dicatat, bukan disembunyikan: skrining versi pertama
sempat berjalan sampai selesai sebelum penegasan itu diterapkan, dan dua baris
ringkasan periode validasi untuk konfigurasi 28 hari (CAGR top-5, keranjang,
BTC, MDD) terlihat oleh asisten di ekor log. Angka itu tidak dicatat di mana
pun, file hasilnya dihapus, data 2023-2024 di cache dihapus, dan skrip diubah
supaya tidak lagi mengunduh atau menghitung periode itu. Konsekuensinya untuk
Aturan Keras Riset nomor 2: validasi H1 sudah "tersentuh" sekali secara tidak
sengaja pada tingkat skrining kotor; pemilik yang memutuskan apakah validasi
H1 masih dianggap bersih. Holdout tidak pernah diunduh.

Kriteria gugur di bawah ikut berubah: keputusan gugur diambil dari periode
riset saja.

## Pertanyaan tunggal

Berapa selisih return tahunan (KOTOR, tanpa biaya) antara keranjang top-5
momentum 30 hari dan keranjang bobot sama 20 koin paling likuid, pada periode
riset dan validasi? Ambang dari Langkah 0 (d): drag biaya sekitar 11,5 persen
per tahun untuk rebalance 14 hari dan 8,7 persen untuk 28 hari. Kalau selisih
kotor jauh di bawah 9 persen per tahun, H1 gugur dan engine portofolio tidak
dibangun.

## Data

- Sumber: arsip publik Binance (bucket S3 data.binance.vision), bar harian
  (1d), file bulanan per simbol. Buku USDT Tokocrypto adalah buku Binance yang
  dibagi. Arsip menyimpan simbol yang sudah delisting.
- Harga: close harian. Volume: quote volume harian (kolom 8).
- Pemotongan data (dicatat juga di RESEARCH.md):
  - riset: 2017-08-17 sampai 2022-12-31
  - validasi: 2023-01-01 sampai 2024-12-31
  - holdout: 2025-01-01 sampai sekarang, DIKUNCI; tidak dihitung, tidak
    dilihat, sampai ada satu kandidat final.

## Semesta

- Semua simbol dengan kuotasi USDT di arsip, KECUALI:
  - stablecoin dan aset dunia nyata: USDC, BUSD, TUSD, USDP, DAI, FDUSD, USD1,
    RLUSD, EUR, EURI, AEUR, PAXG, XAUT, USDE, USDS, PYUSD, GBP, TRY, BRL, UST,
    USTC, USDD, WBTC, WBETH, BFUSD, XUSD, SUSD, GUSD, USDSB, USDX;
  - token leverage: base berakhiran UP, DOWN, BULL, BEAR (kecuali JUP, SUP);
  - saham dan ETF tertokenisasi: base berakhiran B yang terdaftar sejak
    2025-06 (AAPLB, NVDAB, SPYB, SNDKB, dan seterusnya; daftar lengkap dicetak
    skrip). Keputusan pemilik: kelas aset berbeda, keluarkan.
- Per tanggal rebalance, semesta = 20 simbol dengan quote volume 30 hari
  terbesar di antara simbol yang punya bar pada tanggal itu dan minimal 31 bar
  sebelumnya. Kalau yang memenuhi kurang dari 10, tanggal itu dilewati
  (cash); tanggal pertama dengan 10 atau lebih menjadi awal efektif dan
  dilaporkan.

## Sinyal dan portofolio

- Sinyal: return 30 hari kalender terakhir (close t / close t-30 minus 1),
  dihitung dari data sampai close hari rebalance.
- Pegang m = 5 teratas, bobot sama, dari 20 di semesta.
- Eksekusi: di close hari rebalance (skrining kotor; engine nanti memakai open
  bar berikutnya dengan slippage). Tidak ada biaya, slippage, RiskManager,
  stop loss, atau batas pasar.
- Rebalance: DUA konfigurasi, 14 hari dan 28 hari kalender, dari awal efektif.
  Tidak ada nilai ketiga.
- Delisting dan diskontinuitas: kalau simbol tidak punya bar lagi, posisi
  keluar di close terakhir yang tersedia dan dananya menganggur sampai
  rebalance berikutnya. Lompatan close harian lebih dari 10x dalam satu hari
  (relisting token baru dengan simbol lama, misalnya LUNA setelah Mei 2022)
  memutus seri: posisi keluar di close sebelum lompatan, seri baru dimulai
  setelahnya. LUNA 2022 DIMASUKKAN dengan hasil sebenarnya menuju nol
  (keputusan pemilik: mengeluarkannya adalah survivorship bias).

## Pembanding

1. Keranjang bobot sama 20 koin semesta, di-rebalance pada tanggal yang sama.
2. Buy-and-hold BTC dari awal efektif.

## Yang dilaporkan

Periode riset saja, per konfigurasi: return kumulatif, return tahunan (CAGR),
max drawdown, jumlah rebalance, rata-rata rotasi per rebalance (untuk
memperbarui perkiraan biaya), dan selisih tahunan top-5 dikurangi
keranjang-20. Kedua konfigurasi dilaporkan, bukan yang terbaik saja. Selisih
terhadap BTC ikut dilaporkan sebagai konteks, bukan kriteria.

## Kriteria gugur (untuk skrining ini)

H1 gugur kalau, pada periode riset, selisih tahunan kotor top-5 dikurangi
keranjang-20 kurang dari 9 persen untuk KEDUA konfigurasi. Kalau salah satu
konfigurasi melewati 9 persen, pemilik memutuskan apakah engine portofolio
dibangun; kriteria gugur RESEARCH.md (kalah dari buy-and-hold BTC bersih,
atau drawdown lebih dari 1,5x drawdown BTC) baru diuji dengan biaya di sana.
