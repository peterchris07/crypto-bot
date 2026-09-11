# SPEC: Bot Trading Crypto (Python + ccxt)

## Konteks

Saya mahasiswa, bikin bot trading crypto. Tujuan utamanya belajar engineering dan punya sistem yang bisa diuji, bukan cari profit cepat. Asumsikan strategi awal akan rugi dan desain sistemnya supaya kerugian itu murah dan terukur.

* Develop dan uji: Binance Spot Testnet (uang palsu, gratis)
* Target eksekusi live: Binance Spot mainnet
* Bahasa: Python 3.11+, library exchange: ccxt

Venue develop dan venue live sama, jadi adapter-nya satu kode. Bedanya cuma set_sandbox_mode dan pasangan API key. Ini keuntungan besar: perilaku di testnet dan di live praktis identik, jadi bug yang muncul live hampir pasti bug logika, bukan bug beda venue.

Tetap bangun ExchangeAdapter sebagai interface. Bukan karena sekarang perlu, tapi supaya pindah venue nanti tidak berarti tulis ulang bot.

## Aturan Keras

Ini bukan preferensi, ini syarat. Kalau salah satu dilanggar, project dianggap gagal.

1. Default mode adalah paper. Mode live hanya aktif kalau variable TRADING_MODE=live di-set secara eksplisit DAN ada flag --i-know-what-im-doing di command line. Dua-duanya wajib, bukan salah satu.
2. API key tidak pernah hardcode. Semua dari .env lewat python-dotenv. .env masuk .gitignore sejak commit pertama. Sediakan .env.example berisi nama variable saja tanpa nilai.
3. Bot tidak pernah punya izin withdrawal. Dokumentasikan di README bahwa API key harus dibuat tanpa permission withdrawal dan dengan IP whitelist.
4. Kill switch wajib ada sebelum order pertama bisa dikirim. Bot berhenti total dan membatalkan semua order terbuka kalau:
   * kerugian harian melewati batas di config
   * jumlah order dalam satu menit melewati batas (proteksi runaway loop)
   * koneksi ke exchange gagal berturut-turut melewati batas
   * file STOP muncul di root folder (kill switch manual)
5. Setiap order ditulis ke log sebelum dikirim, bukan sesudah. Kalau proses mati di tengah, saya harus tetap tahu order apa yang mungkin sudah masuk.
6. Tidak ada LLM di dalam loop trading. Logika eksekusi harus deterministik dan bisa di-backtest ulang dengan hasil identik.
7. Backtest wajib menghitung fee dan slippage. Taruh angkanya di config supaya gampang diuji sensitivitasnya. Default: fee taker Binance spot, dan slippage dibuat lebih pesimistis dari kenyataan. Sediakan satu perintah untuk menjalankan backtest yang sama dengan fee dan slippage digandakan. Kalau strategi cuma profitable di angka optimis, saya mau langsung tahu.

## Struktur Project

```
.
├── SPEC.md
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── config/
│   └── default.yaml
├── src/
│   ├── exchange/
│   │   ├── base.py          # interface ExchangeAdapter
│   │   ├── ccxt_adapter.py  # implementasi live/testnet via ccxt
│   │   └── paper.py         # implementasi simulasi, pakai data harga live
│   ├── data/
│   │   ├── fetch.py         # download OHLCV historis, simpan ke parquet
│   │   └── cache.py
│   ├── strategy/
│   │   ├── base.py          # interface Strategy
│   │   └── ema_cross.py     # strategi awal
│   ├── risk/
│   │   └── manager.py       # position sizing, kill switch, batas harian
│   ├── backtest/
│   │   ├── engine.py
│   │   └── metrics.py       # return, max drawdown, sharpe, win rate, profit factor
│   ├── live/
│   │   └── runner.py        # loop utama
│   └── cli.py
└── tests/
```

## Komponen

### ExchangeAdapter (interface)

Semua kode lain hanya bicara ke interface ini, tidak pernah langsung ke ccxt. Ini yang bikin pindah antara testnet, paper, dan mainnet jadi sekadar ganti config, dan bikin pindah exchange nanti jadi nulis satu file, bukan tulis ulang bot.

Method minimal: fetch_ohlcv, fetch_ticker, fetch_balance, create_order, cancel_order, cancel_all_orders, fetch_open_orders.

Dua implementasi:

* CcxtAdapter, satu kode, dipakai untuk testnet maupun mainnet. Sandbox on/off dan pemilihan API key murni dari config.
* PaperAdapter yang baca harga asli tapi order-nya cuma dicatat di memori

PaperAdapter tetap dibutuhkan meskipun testnet sudah ada. Alasannya: orderbook testnet Binance tipis dan harganya kadang menyimpang jauh dari pasar asli, jadi testnet bagus untuk menguji apakah kode jalan, tapi buruk untuk menguji apakah strateginya masuk akal. PaperAdapter mengisi celah itu dengan harga mainnet asli dan eksekusi simulasi.

Adapter harus menangani rate limit (aktifkan enableRateLimit di ccxt) dan retry dengan backoff untuk error jaringan sementara. Error autentikasi dan error saldo tidak boleh di-retry, harus langsung berhenti.

### Strategy (interface)

Satu method: terima DataFrame OHLCV, kembalikan signal (LONG, FLAT, atau SHORT kalau nanti perlu). Strategi tidak boleh tahu soal ukuran posisi, saldo, atau exchange. Itu urusan risk manager.

Strategi awal: EMA crossover. EMA cepat memotong EMA lambat ke atas jadi LONG, ke bawah jadi FLAT. Periodenya dari config, jangan hardcode.

### RiskManager

* Position sizing berbasis persentase equity, bukan jumlah tetap
* Batas maksimum satu posisi
* Batas kerugian harian, dihitung dari equity awal hari itu
* Stop loss dan take profit sebagai persentase dari harga masuk
* Semua kill switch di aturan keras nomor 4

### Backtest engine

Harus event-driven bar per bar, bukan vectorized. Alasannya: nanti logika live pakai kode strategy dan risk yang sama persis, jadi perilakunya harus identik.

Wajib hindari lookahead bias: keputusan di bar N hanya boleh pakai data sampai bar N-1 close.

Output metrik: total return, max drawdown, sharpe ratio, win rate, profit factor, jumlah trade, rata-rata durasi posisi, total fee yang terbayar.

Tampilkan juga hasil buy-and-hold di periode yang sama sebagai pembanding. Kalau strategi kalah dari buy-and-hold, saya mau langsung lihat itu.

## Urutan Build

Kerjakan berurutan. Setiap tahap harus punya test yang lulus sebelum lanjut.

1. Setup project, config loader, logging, .env handling, .gitignore
2. ExchangeAdapter interface + CcxtAdapter untuk Binance testnet. Test: berhasil fetch OHLCV dan saldo testnet, dan terbukti menolak jalan kalau config minta mainnet tanpa dua syarat mode live terpenuhi.
3. Data fetcher historis + cache parquet. Test: download 1 tahun data BTC/USDT 1h, verifikasi tidak ada bar bolong.
4. Strategy interface + EMA crossover. Test: pakai data buatan dengan crossover yang sudah diketahui posisinya, pastikan signal muncul persis di bar yang benar.
5. Backtest engine + metrik. Test: strategi dummy yang selalu FLAT harus menghasilkan return 0 dan 0 trade. Strategi yang selalu LONG harus mendekati buy-and-hold dikurangi fee.
6. RiskManager + semua kill switch. Test: setiap kondisi kill switch dipicu secara sintetis dan terbukti menghentikan bot.
7. PaperAdapter + live runner di mode paper. Jalankan minimal beberapa hari, bandingkan dengan hasil backtest di periode sama.
8. Mode live di Binance mainnet. Baru dikerjakan setelah saya bilang siap. Order pertama harus ukuran minimum yang diizinkan exchange, bukan ukuran yang dihitung risk manager. Naikkan ukuran hanya setelah beberapa siklus masuk dan keluar posisi berjalan benar.

## Catatan Kepatuhan (bukan tugas coding, tapi jangan dihapus)

Binance tidak berlisensi PFAK dari OJK. Konsekuensinya, dan ini tanggung jawab saya sendiri di luar kode:

* Tidak ada perlindungan regulator Indonesia kalau ada sengketa atau dana nyangkut
* Pajak tidak dipotong otomatis seperti di exchange lokal, jadi pelaporan jadi kewajiban saya sepenuhnya
* Off-ramp ke IDR biasanya lewat P2P, dan itu titik risiko tersendiri

Karena itu bot harus mencatat setiap trade ke file CSV terpisah dengan kolom: timestamp, pair, sisi, jumlah, harga, nilai dalam quote currency, fee, dan fee currency. File ini bukan log debug, ini bahan mentah untuk pelaporan pajak dan harus rapi, berurutan, dan tidak pernah ditimpa.

## Definition of Done per Tahap

* Ada test yang lulus
* Tidak ada nilai hardcode yang seharusnya di config
* Log cukup jelas untuk saya baca tanpa buka kode
* Kamu jelaskan dalam 3 kalimat apa yang bisa gagal di tahap ini dan bagaimana saya tahu kalau itu terjadi

## Yang Tidak Boleh Dilakukan

* Jangan bikin dashboard, web UI, atau notifikasi Telegram sebelum tahap 7 selesai. Itu pengalih perhatian dari bagian yang benar-benar menentukan.
* Jangan optimasi parameter strategi sampai overfit ke data historis. Kalau saya minta parameter sweep, ingatkan saya soal ini dan pakai walk-forward validation.
* Jangan tambah indikator baru tanpa saya minta.
* Jangan pernah bilang strategi ini "profitable" berdasarkan backtest saja.

## Addendum: keputusan desain yang disepakati (11 September 2026)

Bagian ini melengkapi spec di atas. Kalau ada yang bertentangan, addendum ini yang berlaku, karena diputuskan belakangan.

### Stop loss dua lapis

Lapis 1 adalah stop loss dan take profit di sisi bot: bot memantau harga tiap iterasi loop dan mengirim market order saat tembus. Lapis ini yang aktif normal, dan ini yang dipakai backtest dan paper supaya logikanya identik. Di backtest, tembus dideteksi dari high dan low bar, fill di harga stop ditambah slippage.

Lapis 2 adalah stop order di sisi exchange, dipasang segera setelah posisi terbentuk, dengan jarak lebih lebar dari lapis 1. Default 2 kali jarak, angkanya di config (risk.exchange_stop_multiplier). Ini jaring bencana: normalnya tidak pernah kena, kena hanya kalau bot sudah mati.

Konsekuensi: stop order di exchange mengunci aset. Sebelum bot mengirim order keluar sendiri, stop itu harus dibatalkan dulu. Urutan yang salah membuat order keluar ditolak karena saldo terkunci. Ini harus ada sebagai test eksplisit.

Lapis 2 dikerjakan di tahap 8. Tahap 6 dan 7 cukup lapis 1, tapi interface-nya dirancang sejak tahap 2 supaya tahap 8 tidak bongkar ulang.

### Flatten saat kill switch, diputus per pemicu

Batas rugi harian: flatten, jual posisi. Ini seluruh alasan batas itu ada.

Gagal koneksi beruntun: tidak bisa flatten. Kasus ini ditutup lapis 2.

Runaway order per menit: jangan flatten. Ini bug loop, menambah order memperparah. Cancel semua order terbuka, berhenti, exit code bukan nol.

File STOP manual: jangan flatten. Pengguna yang intervensi, pengguna yang memutuskan.

Semua ini ada di config sebagai risk.flatten_on, satu boolean per pemicu.

### Fee dan slippage default adalah angka realistis, bukan pesimistis

Fee taker 0,1 persen adalah tarif standar Binance spot. Slippage 0,1 persen untuk BTC/USDT ukuran retail sudah konservatif. README tidak boleh menyebut default ini pesimistis. Yang pesimistis adalah output perintah backtest --stress, yang menggandakan keduanya.

### Lokasi project

Project ada di ~/dev/crypto-bot, bukan di Documents, karena Documents bisa ikut sinkron iCloud. Sinkronisasi di belakang layar merusak asumsi fsync pada jurnal order dan file state.

Python 3.12 dipasang lewat uv di venv project, tanpa mengubah Python sistem.

### Gap data

Fetcher tidak mengarang bar. Setiap gap dilaporkan. Gap juga ditangani saat runtime: kalau loop live menerima bar yang stale atau bolong, strategi tidak mengambil keputusan atas bar itu. Iterasi dilewati dan dicatat di log. Toleransi ada di config (live.stale_bar_tolerance_seconds, data.max_gap_bars).

### Minimum notional

Binance punya batas minimum nilai order. Kalau hasil position sizing di bawah batas itu, bot berhenti dengan pesan jelas, bukan diam-diam melewatkan trade. Trade yang hilang tanpa jejak membuat backtest dan live diam-diam berbeda.

### Time drift

Saat start, bot membandingkan jam lokal dengan jam server exchange. Kalau selisihnya melebihi exchange.max_time_drift_ms, bot gagal dengan pesan yang menyebut angka drift-nya, bukan sebagai error autentikasi yang membingungkan.

### Test yang dilewati harus terlihat

Kalau kunci testnet belum ada, test testnet dilewati, tapi tidak diam-diam. Di akhir run pytest selalu tercetak ringkasan jumlah test yang dilewati beserta alasannya.

### Keputusan lain yang disetujui apa adanya

TRADING_MODE punya tiga nilai: paper (default), testnet, live. Testnet tidak butuh flag. Pasangan BTC/USDT, timeframe 1h, order tipe market. Batas hari untuk rugi harian memakai UTC. EMA 20 dan 50 tanpa optimasi. Signal adalah state target (LONG atau FLAT), bukan event; runner membandingkan state target dengan posisi nyata dan membuat order hanya kalau berbeda. Spot saja: SHORT diterima interface tapi diperlakukan sebagai FLAT dengan peringatan di log. Tahap 8 menambah pre-flight cek api restrictions Binance dan menolak jalan kalau withdrawal aktif. Kode ada di src/tradebot/ sebagai package; sisanya mengikuti pohon di atas.
