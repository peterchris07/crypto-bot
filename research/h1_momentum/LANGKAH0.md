# H1 Langkah 0: verifikasi data sebelum menulis apa pun

Tanggal: 2026-09-13. Status: LAPORAN, menunggu persetujuan. Tidak ada engine,
tidak ada backtest, tidak ada pra-registrasi yang ditulis sebelum Anda setuju.

## Sumber data yang dipakai, dan kenapa bukan endpoint Tokocrypto

Host data Tokocrypto (www.tokocrypto.site, www.tokocrypto.com, halaman support)
tidak terjangkau dari lingkungan build (proxy menolak). Yang terjangkau adalah
arsip publik Binance di bucket S3 `data.binance.vision` (bar harian per simbol,
per bulan, sejak 2017-08). Ini sumber yang tepat untuk H1 karena:

- Buku BTC/USDT dan pasangan USDT lain di Tokocrypto adalah buku Binance yang
  dibagi (SPEC.md, Keputusan lain), jadi harga dan volume kuotasi USDT adalah
  buku yang sama.
- Arsip itu MENYIMPAN simbol yang sudah delisting (contoh: MATICUSDT sampai
  2024-09, FTMUSDT sampai 2025-01, GXSUSDT sampai 2022-04, BUSDUSDT sampai
  2023-12, token leverage UP/DOWN). Inilah yang membunuh H2 dan yang di sini
  justru tersedia.

Yang TIDAK bisa dijawab dari arsip Binance: apakah Tokocrypto sendiri
mendaftarkan pasangan itu pada tanggal tertentu. Tokocrypto mendaftarkan
sebagian dari pasangan Binance (daftar aset yang disetujui regulator), dan
riwayat listing/delisting Tokocrypto hanya ada di halaman support-nya.
Skrip `langkah0_tokocrypto.py` (jalankan di Mac) memeriksa 30 kandidat di
`exchangeInfo` Tokocrypto sekarang dan sejak kapan bar hariannya tersedia di
host data Tokocrypto.

Skrip yang menghasilkan angka di bawah: `langkah0_arsip_binance.py` lalu
`langkah0_arsip_binance_2.py`; keluaran mentahnya di `data/`.

## a. Pasangan USDT dengan volume cukup

Inventaris arsip: 3.710 simbol spot, 735 di antaranya kuotasi USDT; 491 masih
punya bar pada Agustus 2026, 243 sudah berhenti. Dari 735 itu, 51 adalah token
leverage (UP/DOWN/BULL/BEAR) dan sekitar 25 stablecoin atau aset dunia nyata
(USDC, FDUSD, USD1, RLUSD, XAUT, PAXG, dan sejenisnya); keduanya tidak layak
masuk keranjang momentum dan dikeluarkan.

30 teratas menurut volume kuotasi 30 hari (2026-08-13 sampai 2026-09-11, bar
harian, juta USDT), tanpa stablecoin, RWA, dan token leverage:

| # | Simbol | Vol 30 hari (juta USDT) | Bar harian pertama |
| --- | --- | --- | --- |
| 1 | BTCUSDT | 37.954 | 2017-08-17 |
| 2 | ETHUSDT | 22.892 | 2017-08-17 |
| 3 | SOLUSDT | 8.082 | 2020-08-11 |
| 4 | XRPUSDT | 6.568 | 2018-05-04 |
| 5 | ZECUSDT | 5.835 | 2019-03-21 |
| 6 | BNBUSDT | 3.272 | 2017-11-06 |
| 7 | DOGEUSDT | 2.105 | 2019-07-05 |
| 8 | SUIUSDT | 1.561 | 2023-05-03 |
| 9 | ENAUSDT | 1.403 | 2024-04-02 |
| 10 | TRUMPUSDT | 1.303 | 2025-01-19 |
| 11 | UNIUSDT | 1.296 | 2020-09-17 |
| 12 | NEARUSDT | 1.275 | 2020-10-14 |
| 13 | XPLUSDT | 1.203 | 2025-09-25 |
| 14 | PUMPUSDT | 1.122 | 2025-09-11 |
| 15 | REUSDT | 1.069 | 2026-06-18 |
| 16 | PEPEUSDT | 1.044 | 2023-05-05 |
| 17 | LINKUSDT | 1.044 | 2019-01-16 |
| 18 | ADAUSDT | 947 | 2018-04-17 |
| 19 | TRXUSDT | 945 | 2018-06-11 |
| 20 | SNDKBUSDT | 827 | 2026-06-11 |
| 21 | UUSDT | 767 | 2026-01-13 |
| 22 | TUTUSDT | 764 | 2025-03-27 |
| 23 | PROMUSDT | 742 | 2023-03-17 |
| 24 | WLDUSDT | 719 | 2023-07-24 |
| 25 | TAOUSDT | 711 | 2024-04-11 |
| 26 | PYTHUSDT | 699 | 2024-02-02 |
| 27 | ARBUSDT | 657 | 2023-03-23 |
| 28 | DASHUSDT | 585 | 2019-03-28 |
| 29 | LTCUSDT | 574 | 2017-12-13 |
| 30 | AVAXUSDT | 572 | 2020-09-22 |

Yang dikeluarkan dari 40 teratas mentah: USDCUSDT (58,8 miliar, terbesar dari
semuanya), USD1USDT, RLUSDUSDT, XAUTUSDT, FDUSDUSDT. Perlu keputusan Anda:
SNDKBUSDT tampak seperti saham tertokenisasi; kalau kelas aset seperti itu
tidak diinginkan di keranjang kripto, ia ikut dikeluarkan.

Volume cukup untuk 5 posisi sebesar 20 persen dari modal ribuan USDT jelas
terpenuhi sampai jauh di bawah peringkat 30; batasnya bukan likuiditas, tapi
berapa lama koin itu punya riwayat (kolom terakhir).

## b. Sejak kapan riwayat harian tersedia

Kolom "bar harian pertama" di tabel di atas, dari arsip Binance. Rentang untuk
keranjang 20 koin: BTC dan ETH sejak 2017-08; pada akhir 2019 sudah ada
sekitar 100 pasangan USDT, pada akhir 2021 ada 301 yang aktif. Enam dari 30
teratas hari ini baru ada sejak 2025 atau 2026 (TRUMP, XPL, PUMP, RE, SNDKB,
U); mereka tidak bisa ada di keranjang historis sebelum tanggal itu, dan
memang tidak boleh.

Bar harian Tokocrypto sendiri: belum diverifikasi (host tidak terjangkau);
`langkah0_tokocrypto.py` melaporkannya per simbol saat dijalankan di Mac.

## c. Apakah koin yang terdaftar pada tanggal lampau bisa diketahui

Ya, untuk buku Binance: arsip menyimpan seluruh riwayat simbol yang sudah
berhenti, dengan bulan pertama dan bulan terakhirnya. Jumlah pasangan USDT
yang berhenti per tahun: 2018: 2, 2019: 3, 2020: 18, 2021: 20, 2022: 38,
2023: 24, 2024: 46, 2025: 51, 2026 (sampai Agustus): 41. Semesta pada tanggal
lampau bisa dibangun dari data yang ada pada tanggal itu, dan koin yang
kemudian delisting tetap ada di keranjang historis sampai bulan terakhirnya.

Ukuran bias kalau semesta dibatasi ke 20 paling likuid, diukur pada enam
tanggal lampau (top-20 menurut volume kuotasi bulan itu, tanpa stablecoin,
RWA, dan leverage; "hilang" berarti tidak punya bar lagi per Agustus 2026):

| Snapshot | Pasangan USDT aktif | Hilang sejak itu (semua) | Hilang dari top-20 | Hilang dari top-50 |
| --- | --- | --- | --- | --- |
| 2021-12 | 301 | 132 (44%) | 3: MATIC, FTM, GXS | 12 |
| 2022-12 | 322 | 128 (40%) | 3: MATIC, HOOK, FTM | 11 |
| 2023-12 | 357 | 128 (36%) | 1: MATIC | 4 |
| 2024-12 | 384 | 97 (25%) | 0 | 1 |
| 2025-12 | 425 | 43 (10%) | 0 | 1 |
| 2026-06 | 427 | 9 (2%) | 1: TON | 1 |

Bacaannya: di semesta penuh, 36 sampai 44 persen pasangan yang ada pada
2021-2023 sudah hilang, jadi keranjang dari daftar hari ini akan sangat bias.
Di top-20, yang hilang 0 sampai 3 per snapshot, dan sebagian besar adalah
penggantian nama, bukan kegagalan: MATIC menjadi POL (2024-09), FTM menjadi S
(2025-01). Yang benar-benar berhenti: GXS (2022-04), HOOK (2026-04), TON
(2026-07). Karena semuanya tetap ada di arsip, engine bisa memegang mereka
sampai bulan terakhirnya; bias sisanya bukan "koin yang tidak terlihat",
melainkan tiga hal yang lebih kecil dan harus disebut di HASIL.md:

1. Return delisting dihitung sebagai keluar di close bar terakhir yang
   tersedia. Untuk penggantian nama itu benar secara ekonomi (posisi
   berpindah ke simbol baru dengan nilai sama), untuk delisting sungguhan
   close terakhir bisa lebih tinggi dari yang bisa direalisasikan.
2. LUNAUSDT berlanjut sebagai satu seri walau token lama menjadi LUNC pada
   Mei 2022; seri itu memuat lompatan yang tidak mewakili apa yang diterima
   pemegang. Usulan: perlakukan LUNAUSDT sebelum 2022-06 sebagai seri terpisah
   yang berakhir 2022-05, atau keluarkan bulan itu; diputuskan di
   pra-registrasi, bukan setelah melihat hasil.
3. Subset Tokocrypto: pasangan yang ada di Binance belum tentu terdaftar di
   Tokocrypto pada tanggal yang sama. Yang bisa dilakukan tanpa halaman
   support Tokocrypto: irisan dengan `exchangeInfo` hari ini (skrip di Mac)
   dan mencatat bahwa tanggal listing Tokocrypto tidak diverifikasi.

Jadi mitigasi "batasi ke 20 paling likuid" tidak lagi perlu dipakai sebagai
pengganti data; ia tetap dipakai sebagai definisi semesta karena itu yang
Anda tetapkan di RESEARCH.md, dan biasnya kecil dan terukur seperti di atas.

## d. Perkiraan biaya

Satu posisi 20 persen ekuitas, satu putaran penuh 1,11 persen, jadi satu
rotasi (jual satu, beli penggantinya) memakan 0,222 persen ekuitas. Drag
tahunan = jumlah rebalance per tahun x rotasi per rebalance x 0,222 persen,
ditambah sekali masuk awal 5 x 20 persen x 0,554 persen (satu sisi, fee +
pajak + bursa + slippage) = 0,55 persen.

| Rotasi per rebalance | Rebalance 14 hari (26/tahun) | Rebalance 28 hari (13/tahun) |
| --- | --- | --- |
| 1 dari 5 | 5,8% | 2,9% |
| 2 dari 5 | 11,5% | 5,8% |
| 3 dari 5 | 17,3% | 8,7% |
| 4 dari 5 | 23,1% | 11,5% |

Turnover yang wajar: sinyal 30 hari dengan rebalance 14 hari memakai jendela
yang tumpang tindih 16 hari, jadi peringkat tidak berubah total; 2 dari 5
adalah tebakan tengah, drag sekitar 11,5 persen per tahun. Dengan rebalance 28
hari jendelanya hampir tidak tumpang tindih, 3 dari 5 wajar, drag sekitar
8,7 persen per tahun.

Pembanding baseline EMA: biaya 145,57 dari modal 1.000 pada periode
backtest-nya, sekitar 14,6 persen. Konfigurasi 14 hari (11,5 persen pada
tebakan tengah, 17,3 persen kalau turnover 3 dari 5) MENDEKATI baseline itu;
konfigurasi 28 hari sekitar 60 persen dari baseline. Ini mengubah
kelayakannya: H1 hanya layak kalau selisih return antara 5 koin teratas dan
keranjang rata-rata melebihi 9 sampai 12 persen per tahun secara konsisten,
dan literatur momentum kripto melaporkan premi kotor di kisaran yang tidak
jauh dari angka itu. Kalau Anda tetap ingin menjalankannya, engine akan
MENGUKUR turnover sungguhan, bukan menebaknya, dan melaporkannya di samping
biaya per komponen; kalau turnover terukur di atas 3 dari 5 pada 14 hari,
konfigurasi itu praktis sudah gugur sebelum melihat return.

## Yang saya usulkan untuk dikunci di pra-registrasi (belum ditulis)

- Semesta per tanggal rebalance: 20 pasangan USDT dengan volume kuotasi 30
  hari terbesar di antara yang punya bar pada tanggal itu, tanpa stablecoin,
  RWA, dan token leverage (daftar pengecualian dikunci sebelum backtest).
- Periode: data riset mulai 2020-01 (butuh 30 hari sebelumnya untuk sinyal;
  sebelum 2020 semesta terlalu tipis). Pemotongan riset/validasi/holdout
  diusulkan 2020-01 sampai 2023-12 / 2024-01 sampai 2025-06 / 2025-07 sampai
  2026-08; holdout dikunci dan tidak dibuka sampai satu kandidat final.
- Delisting: keluar di close bar terakhir; LUNAUSDT diperlakukan sebagai dua
  seri (sebelum dan sesudah 2022-06) atau bulan 2022-05 dikeluarkan; pilih
  satu sekarang.
- Dua konfigurasi rebalance, 14 dan 28 hari, keduanya dilaporkan; percobaan
  ke-2 dan ke-3 dari total percobaan riset (baseline EMA percobaan ke-1).

## Yang perlu dari Anda

1. Setuju atau ubah butir-butir di atas (khususnya SNDKB, LUNA, pemotongan
   periode).
2. Jalankan di Mac: `uv run python research/h1_momentum/langkah0_tokocrypto.py`
   dan kirim tabelnya, supaya subset Tokocrypto tercatat sebelum backtest.

Berhenti di sini sampai ada persetujuan.
