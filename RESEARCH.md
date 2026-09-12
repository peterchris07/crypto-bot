# Protokol Riset Strategi

Dokumen ini mengatur bagaimana strategi ditemukan dan diuji, terpisah dari SPEC.md
yang mengatur bagaimana bot dibangun. Simpan di root repo. Claude Code wajib
membaca ini sebelum mengerjakan apa pun yang berhubungan dengan pencarian atau
evaluasi strategi.

---

## Masalah yang dokumen ini selesaikan

Bukan kekurangan ide. Masalahnya sebaliknya: terlalu mudah menemukan strategi yang
kelihatan bagus di data historis dan tidak berarti apa-apa.

Kalau saya menguji 1000 variasi strategi pada data yang sama, sekitar 50 akan lolos
uji signifikansi 5 persen murni karena kebetulan. Backtest terbaik dari 1000 percobaan
bukan penemuan, itu hasil pencarian. Bailey, Borwein, Lopez de Prado dan Zhu menulis
soal ini secara eksplisit: dengan jumlah percobaan yang cukup, siapa pun bisa
menghasilkan backtest yang mengesankan dari data acak sekalipun.

Konsekuensi praktis: **jumlah percobaan harus dihitung dan dilaporkan.** Sharpe ratio
tanpa keterangan berapa konfigurasi yang sudah dicoba adalah angka tanpa arti.

---

## Aturan Keras Riset

Sama statusnya dengan Aturan Keras di SPEC.md. Melanggar salah satu membuat hasilnya
tidak sah, bukan sekadar kurang rapi.

1. **Hipotesis dulu, baru data.** Setiap eksperimen dimulai dengan satu kalimat alasan
   ekonomi kenapa pola ini seharusnya ada, ditulis SEBELUM backtest dijalankan. Kalau
   alasannya tidak bisa ditulis, eksperimennya tidak dijalankan. "Kombinasi ini kebetulan
   menghasilkan Sharpe tinggi" bukan hipotesis.

2. **Holdout disentuh sekali seumur hidup.** Potong data menjadi tiga: riset, validasi,
   dan holdout final. Holdout dikunci di awal, tidak pernah dilihat, tidak pernah
   dipakai untuk memilih apa pun. Hanya dibuka satu kali, untuk satu strategi final,
   dan hasilnya diterima apa adanya. Kalau holdout sudah dibuka dan hasilnya jelek,
   holdout itu mati, bukan strateginya yang diperbaiki lalu diuji ulang di sana.

3. **Setiap percobaan dicatat, termasuk yang gagal.** Satu baris per eksperimen:
   tanggal, hipotesis, parameter, periode data, metrik, dan keputusan. Eksperimen yang
   tidak tercatat tetap menaikkan jumlah percobaan tapi menghilang dari perhitungan,
   dan itulah cara orang membohongi diri sendiri tanpa sadar.

4. **Cross-validation deret waktu harus purged dan embargo.** K-fold biasa bocor pada
   data finansial karena label satu bar tumpang tindih dengan bar berikutnya. Buang
   (purge) sampel latih yang periodenya beririsan dengan periode uji, lalu tambahkan
   jeda (embargo) setelah setiap fold uji. Tanpa ini, skor validasi terlalu optimis
   dan tidak ada yang memberitahu.

5. **Walk-forward, bukan optimasi satu kali.** Parameter dipilih dari data masa lalu,
   diuji pada periode berikutnya yang belum terlihat, lalu jendela digeser. Satu set
   parameter yang dioptimasi di seluruh data adalah overfit menurut definisi.

6. **Laporkan jumlah percobaan bersama setiap hasil.** Format wajib:
   "Sharpe 1,4 pada validasi, percobaan ke-37 dari 37." Angka kedua mengubah arti
   angka pertama.

7. **Buy-and-hold selalu ditampilkan.** Strategi yang kalah dari memegang BTC begitu
   saja, setelah fee, bukan strategi.

8. **Biaya dimasukkan sejak percobaan pertama.** Fee dan slippage venue tujuan, bukan
   venue develop. Riset tanpa biaya menghasilkan strategi frekuensi tinggi yang mati
   begitu biaya dimasukkan, dan itu membuang waktu berminggu-minggu.

---

## Dari mana hipotesis datang

Bukan dari mesin. Sumber yang masuk akal, berurutan dari yang paling murah:

- **Struktur pasar.** Funding rate perp, basis spot versus futures, jam perdagangan,
  efek akhir pekan, likuidasi beruntun. Ini punya alasan mekanis, bukan pola grafik.
- **Perilaku pelaku.** Reaksi berlebihan setelah berita, aliran dana stablecoin,
  perilaku setelah lonjakan volume.
- **Literatur akademik.** Momentum dan mean reversion cross-sectional punya bukti
  puluhan tahun di ekuitas; pertanyaannya apakah bertahan di kripto, dan itu hipotesis
  yang layak diuji.
- **Anomali yang kamu amati sendiri** dan bisa jelaskan sebabnya.

Yang bukan sumber hipotesis: menjalankan pencarian parameter lalu mencari cerita untuk
hasil terbaiknya. Itu urutan terbalik dan menghasilkan omong kosong yang meyakinkan.

---

## Lima Hipotesis Awal

Ditulis di depan, lengkap dengan alasan ekonomi dan kriteria gugur. Diuji satu per
satu, berurutan, di data riset. Jangan menambah hipotesis keenam sebelum kelima ini
selesai, karena setiap tambahan menaikkan jumlah percobaan dan melemahkan arti
hasilnya.

Venue: Tokocrypto. Biaya all-in taker 0,4044% per sisi untuk pasangan USDT, slippage
0,15% per sisi. Satu putaran masuk-keluar sekitar 1,11%. Setiap hipotesis di bawah
harus mengalahkan angka itu dikali jumlah putarannya, sebelum dibandingkan dengan
buy-and-hold.

### H1. Cross-sectional momentum pada keranjang koin

**Alasan ekonomi.** Momentum relatif punya bukti lintas kelas aset selama puluhan
tahun. Mekanismenya diduga underreaction terhadap informasi baru dan aliran dana yang
mengejar performa. Kalau mekanismenya nyata, dia seharusnya muncul juga di kripto.

**Bentuk.** Ambil N koin paling likuid di Tokocrypto dengan kuotasi USDT. Setiap
periode rebalance, peringkatkan berdasarkan return k periode terakhir. Pegang m
teratas dengan bobot sama, jual sisanya. Rebalance mingguan atau dua mingguan, bukan
harian, karena biaya.

**Parameter awal.** N=20, k=30 hari, m=5, rebalance 14 hari. Dipilih sebagai titik
tengah yang masuk akal, bukan hasil optimasi. Jangan menyapu parameter di tahap ini.

**Kriteria gugur.** Gugur kalau return bersih setelah biaya kalah dari buy-and-hold
BTC pada periode yang sama, atau kalau max drawdown lebih dari 1,5 kali drawdown
buy-and-hold.

**Jebakan yang harus ditangani.** Survivorship bias: keranjang harus dibangun dari
koin yang terdaftar pada tanggal itu, bukan dari daftar koin yang ada hari ini. Koin
yang sudah delisting harus tetap masuk keranjang historisnya sampai tanggal
delisting, dengan return delisting dihitung. Tanpa ini hasilnya terlalu optimis dan
tidak ada yang memberitahu.

### H2. Persistensi trader teratas, uji kelayakan sebelum copy-following

**Alasan ekonomi.** Premis seluruh copy trading adalah bahwa performa masa lalu
memprediksi performa berikutnya. Kalau premis itu salah, semua yang dibangun di
atasnya salah. Uji premisnya dulu, bukan strateginya.

**Bentuk.** Ini bukan strategi trading, ini uji kelayakan. Ambil leaderboard wallet
Hyperliquid, yang seluruh posisinya publik dan on-chain. Catat peringkat PnL pada
akhir bulan N. Cek peringkat wallet yang sama pada bulan N+1 dan N+2. Hitung korelasi
peringkat antar periode, misalnya Spearman.

**Kriteria lolos.** Korelasi peringkat positif dan stabil lintas beberapa pasangan
bulan. Kalau korelasinya nol atau acak, peringkat tinggi adalah keberuntungan, dan
seluruh kategori strategi ikut-trader gugur tanpa perlu diuji lebih jauh.

**Kenapa Hyperliquid dan bukan Nansen atau Arkham.** Label "smart money" di penyedia
itu diberikan secara retrospektif. Memakai label hari ini pada data masa lalu adalah
look-ahead bias dan menghasilkan backtest yang indah sekaligus fiktif. Peringkat PnL
Hyperliquid dihitung dari posisi on-chain yang tercatat pada waktunya, jadi bisa
direkonstruksi point-in-time.

**Catatan.** Bahkan kalau lolos, menyalinnya tetap menghadapi masalah latency: kamu
melihat posisi setelah dia masuk dan mengetahui keluarnya setelah dia keluar.
Persistensi adalah syarat perlu, bukan syarat cukup.

### H3. Netflow exchange agregat sebagai filter rezim

**Alasan ekonomi.** Koin yang keluar dari exchange menuju dompet pribadi mengurangi
pasokan yang siap dijual. Ini agregat dan lambat, jadi tidak bergantung pada atribusi
wallet tunggal yang selalu ambigu.

**Bentuk.** Bukan pemicu trade. Satu angka fitur: netflow BTC agregat 30 hari,
dinormalisasi. Dipakai sebagai gerbang on/off untuk strategi lain, bukan sebagai
sinyal mandiri.

**Kriteria gugur.** Gugur kalau menambahkannya sebagai gerbang tidak memperbaiki
return bersih atau drawdown H1 secara berarti. "Tidak memperbaiki" berarti gugur,
bukan berarti dicari cara lain memasangnya.

**Jebakan.** Data netflow sering direvisi ke belakang saat penyedia memperbaiki label
alamat exchange. Pastikan seri yang dipakai point-in-time, atau catat bahwa hasilnya
punya bias optimis yang tidak bisa dihilangkan.

### H4. Efek funding rate pada perp sebagai indikator posisi berlebih

**Alasan ekonomi.** Funding rate yang sangat positif berarti pemegang long membayar
short, yang menandakan posisi long berlebih dan rentan likuidasi beruntun. Ini
mekanis, bukan pola grafik.

**Bentuk.** Fitur: funding rate agregat pada perp BTC, dirata-ratakan beberapa hari.
Hipotesis: setelah funding ekstrem, return jangka pendek berikutnya cenderung
berlawanan arah.

**Kriteria gugur.** Efeknya harus bertahan setelah biaya dan harus muncul di lebih
dari satu periode, bukan hanya di satu tahun tertentu.

**Catatan.** Bot spot tidak bisa short, jadi hipotesis ini hanya dipakai sebagai
sinyal untuk keluar atau tidak masuk, bukan untuk membuka posisi berlawanan.

### H5. Return akhir pekan dan jam perdagangan

**Alasan ekonomi.** Kripto buka 24/7, tetapi aliran dana institusional dan likuiditas
market maker tidak. Kalau likuiditas berbeda secara sistematis menurut jam atau hari,
distribusi return-nya juga berbeda.

**Bentuk.** Uji deskriptif dulu: apakah distribusi return berbeda secara berarti
menurut hari dalam minggu dan jam UTC. Baru kalau ada perbedaan yang konsisten
lintas beberapa tahun, rumuskan aturan.

**Kriteria gugur.** Perbedaan yang hanya muncul di satu atau dua tahun adalah derau.
Butuh konsistensi lintas periode, bukan signifikansi statistik di satu sampel.

**Kenapa ini masuk daftar.** Murah diuji dan kalaupun ada efeknya kecil. Nilainya ada
pada holding period yang panjang, yang cocok dengan struktur biaya Tokocrypto.

---

### Catatan tentang urutan dan jumlah percobaan

Kelima hipotesis di atas dihitung sebagai lima percobaan, bukan satu. Kalau H1 diuji
di tiga konfigurasi parameter, itu tujuh percobaan total, bukan lima. Angka ini
dilaporkan bersama setiap hasil, sesuai Aturan Keras Riset nomor 6.

H2 dikerjakan pertama meskipun bukan strategi, karena hasilnya menentukan apakah
seluruh arah "ikuti trader lain" layak dikejar, dan biayanya paling murah.

---

## Di mana ML benar-benar punya tempat

Jangan meminta model memprediksi harga berikutnya. Rasio sinyal terhadap derau di deret
harga sangat rendah, dan model apa pun yang cukup fleksibel akan menghafal derau.
Deep learning di atas OHLCV mentah adalah cara paling mahal untuk overfit.

Yang lebih masuk akal:

**Meta-labeling.** Aturan sederhana menentukan arah (misalnya EMA crossover). Model ML
tidak menebak arah, dia hanya menjawab satu pertanyaan biner: sinyal ini layak diambil
atau dilewati. Masalah klasifikasi biner dengan label yang seimbang jauh lebih mudah
daripada regresi harga, dan kegagalannya lebih jinak: kamu melewatkan trade, bukan
masuk ke arah yang salah. Ini pendekatan Lopez de Prado.

**Klasifikasi rezim.** Model memutuskan pasar sedang tren atau sideways, dan bot
mematikan strategi yang tidak cocok dengan rezim itu. Ini menjawab langsung poin 5 di
skill crypto-trading-bot, satu-satunya bagian yang tadi saya tandai tidak bisa dikodekan.

**Rekayasa fitur dari data alternatif.** Sentimen berita, aktivitas on-chain, funding
rate, diolah menjadi beberapa angka yang masuk ke strategi sebagai fitur. Di sinilah LLM
boleh dipakai: offline, menghasilkan angka, jauh dari loop eksekusi.

Ketiganya hanya dikerjakan setelah pipeline tahap 3 sampai 7 selesai dan ada strategi
baseline yang bisa dibandingkan.

---

## Stack

Sengaja membosankan. Tidak butuh GPU, tidak butuh platform.

| Kebutuhan | Pakai | Alasan |
|---|---|---|
| Data | parquet + pandas | Sudah ada di tahap 3 |
| Fitur teknikal | pandas-ta | Cukup, dan tidak menambah masalah instalasi |
| Model | scikit-learn | Random forest dan gradient boosting sudah lebih dari cukup untuk meta-labeling |
| Validasi deret waktu | purged K-fold, tulis sendiri | Implementasinya pendek dan harus kamu pahami |
| Pencatatan eksperimen | CSV atau SQLite di repo | MLflow baru sepadan setelah ratusan eksperimen |
| Notebook | Jupyter, hanya untuk eksplorasi | Apa pun yang jadi keputusan dipindahkan ke kode yang diuji |

Yang tidak dipakai sampai ada alasan konkret: deep learning framework, platform
eksperimen berbayar, agen LLM yang mengambil keputusan, data provider berbayar.

---

## Urutan kerja

1. Selesaikan tahap 3 sampai 7 di SPEC.md. Tanpa backtester yang benar, riset apa pun
   menghasilkan angka yang tidak bisa dipercaya.
2. Kunci holdout. Catat tanggal potongnya di file ini.
3. Lima hipotesis sudah tertulis di bagian "Lima Hipotesis Awal". Mulai dari H2 karena
   paling murah dan hasilnya menutup atau membuka seluruh arah copy-following.
4. Uji satu per satu di data riset. Catat semuanya, termasuk yang mati.
5. Yang bertahan diuji walk-forward di data validasi.
6. Kalau ada yang masih bertahan, baru pertimbangkan meta-labeling untuk memperbaiki
   rasio, bukan untuk menyelamatkan strategi yang gagal.
7. Satu kandidat final, satu kali buka holdout.

---

## Kapan berhenti

Tulis angkanya sekarang, sebelum ada hasil yang bikin emosional.

- Kalau setelah 20 hipotesis tidak ada yang lolos validasi, kesimpulannya bukan
  "butuh model yang lebih canggih", tapi bahwa pendekatan ini tidak menemukan edge.
  Itu hasil yang sah dan hemat.
- Kalau kandidat final gagal di holdout, selesai. Tidak ada putaran kedua di holdout
  yang sama.

---

## Bacaan

- Bailey, Borwein, Lopez de Prado & Zhu, "Pseudo-Mathematics and Financial Charlatanism:
  The Effects of Backtest Overfitting on Out-of-Sample Performance", Notices of the AMS,
  Mei 2014. https://www.ams.org/notices/201405/rnoti-p458.pdf
- Bailey & Borwein, "The Probability of Backtest Overfitting".
  https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Marcos Lopez de Prado, *Advances in Financial Machine Learning*, Wiley 2018.
  Sumber untuk purged cross-validation, embargo, meta-labeling, dan triple-barrier
  labeling.
