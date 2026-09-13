# SPEC: Bot Trading Crypto (Python + ccxt)

Ini satu-satunya versi spesifikasi yang dibaca. Keputusan yang dulu ada di addendum bertanggal sudah dilebur ke badan dokumen ini. Protokol riset strategi ada di RESEARCH.md, terpisah dari dokumen ini.

## Konteks

Saya mahasiswa, bikin bot trading crypto. Tujuan utamanya belajar engineering dan punya sistem yang bisa diuji, bukan cari profit cepat. Asumsikan strategi awal akan rugi dan desain sistemnya supaya kerugian itu murah dan terukur.

* Develop dan uji: Binance Spot Testnet (uang palsu, gratis)
* Target eksekusi live: Tokocrypto Spot mainnet (berizin OJK, tidak punya testnet)
* Bahasa: Python 3.11+, library exchange: ccxt

Venue live semula Binance Spot mainnet. Diganti ke Tokocrypto pada 12 September 2026 karena api.binance.com diblokir ISP di level DNS (jawaban DNS dibelokkan ke halaman blokir Biznet, sertifikat tidak cocok, dan resolver luar di port 53 ikut dibelokkan). Testnet Binance (testnet.binance.vision) tidak diblokir, jadi tetap dipakai untuk develop.

Konsekuensinya, venue develop dan venue live sekarang berbeda. Kode yang lolos di testnet Binance tidak otomatis benar di Tokocrypto: nama parameter, aturan market buy, format simbol, pembacaan batas pasar, dan perilaku operasional venue berbeda. Dua hal yang menutup celah itu: ExchangeAdapter sebagai interface dengan dua implementasi terpisah, dan PaperAdapter yang membaca harga dari Tokocrypto supaya perilaku venue live terlihat sebelum uang asli masuk. Semua backtest yang pernah memakai asumsi fee Binance tidak berlaku lagi; biaya Tokocrypto dua sampai empat kali lipat.

## Aturan Keras

Ini bukan preferensi, ini syarat. Kalau salah satu dilanggar, project dianggap gagal.

1. Default mode adalah paper. Mode live hanya aktif kalau variable TRADING_MODE=live di-set secara eksplisit DAN ada flag --i-know-what-im-doing di command line. Dua-duanya wajib, bukan salah satu. Flag tanpa TRADING_MODE=live juga ditolak, supaya tidak ada jalur di mana bot diam-diam jalan di mode yang tidak dimaksud.
2. API key tidak pernah hardcode. Semua dari .env lewat python-dotenv. .env masuk .gitignore sejak commit pertama. Sediakan .env.example berisi nama variable saja tanpa nilai. Kunci tidak pernah masuk log: setiap baris log melewati penyamar.
3. Bot tidak pernah punya izin withdrawal. README mendokumentasikan bahwa API key harus dibuat tanpa permission withdrawal dan dengan pembatasan IP kalau venue menyediakannya. Tokocrypto tidak punya endpoint untuk memeriksa izin key dan tidak mendokumentasikan IP whitelist, jadi pemeriksaan ini manual di halaman API Management dan dicatat di README sebagai kewajiban pengguna.
4. Kill switch wajib ada sebelum order pertama bisa dikirim ke uang asli. Bot berhenti total dan membatalkan semua order terbuka kalau:
   * kerugian harian melewati batas di config
   * jumlah order dalam satu menit melewati batas (proteksi runaway loop)
   * koneksi ke exchange gagal berturut-turut melewati batas
   * file STOP muncul di root folder (kill switch manual)

   Apakah posisi yang sedang dipegang ikut dijual diputus per pemicu, bukan satu flag global. Batas rugi harian: flatten, karena itu seluruh alasan batas itu ada. Gagal koneksi beruntun: tidak bisa flatten, kasus ini ditutup stop lapis 2 di exchange. Runaway order: jangan flatten, ini bug loop dan menambah order memperparah; cancel semua, berhenti, exit code bukan nol. File STOP: jangan flatten, pengguna yang intervensi, pengguna yang memutuskan. Semua ada di config sebagai risk.flatten_on.

   Aturan ini melindungi uang asli. Testnet Binance memakai uang palsu, dan justru ada supaya jalur order bisa diuji terhadap API sungguhan tanpa risiko; test integrasi testnet boleh dan harus memasang serta membatalkan order.
5. Setiap order ditulis ke log sebelum dikirim, bukan sesudah. Kalau proses mati di tengah, saya harus tetap tahu order apa yang mungkin sudah masuk. Setiap order punya client order id buatan bot; order yang jawabannya hilang di jaringan tidak pernah dikirim ulang, tapi direkonsiliasi lewat id itu.
6. Tidak ada LLM di dalam loop trading. Logika eksekusi harus deterministik dan bisa di-backtest ulang dengan hasil identik.
7. Backtest wajib menghitung fee, pajak, biaya bursa, dan slippage venue tujuan. Angkanya di config supaya gampang diuji sensitivitasnya. Default adalah angka realistis dari exchange, bukan pesimistis. Sediakan satu perintah untuk menjalankan backtest yang sama dengan fee, biaya bursa, dan slippage digandakan; pajak tidak digandakan karena angkanya pasti dari peraturan. Yang pesimistis adalah output perintah itu. Kalau strategi cuma profitable di angka default, saya mau langsung tahu.

## Keputusan Desain

### Stop loss dua lapis

Lapis 1 adalah stop loss dan take profit di sisi bot: bot memantau harga tiap iterasi loop dan mengirim market order saat tembus. Lapis ini yang aktif normal, dan ini yang dipakai backtest dan paper supaya logikanya identik. Di backtest, tembus dideteksi dari high dan low bar, fill di harga stop ditambah slippage.

Lapis 2 adalah stop order di sisi exchange (STOP_LOSS_LIMIT), dipasang segera setelah posisi terbentuk, dengan jarak lebih lebar dari lapis 1. Default 2 kali jarak, angkanya di config (risk.exchange_stop_multiplier). Ini jaring bencana: normalnya tidak pernah kena, kena hanya kalau bot sudah mati.

Konsekuensi: stop order di exchange mengunci aset. Sebelum bot mengirim order keluar sendiri, stop itu harus dibatalkan dulu. Urutan yang salah membuat order keluar ditolak karena saldo terkunci. Ini harus ada sebagai test eksplisit, bukan cuma di kode.

Lapis 2 sudah dikerjakan (tahap 8) dan hanya aktif di adapter yang mendukung stop order di exchange, yaitu Tokocrypto berkunci dan Binance testnet; paper tidak mensimulasikannya. Runner memasangnya segera setelah posisi terbentuk dengan jarak stop_loss_fraction x exchange_stop_multiplier dan harga limit exchange_stop_limit_offset_fraction di bawah harga stop, mencatat id order-nya di catatan posisi, dan membatalkannya DULU sebelum order keluar apa pun; kalau pembatalan gagal, order keluar tidak dikirim. Kalau saat pembatalan atau saat start stop itu ternyata sudah tereksekusi, posisi dianggap sudah ditutup oleh exchange: fill-nya dicatat ke ledger dan direkonsiliasi, jurnal mencatat "executed", catatan posisi dihapus, dan tidak ada order jual tambahan. Venue yang tidak mengiklankan STOP_LOSS_LIMIT membuat mode live gagal keras saat connect; di paper dan testnet cukup peringatan. Urutan batalkan-lalu-keluar diuji eksplisit dengan klien palsu yang menolak jual selama stop masih terbuka.

### Biaya per komponen

Biaya di config dipecah menjadi taker_fee_rate (biaya taker exchange), tax_rate (PPh 22 final yang dipungut exchange), exchange_fee_rate (biaya bursa dan kliring), dan slippage_rate. Ketiganya berubah terpisah dan pada waktu berbeda, jadi disimpan terpisah dengan komentar tanggal berlaku. Backtest menjumlahkannya per sisi dan melaporkan biaya all-in per putaran secara eksplisit. Nilai lama Binance tetap ada di config sebagai komentar dengan keterangan tidak berlaku lagi.

### Dua venue, tiga adapter

CcxtAdapter untuk Binance (testnet lewat set_sandbox_mode; mainnet Binance masih bisa diminta lewat config tapi bukan target). TokocryptoAdapter sebagai implementasi terpisah dari interface yang sama, dengan penanganan khusus venue: host data pasar resmi Tokocrypto (www.tokocrypto.site) menggantikan rute ccxt ke api.binance.com; market buy dikonversi ke jumlah quote; stop order lewat parameter stopPrice; client id sebagai clientId; minimum notional dibaca dari filter mentah karena ccxt melewatkannya; tidak ada cancel-all sehingga dibatalkan satu per satu; dan validasi pair yang keras saat start. PaperAdapter membungkus adapter publik Tokocrypto dan mensimulasikan eksekusi.

Kedua adapter ccxt berbagi satu kerangka (retry, terjemahan error, cek jam, gerbang mainnet, pencatatan niat order) lewat kelas dasar, bukan lewat percabangan per-exchange di satu kelas.

Gerbang dua kunci: konstruktor adapter menolak klien mainnet berkunci kecuali pemanggil secara eksplisit memberi allow_mainnet_trading=True, dan di dalam src/ hanya factory yang melakukannya, hanya dalam mode live, yang sendiri hanya lahir dari TRADING_MODE=live plus flag. Konstruksi langsung tanpa argumen itu ditolak sebelum klien sempat dibuat.

### Adapter Tokocrypto lebih defensif

Tokocrypto pernah memindahkan 31 pair IDR ke mesin lain (November 2025), mengganti base URL data pasar, dan membatalkan semua order terbuka di pair terdampak tanpa aksi pengguna. Karena itu: saat connect, adapter memverifikasi pair di config benar-benar ada di load_markets, aktif, berjenis MBX, spot diizinkan, dan mengiklankan MARKET, LIMIT, dan STOP_LOSS_LIMIT; gagal dengan pesan jelas kalau ada yang hilang. Runner memperlakukan order terbuka yang lenyap sebagai kejadian yang mungkin, bukan error aneh.

### Celah yang diketahui di jalur rekonsiliasi

Pencarian order lewat client id di Tokocrypto memindai open orders lalu riwayat order dengan type -1 dan batas ORDER_HISTORY_LIMIT (200) order terbaru, tanpa startTime. Kondisi yang membuat celah ini muncul: bot mati beberapa jam setelah mengirim order yang jawabannya hilang, sementara akun terus bertransaksi sehingga order itu bukan lagi salah satu dari 200 order terbaru; rekonsiliasi lalu menyimpulkan order tidak ada, padahal jurnal write-ahead ada justru untuk mencegah kesimpulan itu. Urutan hasil endpoint riwayat order Tokocrypto (naik atau turun menurut waktu) belum terverifikasi dengan kunci asli, jadi pemotongan 200 baris bisa memotong sisi yang salah. Penutupnya adalah meneruskan startTime dari waktu niat order di jurnal; ini BELUM dikerjakan dan tercatat sebagai celah yang diketahui di README dan SIAP-PAKAI.md. Test tests/test_tokocrypto_keyed_integration.py mendokumentasikan celah ini dan dilewati sampai kunci Tokocrypto ada; pada akun dengan riwayat panjang test itu diharapkan gagal selama celah belum ditutup.

### Lokasi project

Project ada di ~/dev/crypto-bot, bukan di Documents, karena Documents bisa ikut sinkron iCloud. Sinkronisasi di belakang layar merusak asumsi fsync pada jurnal order dan file state. Python 3.12 dipasang lewat uv di venv project, tanpa mengubah Python sistem.

### Gap data

Fetcher tidak mengarang bar. Setiap gap dilaporkan: di log, di output fetch-data, di metadata parquet (acuan, ditulis dalam satu penulisan yang sama dengan bar-nya), dan di salinan BTC-USDT_1h.gaps.json untuk dibaca orang, supaya backtest dan orang yang membacanya tahu bar mana yang tidak ada. Gap lebih panjang dari data.max_gap_bars dianggap data rusak, bukan downtime exchange: fetch-data berhenti dengan DataGapError dan tidak menulis apa pun. Menaikkan batas itu adalah keputusan eksplisit di config setelah downtime-nya terkonfirmasi. Data yang melanggar skema atau grid bar (harga kosong, bar bergeser dari grid, jarak bukan kelipatan timeframe) juga DataError tanpa menulis apa pun. Bar sebelum bar pertama yang dikembalikan exchange bukan gap, karena pair bisa saja baru tercatat setelah tanggal yang diminta; ini dilaporkan sebagai peringatan terpisah.

Bar yang masih berjalan tidak pernah disimpan. Batas akhir unduhan dipotong ke waktu buka bar saat ini menurut jam server, pada grid venue: satuan menit, jam, dan hari sejajar epoch, sedangkan bar mingguan Binance dan Tokocrypto buka Senin 00:00 UTC, empat hari dari grid epoch yang jatuh di Kamis. Cache hanya berisi bar yang sudah tutup. Setiap kali ada bar baru, bar terakhir di cache diambil ulang dan versi barunya menimpa yang lama; tanpa bar baru yang mungkin, tidak ada permintaan dan cache tidak ditulis ulang. Cache ditulis atomik: ke file sementara bernama unik, fsync, ganti nama, fsync folder.

fetch-data mengabaikan TRADING_MODE dan kunci di .env dan selalu berjalan sebagai paper. Ini bukan jalur tersembunyi ke mode lain: paper adalah mode teraman, perintah ini hanya membaca data publik, dan pengabaian itu dicatat di log dan di output.

Gap juga ditangani saat runtime: kalau loop live menerima bar yang stale atau bolong, strategi tidak mengambil keputusan atas bar itu. Iterasi dilewati dan dicatat di log. Toleransi ada di config (live.stale_bar_tolerance_seconds, data.max_gap_bars).

### Minimum notional

Kalau hasil position sizing di bawah batas minimum nilai order exchange, bot berhenti dengan pesan jelas, bukan diam-diam melewatkan trade. Trade yang hilang tanpa jejak membuat backtest dan live diam-diam berbeda.

### Time drift

Saat start, bot membandingkan jam lokal dengan jam server exchange memakai titik tengah round trip. Panggilan pertama ke host memuat resolusi DNS dan jabat tangan TLS, jadi satu panggilan pemanasan dibuang dulu, lalu diambil exchange.time_sync_samples sampel dan dipakai sampel dengan rtt terkecil, karena ketidakpastian estimasi titik tengah sekitar rtt/2. Dua keadaan dipisahkan tegas. Jam melenceng: pengukuran valid (rtt/2 di bawah batas) dan selisihnya melebihi exchange.max_time_drift_ms, fatal dengan pesan yang menyebut selisih, rtt terbaik, dan jumlah sampel. Pengukuran tidak konklusif: rtt/2 lebih besar dari batas, jadi pengukuran tidak bisa memutuskan apa pun; ini jaringan yang lambat, bukan jam yang salah, dan tidak pernah dilaporkan sebagai jam yang salah. Untuk kasus kedua yang dilihat adalah batas yang benar-benar dipakai exchange, recv_window_ms: kalau batas atas selisih (|selisih| + rtt/2) masih di bawahnya, jam tidak mungkin membuat exchange menolak request dan bot lanjut dengan peringatan; kalau sudah menyentuhnya, bot berhenti dengan pesan bahwa yang gagal adalah pengukurannya. Batas max_time_drift_ms sendiri tidak dinaikkan.

### Test yang dilewati harus terlihat

Kalau kunci testnet belum ada, test testnet dilewati, tapi tidak diam-diam. Di akhir run pytest selalu tercetak ringkasan jumlah test yang dilewati beserta alasannya. Test yang memanggil endpoint publik sungguhan ditandai network dan bisa dimatikan saat offline.

### Catatan per mode dan overlay lokal

Catatan runtime (jurnal order, posisi, akun paper, ledger, penanda ukuran live, state risk harian, log) dipisah per mode lewat placeholder {mode_dir} di path config: kosong untuk paper, "live/" atau "testnet/" untuk mode berkunci. Paper tetap di state/, trades/, logs/ seperti sebelumnya supaya catatan yang sudah ada tidak hilang; mode berkunci masuk subfolder supaya posisi, jurnal, atau ledger simulasi tidak pernah terbaca sebagai uang asli saat start. Placeholder lain ditolak dengan nama field-nya. File STOP sengaja tetap satu: menghentikan apa pun yang jalan.

config/local.yaml adalah overlay milik pemilik di folder config, tidak di-commit, hanya boleh menimpa key yang sudah ada di default.yaml (section atau key baru ditolak dengan nama file dan path key-nya). Isinya live.enabled, live.api_key_verified_date, dan risk.position_fraction untuk trial; tidak pernah kunci API, dan `tradebot local-set` menolak nama key yang berbau kunci. local-set memuat ulang settings setelah menulis dan mengembalikan file kalau validasi gagal, jadi file itu tidak pernah berisi nilai yang ditolak.

Supervisor macOS dan LaunchAgent juga per mode (com.tradebot.paper, com.tradebot.live) dengan mode dipaksa lewat environment launchd, yang menang atas .env, supaya paper tetap paper setelah .env berisi TRADING_MODE=live. Supervisor tidak memulai ulang setelah exit 0, 2, 6, dan 9 (preflight live gagal). Paper dan live tidak dijalankan bersamaan.

### Trial live kecil sebagai pengganti paper run

Pemilik memutuskan melewati paper run dan langsung menguji di akun Tokocrypto asli dengan modal kecil (sekitar dua juta rupiah, dikonversi manual ke USDT). Konsekuensinya dicatat, bukan disembunyikan: keempat butir checklist pertama kali teruji dengan uang asli; kerugian maksimum dibatasi saldo yang disetor, bukan oleh kode; order pertama tetap dipaksa ke minimum exchange. Jalurnya dari Finder: live-setup (kunci hanya ke .env lewat stdin, tidak pernah ke layar, argumen proses, atau log; tiga pertanyaan API Management; tanggal verifikasi dan pecahan posisi ke local.yaml; preflight), live-start (preflight, fetch-data sebelum apa pun diaktifkan, ketikan SAYA SIAP, live.enabled=true, LaunchAgent; gagal memasang agent berarti live.enabled kembali false), live-stop (STOP, lepas agent, live.enabled=false; posisi tidak dijual otomatis, stop lapis 2 tetap di exchange). Skrip mendeteksi mode lewat scripts/env-mode.sh dengan toleransi yang sama seperti bot, plist dirender dengan escape XML (bukan sed), bootstrap tanpa kickstart -k, dan skrip yang memegang kunci mematikan xtrace. Perintah baca-saja tidak menulis banner ke log bot; status memperingatkan kalau supervisor mode lain hidup. Konversi IDR ke USDT sengaja tidak dibuatkan jalur order: bot hanya punya jalur BTC/USDT yang teruji.

### Keputusan lain

TRADING_MODE punya tiga nilai: paper (default), testnet, live. Testnet tidak butuh flag. Pasangan BTC/USDT, timeframe 1h, order tipe market. Di Tokocrypto, buku order BTC/USDT adalah buku Binance yang dibagi (likuiditas setara Binance, histori sejak Agustus 2017), sedangkan BTC/IDR adalah buku Tokocrypto sendiri yang tipis dan baru ada sejak November 2025. Batas hari untuk rugi harian memakai UTC. EMA 20 dan 50 tanpa optimasi. Signal adalah state target (LONG atau FLAT), bukan event; runner membandingkan state target dengan posisi nyata dan membuat order hanya kalau berbeda. Spot saja: SHORT diterima interface tapi diperlakukan sebagai FLAT dengan peringatan di log. Kode ada di src/tradebot/ sebagai package.

## Struktur Project

```
.
├── SPEC.md
├── RESEARCH.md              # protokol riset strategi
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── config/
│   ├── default.yaml
│   └── local.yaml           # overlay pemilik, tidak di-commit; ditulis `tradebot local-set`
├── scripts/
│   ├── bot-supervisor.sh    # supervisor per mode (paper|live) di bawah caffeinate, untuk launchd
│   ├── bot-start.sh / bot-stop.sh   # pasang/lepas LaunchAgent com.tradebot.<mode>
│   ├── paper-start.sh / paper-stop.sh / paper-supervisor.sh   # pembungkus mode paper
│   ├── live-setup.sh        # kunci ke .env lewat stdin, pertanyaan API Management, preflight
│   ├── live-start.sh / live-stop.sh   # SAYA SIAP -> live.enabled=true; stop -> false
│   ├── write-env.sh         # tulis TRADING_MODE dan kunci Tokocrypto ke .env, baris lain tetap
│   ├── install-commands.sh  # buat file .command untuk Finder (tidak di-commit)
│   └── com.tradebot.plist.template
├── src/tradebot/
│   ├── config.py            # YAML + local.yaml + .env + flag -> Settings, mode, {mode_dir}
│   ├── localconfig.py       # tulis config/local.yaml atomik untuk `tradebot local-set`
│   ├── logging_setup.py     # console + file berputar, UTC, penyamar kunci
│   ├── ledger.py            # CSV trade dua fase (pending -> reconciled), append-only
│   ├── exchange/
│   │   ├── base.py          # interface ExchangeAdapter + tipe Order, Balance, Ticker, MarketLimits
│   │   ├── errors.py        # RetryableExchangeError vs FatalExchangeError, OrderStateUnknownError
│   │   ├── ccxt_base.py     # kerangka bersama adapter ccxt
│   │   ├── ccxt_adapter.py  # Binance (testnet, dan mainnet kalau diminta)
│   │   ├── tokocrypto_adapter.py  # Tokocrypto mainnet, venue live
│   │   ├── factory.py       # build_adapter: mode -> venue, kunci, izin mainnet
│   │   └── paper.py         # simulasi eksekusi di atas harga Tokocrypto asli, akun dipersist
│   ├── data/
│   │   ├── ohlcv.py         # skema DataFrame OHLCV, parser timeframe, deteksi gap
│   │   ├── errors.py        # DataError, DataGapError, CacheError
│   │   ├── fetch.py         # download OHLCV historis inkremental, kebijakan gap
│   │   └── cache.py         # parquet per venue/pasangan/timeframe, tulis atomik, laporan gap
│   ├── strategy/
│   │   ├── base.py          # interface Strategy, Signal, kontrak jendela tetap
│   │   ├── ema_cross.py     # strategi awal
│   │   └── registry.py      # strategy.name -> kelas
│   ├── risk/
│   │   ├── manager.py       # position sizing, stop lapis 1, semua kill switch, batas harian
│   │   └── state.py         # equity awal hari UTC, dipersist atomik, selamat dari restart
│   ├── backtest/
│   │   ├── engine.py        # event-driven bar per bar, buy-and-hold dengan aturan sama
│   │   ├── metrics.py       # return, max drawdown, sharpe, win rate, profit factor
│   │   └── report.py        # laporan teks, biaya per komponen, pembanding
│   ├── live/
│   │   ├── journal.py       # write-ahead log order: intent, result, unknown, reconciled
│   │   ├── runner.py        # loop utama, PositionStore, rekonsiliasi jurnal saat start
│   │   ├── preflight.py     # pemeriksaan kesiapan live tanpa order
│   │   ├── stage.py         # penanda ukuran order live: minimum -> normal setelah siklus cukup
│   │   └── checklist.py     # empat butir bukti paper run dari log, jurnal, ledger
│   └── cli.py
└── tests/
```

## Komponen

### ExchangeAdapter (interface)

Semua kode lain hanya bicara ke interface ini, tidak pernah langsung ke ccxt. Ini yang bikin pindah antara testnet, paper, dan live jadi sekadar ganti config, dan bikin pindah exchange jadi nulis satu file, bukan tulis ulang bot. Pindah dari Binance ke Tokocrypto menguji itu: yang ditulis adalah satu adapter baru, satu kelas dasar bersama, factory, dan skema config dua venue. Strategy, risk, dan backtest belum ada saat itu, jadi belum bisa diklaim tidak tersentuh.

Method: connect, fetch_server_time_ms, fetch_market_limits, fetch_ohlcv, fetch_ticker, fetch_balance, create_order, cancel_order, cancel_all_orders, fetch_open_orders, fetch_order.

Tiga implementasi:

* CcxtAdapter untuk Binance. Sandbox on/off murni dari mode.
* TokocryptoAdapter untuk venue live, dengan penanganan khusus yang dijelaskan di Keputusan Desain.
* PaperAdapter yang membaca harga asli Tokocrypto tapi order-nya hanya dicatat di memori dan dipersist ke file.

PaperAdapter dibutuhkan karena venue develop dan live berbeda, dan karena orderbook testnet Binance tipis dan harganya kadang menyimpang jauh dari pasar asli. Testnet bagus untuk menguji apakah kode jalan, buruk untuk menguji apakah strateginya masuk akal, dan sama sekali tidak menunjukkan perilaku Tokocrypto.

Adapter harus menangani rate limit (enableRateLimit di ccxt) dan retry dengan backoff untuk error jaringan sementara. Error autentikasi, saldo, order tidak valid, dan jam melenceng tidak boleh di-retry, harus langsung berhenti. create_order tidak pernah di-retry: jawaban yang hilang berarti order mungkin sudah masuk.

### Strategy (interface)

Satu method: terima DataFrame OHLCV, kembalikan signal (LONG, FLAT, atau SHORT kalau nanti perlu). Strategi tidak boleh tahu soal ukuran posisi, saldo, atau exchange. Itu urusan risk manager.

Strategi awal: EMA crossover. EMA cepat di atas EMA lambat jadi LONG, selain itu FLAT (sama tinggi berarti FLAT). Periodenya dari config, jangan hardcode.

Jendela tetap. EMA bergantung pada seluruh histori, jadi nilainya hanya identik antara backtest dan live kalau keduanya menghitung dari jendela yang sama. Setiap strategi menyatakan lookback_bars, dan sinyalnya adalah fungsi murni dari lookback_bars bar terakhir yang diberikan: bar sebelumnya diabaikan, dan kalau bar yang tersedia kurang dari itu sinyalnya FLAT. Untuk EMA crossover, lookback_bars = slow_period x strategy.lookback_multiplier (default 5, bobot EMA yang terbuang di luar jendela sekitar e^(-10)). Backtest dan runner sama-sama boleh memberi lebih dari itu; hasilnya tidak berubah.

### RiskManager

* Position sizing berbasis persentase equity, bukan jumlah tetap
* Batas maksimum satu posisi
* Batas kerugian harian, dihitung dari equity awal hari itu
* Stop loss dan take profit sebagai persentase dari harga masuk
* Cek minimum notional exchange sebelum order
* Semua kill switch di aturan keras nomor 4

### Backtest engine

Harus event-driven bar per bar, bukan vectorized. Alasannya: nanti logika live pakai kode strategy dan risk yang sama persis, jadi perilakunya harus identik.

Wajib hindari lookahead bias: keputusan di bar N hanya boleh pakai data sampai bar N-1 close, eksekusi di open bar N plus slippage.

Output metrik: total return, max drawdown, sharpe ratio, win rate, profit factor, jumlah trade, rata-rata durasi posisi, total biaya yang terbayar per komponen, dan biaya all-in per putaran yang dipakai.

Tampilkan juga hasil buy-and-hold di periode yang sama sebagai pembanding. Kalau strategi kalah dari buy-and-hold, saya mau langsung lihat itu.

Aturan eksekusi yang dipakai engine: order dieksekusi di open bar N setelah keputusan dari bar 0..N-1; stop lapis 1 dicek dari low dan high bar N termasuk untuk posisi yang baru dibuka di bar itu, dan kalau stop dan take profit tembus dalam satu bar diambil stop loss karena urutannya tidak diketahui. Kalau open bar sudah melewati level (harga gap), fill memakai open, bukan level, karena bot live akan market-sell di sekitar open; fill di harga yang tidak pernah ada di bar itu akan memperkecil kerugian secara sistematis. Di akhir data posisi terbuka ditutup di close terakhir dengan biaya penuh.

Warmup dan pembanding: strategi baru bisa memberi sinyal setelah lookback_bars bar, jadi bar pertama yang bisa ditransaksikan adalah bar ke-lookback_bars, dan buy-and-hold masuk di bar yang sama dengan aturan dan biaya yang sama: beli seluruh equity di open bar itu, jual di close terakhir. Perintah backtest menyertakan lookback_bars bar sebelum --start sebagai warmup supaya periode yang diminta diperdagangkan penuh; kalau histori sebelum --start tidak cukup, bar pertama yang diperdagangkan bergeser dan itu dilaporkan sebagai peringatan, bukan disamarkan. Laporan selalu menyebut bar pertama yang diperdagangkan.

Batas rugi harian di backtest: equity mark-to-market di close tiap bar diperiksa RiskManager yang sama dengan live. Melewati batas berarti posisi dijual di open bar berikutnya (kalau risk.flatten_on.daily_loss) dan tidak ada posisi baru sampai hari UTC berikutnya, pendekatan backtest untuk "bot berhenti, operator menjalankannya lagi besok". Jumlah kejadiannya dicetak di laporan. Kill switch lain adalah kejadian runtime tanpa padanan di backtest.

Bar bolong: bar yang datang setelah lubang data tidak mendapat keputusan strategi, sama seperti runner live yang melewati iterasi atas bar stale atau bolong; stop lapis 1 tetap dicek. Lama posisi dihitung dari selisih waktu, bukan jumlah baris, dan jumlah bar setelah lubang dicetak di laporan. Backtest dari CLI tidak menyentuh jaringan dan tidak punya batas pasar exchange, jadi sizing tidak dibulatkan ke step dan minimum notional tidak dicek; engine menerima MarketLimits kalau pemanggil punya.

Sharpe: kalau holding period panjang, menganualisasi Sharpe dari return per bar 1h secara statistik meragukan, karena return antar bar dalam satu posisi saling bergantung. Yang benar adalah menganualisasi dari seri return pada frekuensi rebalance atau frekuensi keputusan strategi; untuk EMA crossover frekuensi keputusannya memang per bar, jadi laporan memakai return per bar dan selalu menyebut basis anualisasinya (backtest.bars_per_year, divalidasi terhadap timeframe). Angka Sharpe di laporan bersifat indikatif, bukan uji statistik.

## Urutan Build

Kerjakan berurutan. Setiap tahap harus punya test yang lulus sebelum lanjut.

1. Setup project, config loader, logging, .env handling, .gitignore. Selesai.
2. ExchangeAdapter interface + CcxtAdapter untuk Binance testnet + TokocryptoAdapter untuk venue live. Test: fetch OHLCV dan saldo testnet, satu putaran limit order jauh dari harga di testnet (muncul di open orders dengan client id, dibatalkan, hilang), data publik Tokocrypto dari host resmi, dan terbukti menolak jalan kalau config minta mainnet tanpa dua syarat mode live. Selesai, kecuali test testnet yang menunggu kunci.
3. Data fetcher historis + cache parquet, dari data publik Tokocrypto. Test: download 1 tahun data BTC/USDT 1h, verifikasi tidak ada bar bolong selain downtime yang tercatat. Selesai. Test unit memakai klien palsu dengan lubang yang diketahui posisinya (paginasi, bar berjalan, grid mingguan, kebijakan gap, data rusak dari venue, gangguan jaringan di tengah unduhan, inkremental, tulis atomik dan penulis bersamaan); test network mengunduh satu tahun penuh ke folder sementara, menanyakan ulang setiap gap yang dilaporkan langsung ke exchange supaya bar yang dijatuhkan fetcher sendiri ketahuan, dan mencocokkan laporan gap di parquet dengan isinya.
4. Strategy interface + EMA crossover. Test: pakai data buatan dengan crossover yang sudah diketahui posisinya, pastikan signal muncul persis di bar yang benar. Selesai. Test memakai EMA acuan yang ditulis sebagai rekursi polos, terpisah dari pandas, plus data lompatan dan bentuk V; juga membuktikan sinyal adalah fungsi murni dari jendela tetap.
5. Backtest engine + metrik. Test: strategi dummy yang selalu FLAT harus menghasilkan return 0 dan 0 trade. Strategi yang selalu LONG harus mendekati buy-and-hold dikurangi biaya. Selesai, bersama RiskManager minimal (sizing, minimum notional, stop lapis 1) yang interface-nya sudah final; tahap 6 melengkapi kill switch di kelas yang sama. Test tambahan: strategi mata-mata membuktikan keputusan bar N hanya melihat bar sampai N-1, eksekusi di open bar N, stop dan take profit dari high/low bar dengan fill di harga stop plus slippage, keduanya tembus dalam satu bar dihitung stop loss, biaya per komponen, --stress menggandakan fee/bursa/slippage tapi bukan pajak, posisi terbuka ditutup di close terakhir, sizing di bawah minimum notional menghentikan backtest.
6. RiskManager + semua kill switch. Test: setiap kondisi kill switch dipicu secara sintetis dan terbukti menghentikan bot; sizing di bawah minimum notional menghentikan bot dengan pesan jelas. Selesai. Setiap pemicu melempar KillSwitchTriggered yang membawa keputusan flatten dari risk.flatten_on; equity awal hari UTC dipersist di live.state_path dan terbukti selamat dari restart; jendela order satu menit bergeser; satu koneksi sukses mereset hitungan gagal; file STOP dicek tiap iterasi. Batas rugi harian juga berlaku di backtest lewat RiskManager yang sama: posisi dijual di open bar berikutnya dan tidak ada posisi baru sampai hari UTC berikutnya.
7. PaperAdapter di atas harga Tokocrypto + live runner di mode paper. Jalankan minimal beberapa hari, bandingkan dengan hasil backtest di periode sama. Kode selesai; menjalankannya beberapa hari adalah pekerjaan operator. PaperAdapter mengeksekusi dengan aturan backtest (ask atau bid plus slippage, fee per sisi, respons memuat fee) dan akunnya dipersist. Runner: file STOP, harga dan saldo, batas rugi harian, lalu satu keputusan per bar tutup (bar berjalan dibuang, warmup, stale, dan lubang seperti backtest), BARU stop lapis 1 dari harga; urutan keputusan-lalu-stop ini sama dengan backtest, kalau dibalik posisi yang keluar karena stop langsung masuk lagi di bar yang sama. Stale berarti exchange belum memberi bar yang seharusnya sudah tutup lebih lama dari toleransi, bukan bot yang terlambat: bot yang mulai jam :10 tetap memutuskan bar yang tutup jam :00. Bar terakhir yang diputuskan dipersist bersama catatan posisi, jadi restart di bar yang sama tidak memutuskan ulang. Jual yang tidak terisi tidak menghapus catatan posisi; terisi sebagian mengurangi jumlahnya. Fee yang dipotong dari base (Binance tanpa BNB) dikurangkan dari jumlah posisi. Posisi tanpa catatan direkonstruksi dari saldo dengan harga masuk dari pembelian terakhir di ledger yang belum dijual, dan hanya kalau tidak ada dari harga sekarang, dengan peringatan bahwa stop efektif melebar. Saat kill switch, order terbuka dibatalkan DULU baru flatten, karena stop lapis 2 mengunci aset. Setiap order lewat before_order, jurnal intent, kirim, jurnal result, ledger; jawaban hilang dicari lewat client_order_id dan tidak pernah dikirim ulang; saat start intent yang belum tertutup direkonsiliasi dan catatan posisi dicocokkan dengan saldo. Test paritas: runner paper dan run_backtest menghasilkan trade yang sama (sisi, bar, harga) untuk data yang sama, dan selisih harga isi per trade dicatat dengan tanda: nol untuk fill bersinyal, merugikan untuk stop loss karena paper mengisi di harga tick. Perintah compare-paper melakukan hal yang sama atas ledger paper sungguhan dan menyatakan bias satu arah (exit code 7) kalau pangsa fill yang merugikan mencapai live.bias_adverse_share dari minimal live.bias_min_trades pasangan. Perintah paper-checklist memeriksa dari log, jurnal, dan ledger apakah restart di tengah posisi, kegagalan jaringan yang pulih, kill switch dengan exit code 6, dan trade yang tertelusuri sudah terlihat; paper selesai diukur dari itu, bukan dari jumlah hari.
8. Mode live di Tokocrypto. Kodenya sudah ditulis dan diuji dengan klien palsu, TIDAK diaktifkan: selain TRADING_MODE=live dan flag, config live.enabled harus true, dan preflight harus lulus sebelum loop dimulai. Order pertama dipaksa ke ukuran minimum exchange lewat penanda live.stage_path ("minimum"), dan naik ke ukuran normal hanya lewat perintah live-size --normal yang ditolak sebelum live.min_cycles_before_normal siklus masuk-keluar selesai. Preflight (juga perintah tersendiri): live.api_key_verified_date ada dan tidak lebih tua dari live.max_key_age_days, dengan pesan yang menyebut apa yang harus diperiksa di halaman API Management Tokocrypto (withdrawal mati, pembatasan IP kalau tersedia); kunci bisa membaca saldo, dan hanya itu, withdrawal tidak pernah dicoba; pasangan ada di load_markets, minimum notional terbaca, sizing dan ukuran minimum di atasnya; jam lewat pengukuran yang diperbaiki; venue mengiklankan STOP_LOSS_LIMIT. Lapis 2 dan urutan batalkan-stop-sebelum-keluar ada di Keputusan Desain. Ledger dua fase tetap berlaku: bot tidak pernah menyatakan ledger lengkap selama ada baris pending, dan merekonsiliasinya saat start dan setelah setiap fill. Pengaktifannya lewat jalur trial live kecil di Keputusan Desain: live.enabled hanya ditulis ke config/local.yaml oleh live-start setelah preflight lulus dan pemilik mengetik SAYA SIAP, dan dikembalikan ke false oleh live-stop.

## Catatan Kepatuhan (bukan tugas coding, tapi jangan dihapus)

Tokocrypto (PT Aset Digital Berkat) berizin OJK sebagai Pedagang Aset Keuangan Digital, S-14/D.07/2025 tanggal 1 Februari 2025, tercantum di daftar penyelenggara OJK per April 2026. Sejak 18 Juni 2026 keanggotaan bursanya di PT Fortuna Integritas Mandiri (ICEx) dan kliringnya di PT Pranata Karya Solusi, keduanya berizin OJK. Beban pelaporan saya lebih ringan dari skenario Binance:

* Ada regulator Indonesia yang mengawasi kalau ada sengketa. Tetap tidak ada jaminan dana seperti simpanan bank.
* Pajak dipotong di sumber oleh Tokocrypto: PPh 22 final 0,21% atas penjualan aset kripto, dan di pasangan USDT/kripto dikenakan di kedua sisi. PPN atas aset kripto dihapus sejak 1 Agustus 2025 (PMK 50/2025). Sejak 1 Januari 2026 exchange melaporkan data agregat pengguna ke DJP (PMK 108/2025), jadi aktivitas bot ikut terlapor. Pelaporan tahunan tetap kewajiban saya.
* Off-ramp ke IDR lewat penarikan bank biasa, bukan P2P.

Ledger CSV tetap wajib dan tetap append-only, karena saya tetap butuh catatan sendiri untuk rekonsiliasi dengan laporan Tokocrypto. Kolom: timestamp, pair, sisi, jumlah, harga, nilai dalam quote currency, fee, fee currency, order id, client order id, status fee, dan waktu pencatatan. File ini bukan log debug; harus rapi, berurutan, dan tidak pernah ditimpa.

Ledger bekerja dua fase karena respons order Tokocrypto tidak memuat fee. Fase pertama: begitu order terisi, barisnya ditulis segera dengan fee kosong dan status pending. Fase kedua: rekonsiliasi lewat fetch_my_trades menjumlahkan fee per order per mata uang dan menambahkan baris baru berstatus reconciled; baris lama tidak pernah diubah. Fee dalam lebih dari satu mata uang, misalnya karena diskon TKO, dicatat lengkap per komponen dengan pemisah "|" di kolom fee dan fee_currency, tanpa konversi ke satu mata uang; konversi adalah urusan pelaporan pajak. Perintah ledger-status melaporkan berapa order yang masih pending, dan bot tidak pernah menyatakan ledger lengkap selama ada order yang fee-nya benar-benar belum diambil dari exchange.

Sumber: lisensi dari daftar OJK "Daftar Penyelenggara Perdagangan Aset Keuangan Digital Posisi 21 April 2026" dan siaran pers OJK SP 226/GKPB/OJK/XII/2025 (19 Desember 2025); migrasi bursa dan kliring dari pengumuman Tokocrypto "Pemeliharaan Sistem dan Migrasi Pengalihan Keanggotaan Bursa dan Kliring" (11 Juni 2026); pajak dari artikel DJP tentang PMK 50/2025 (31 Juli 2025) dan pengumuman Tokocrypto tentang penerapannya (31 Juli 2025); migrasi 31 pair IDR dari "API Integration Alert: IDR Migration Impacting 31 Trading Pairs" (14 November 2025). Semua diakses 12 September 2026.

## Definition of Done per Tahap

* Ada test yang lulus
* Tidak ada nilai hardcode yang seharusnya di config
* Log cukup jelas untuk saya baca tanpa buka kode
* Kamu jelaskan dalam 3 kalimat apa yang bisa gagal di tahap ini dan bagaimana saya tahu kalau itu terjadi

## Yang Tidak Boleh Dilakukan

* Jangan bikin dashboard, web UI, atau notifikasi Telegram sebelum tahap 7 selesai. Itu pengalih perhatian dari bagian yang benar-benar menentukan.
* Jangan optimasi parameter strategi sampai overfit ke data historis. Kalau saya minta parameter sweep, ingatkan saya soal ini dan pakai walk-forward validation. Aturan lengkapnya di RESEARCH.md.
* Jangan tambah indikator baru tanpa saya minta.
* Jangan pernah bilang strategi ini "profitable" berdasarkan backtest saja.
