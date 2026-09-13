# H1 skrining kotor: hasil periode riset

Dijalankan 2026-09-13 sesuai PRAREGISTRASI.md (termasuk amandemennya). Percobaan
ke-2 (rebalance 14 hari) dan ke-3 (rebalance 28 hari) dalam hitungan riset;
baseline EMA adalah percobaan ke-1. Skrip: `skrining.py`; keluaran mentah:
`data/skrining_hasil.json`.

## Apa yang dijalankan

- Data: arsip publik Binance, bar harian, 356 pasangan USDT setelah
  pengecualian (24 stablecoin/RWA, 51 token leverage, 69 saham tertokenisasi,
  dan 234 simbol yang baru ada setelah 2022-12). Tiga seri diputus karena
  lompatan close lebih dari 10x dalam sehari (relisting): COCOS 2021-01-23,
  DREP 2021-04-02, LUNA 2022-05-31.
- Periode: riset saja, 2017-08-17 sampai 2022-12-31. Awal efektif 2018-06-27
  (tanggal pertama dengan minimal 10 simbol yang punya 30 hari data).
  Validasi dan holdout tidak dihitung.
- Semesta per tanggal rebalance: 20 pasangan dengan quote volume 30 hari
  terbesar di antara yang punya data pada tanggal itu. Sinyal return 30 hari,
  pegang 5 teratas bobot sama, eksekusi di close hari rebalance, tanpa biaya
  dan tanpa slippage. Simbol yang berhenti keluar di close terakhirnya.

## Hasil

| Konfigurasi | Rebalance | Rotasi rata-rata dari 5 | Top-5 kumulatif | Top-5 CAGR | Keranjang-20 CAGR | BTC CAGR | Selisih top-5 dikurangi keranjang per tahun | MDD top-5 | MDD keranjang |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A. 14 hari | 118 | 2,80 | -84,9% | -34,2% | -21,2% | +24,6% | **-13,0%** | -93,7% | -88,8% |
| B. 28 hari | 59 | 4,00 | -91,6% | -42,3% | -20,5% | +24,6% | **-21,7%** | -93,6% | -89,5% |

Keranjang-20 kumulatif: -65,9% (14 hari) dan -64,6% (28 hari). BTC
buy-and-hold pada jendela yang sama: +169,7%.

## Vonis terhadap kriteria gugur

Ambang pra-registrasi: selisih kotor top-5 dikurangi keranjang-20 minimal
+9 persen per tahun pada salah satu konfigurasi. Hasilnya negatif pada
keduanya: -13,0 dan -21,7 persen per tahun, KOTOR, sebelum biaya yang
diperkirakan 11,5 dan 8,7 persen per tahun. Seleksi momentum 30 hari di
semesta ini bukan sekadar gagal mengalahkan biaya; ia memilih koin yang lalu
berbalik arah. **H1 GUGUR** pada skrining periode riset. Engine portofolio
tidak dibangun. Tidak ada konfigurasi tambahan yang dicoba.

Dua fakta pendukung dari angka di atas:

- Rotasi terukur 2,80 dari 5 (14 hari) dan 4,00 dari 5 (28 hari), lebih tinggi
  dari tebakan Langkah 0 (2 dan 3). Perkiraan drag biaya naik menjadi sekitar
  16 dan 11,6 persen per tahun. Ambangnya lebih tinggi lagi dari 9 persen.
- Keranjang-20 sendiri kalah jauh dari BTC (-21 persen vs +25 persen per
  tahun). "20 paling likuid menurut volume 30 hari" memilih koin yang sedang
  ramai, dan keramaian itu cenderung berbalik; semesta ini sudah miring
  sebelum seleksi momentum ditambahkan.

## Keterbatasan

- Kotor: tanpa biaya, slippage, batas pasar, stop, atau RiskManager. Semua
  faktor itu hanya memperburuk angka top-5, bukan memperbaikinya.
- Eksekusi di close hari sinyal, bukan open bar berikutnya seperti engine;
  bias ini menguntungkan strategi, bukan merugikannya.
- Delisting keluar di close terakhir yang tersedia. Untuk delisting sungguhan
  harga itu bisa lebih tinggi dari yang bisa direalisasikan (kembali
  menguntungkan strategi).
- LUNA 2022 dimasukkan: seri lama berakhir di close terakhir sebelum lompatan
  relisting 2022-05-31, jadi penurunan menuju nol ikut dihitung.
- Semesta dari arsip Binance; tanggal listing Tokocrypto sendiri belum
  diverifikasi (skrip `langkah0_tokocrypto.py` dijalankan pemilik di Mac).
- Survivorship yang tersisa kecil: simbol yang berhenti tetap ada di arsip
  sampai bulan terakhirnya (Langkah 0 bagian c).
- Pelanggaran yang dicatat di PRAREGISTRASI.md: dua baris ringkasan validasi
  konfigurasi 28 hari sempat terlihat sebelum penegasan "riset saja"
  diterapkan; angkanya tidak dicatat dan tidak dipakai.

## Verifikasi independen

Angka di atas direplikasi oleh implementasi kedua (`replikasi.py`) dari cache
CSV yang sama dengan matematika portofolio yang berbeda: NAV per periode
dihitung dari rata-rata close forward-fill relatif terhadap close awal
periode, tanpa unit dan tanpa kas beku eksplisit; simbol yang berhenti otomatis
beku di close terakhirnya. Hasilnya identik sampai satu desimal untuk semua
kolom tabel (CAGR, kumulatif, MDD, jumlah rebalance, rotasi) pada kedua
konfigurasi; keluaran mentahnya di `data/replikasi_hasil.json`.

Yang juga diperiksa dari replikasi: LUNA masuk top-5 pada 2022-03-02 dan
2022-03-16 (14 hari) dan 2022-03-02 (28 hari), lalu keluar dari top-5 sebelum
kejatuhan Mei 2022; kejatuhannya tetap masuk lewat keranjang-20 selama LUNA
masih 20 paling likuid, dan seri lamanya berakhir di close terakhir sebelum
lompatan relisting 2022-05-31. Review adversarial multi-agen yang dijadwalkan
untuk skrip ini mati karena interupsi sesi sebelum menghasilkan temuan; yang
dipakai sebagai verifikasi adalah replikasi di atas.
