# tradebot

Bot trading crypto spot untuk satu pasangan, BTC/USDT, dibangun sebagai latihan engineering sistem yang bisa diuji, bukan sebagai mesin cari untung. Strategi awalnya EMA crossover 20/50 pada bar 1 jam dan diasumsikan rugi; seluruh sistem dirancang supaya kerugian itu murah, terukur, dan bisa dihentikan. Hasil backtest tidak pernah menjadi dasar untuk menyebut strategi ini menguntungkan.

Yang dibangun: fetcher data historis dengan cache parquet dan laporan gap, backtest event-driven tanpa lookahead dengan buy-and-hold sebagai pembanding, RiskManager dengan sizing, stop lapis 1, dan empat kill switch, PaperAdapter yang mengeksekusi di atas harga asli, runner dengan jurnal order write-ahead, ledger dua fase untuk pajak, dan perintah operasional untuk menjalankan paper berhari-hari lalu membandingkannya dengan backtest. Spesifikasi dan semua keputusan desain ada di SPEC.md, protokol riset strategi di RESEARCH.md. File ini menjelaskan cara memasang, menjalankan, dan apa yang belum selesai.

Tiga mode: paper (default, tanpa kunci, harga asli Tokocrypto, eksekusi disimulasikan), testnet (Binance Spot Testnet, uang palsu, hanya untuk menguji jalur order), dan live (Tokocrypto mainnet, uang asli; kodenya ada tetapi ditolak jalan sampai pemiliknya menyatakan siap).

| Hal | Nilai |
|---|---|
| Venue live | Tokocrypto, berizin OJK, pasangan BTC/USDT, buku Binance yang dibagi |
| Venue develop | Binance Spot Testnet |
| Biaya per sisi | taker 0,15% + PPh 22 final 0,21% + biaya bursa ICEx 0,0444% = 0,4044%, plus asumsi slippage 0,15% |
| All-in per putaran masuk-keluar | sekitar 1,11% |
| Konsekuensi | strategi harus mengalahkan 1,11% per putaran sebelum dibandingkan dengan buy-and-hold; frekuensi tinggi mati oleh biaya |

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

Kalau jam mesin melenceng lebih dari batas di config, perintah gagal dengan pesan yang menyebut selisihnya, rtt terbaik, dan jumlah sampel; sinkronkan jam sistem lalu ulangi. Kalau yang muncul adalah "pengukuran jam tidak konklusif", jam Anda tidak salah: jaringan terlalu lambat untuk mengukurnya (rtt terbaik terlalu besar), coba lagi saat koneksi lebih stabil. Pengukuran memakai satu panggilan pemanasan yang dibuang lalu beberapa sampel dengan rtt terkecil, jadi koneksi dingin tidak lagi dinilai sebagai jam yang buruk. Kalau pair di config tidak ada lagi di venue, perintah gagal dengan pesan yang menyebut pair dan jumlah pasar per quote yang tersedia.

Cek ledger trade: jumlah baris dan order yang fee-nya masih pending. Exit code bukan nol selama ada yang pending.

```bash
uv run tradebot ledger-status
```

Unduh data historis ke cache parquet. Sumbernya selalu data publik venue live (Tokocrypto) dan tidak butuh kunci: perintah ini mengabaikan TRADING_MODE dan kunci di .env, selalu berjalan sebagai paper, dan tidak bisa mengirim order. Rentang default dari data.history_start di config sampai bar yang sedang berjalan menurut jam server; bar yang belum tutup tidak pernah disimpan, termasuk bar mingguan yang gridnya Senin, bukan grid epoch. Perintah ini inkremental: menjalankannya lagi hanya mengambil bar yang belum ada, plus bar terakhir yang diambil ulang untuk memastikan nilainya final; kalau belum ada bar baru yang mungkin, tidak ada permintaan dan cache tidak ditulis ulang.

```bash
uv run tradebot fetch-data
uv run tradebot fetch-data --start 2025-06-01 --end 2025-09-01
```

Output menyebut jumlah bar di cache, bar baru, jumlah permintaan, dan setiap gap. Fetcher tidak pernah mengarang bar: bar yang hilang dilaporkan di layar, di log, di metadata parquet (acuan, ditulis bersama bar-nya), dan di salinan .gaps.json di samping parquet untuk dibaca orang. Gap yang lebih panjang dari data.max_gap_bars dianggap data rusak, bukan downtime: perintah berhenti dengan exit code 5 dan tidak menulis apa pun. Kalau gap itu memang downtime exchange yang terkonfirmasi, naikkan data.max_gap_bars, lalu jalankan lagi. Data yang rusak dari venue (harga kosong, bar yang bergeser dari grid) juga exit code 5 tanpa menulis apa pun. Kalau data dimulai lebih lambat dari tanggal yang diminta, itu dilaporkan sebagai peringatan, bukan gap, karena pair bisa saja baru tercatat setelah tanggal itu.

Menarik histori jauh ke belakang, misalnya sejak Agustus 2017 untuk riset di RESEARCH.md, hampir pasti menabrak jendela pemeliharaan Binance yang lebih panjang dari enam jam (buku BTC/USDT Tokocrypto adalah buku Binance yang dibagi). Itu memang dirancang berhenti: cocokkan gap yang disebut di pesan error dengan pengumuman pemeliharaan, naikkan data.max_gap_bars secara sadar, dan catat keputusannya. Ini belum pernah dijalankan; angka lubangnya belum diketahui.

Jalankan backtest dari cache parquet, tanpa jaringan. Strategi, risk, dan biaya dari config; buy-and-hold dengan biaya yang sama selalu ditampilkan, berikut biaya yang terbayar per komponen dan alasan keluar tiap posisi. Sharpe dianualisasi dari return per bar dan laporan menyebut basisnya.

```bash
uv run tradebot backtest
uv run tradebot backtest --stress
uv run tradebot backtest --start 2026-01-01 --end 2026-07-01
```

`--stress` menggandakan fee, biaya bursa, dan slippage dengan costs.stress_multiplier; pajak tetap. Kalau strategi hanya untung di angka default, laporan --stress yang memberi tahu. Cache yang belum ada berarti exit code 5 dengan pesan untuk menjalankan fetch-data dulu.

Strategi butuh jendela warmup (250 bar untuk EMA 20/50 dengan pengali 5) sebelum bisa memberi sinyal. Perintah backtest menyertakan bar sebanyak itu sebelum --start, dan buy-and-hold masuk di bar pertama yang bisa diperdagangkan strategi, bukan di bar pertama data, supaya pembandingnya adil. Kalau histori sebelum --start tidak cukup, bar pertama yang diperdagangkan bergeser dan ada peringatan di stderr. Bar yang datang setelah lubang data tidak mendapat keputusan strategi, sama seperti runner live nanti, dan jumlahnya dicetak di laporan.

Jalankan loop trading. Di mode paper (default) harga dari Tokocrypto, eksekusi disimulasikan, dan akun paper disimpan di state/paper_account.json dengan saldo awal backtest.initial_equity. Di mode testnet order sungguhan masuk ke Binance Spot Testnet. Mode live butuh tiga hal sekaligus: TRADING_MODE=live di .env, flag --i-know-what-im-doing, dan live.enabled true di config/local.yaml (overlay lokal yang ditulis `tradebot local-set`, tidak di-commit); lalu preflight harus lulus sebelum loop dimulai. Selama live.enabled false, run menolak dengan pesan yang menyebut syaratnya.

Catatan tiap mode terpisah: paper menulis ke state/, trades/, dan logs/ seperti biasa; live dan testnet ke state/live/ (atau state/testnet/), trades/live/, dan logs/live/. Placeholder {mode_dir} di path config yang mengaturnya, supaya posisi, jurnal, atau ledger simulasi tidak pernah terbaca sebagai uang asli. File STOP tetap satu untuk semua mode.

Di live, order pertama dipaksa ke ukuran minimum exchange, bukan hasil sizing, sampai Anda menaikkannya secara sadar:

```bash
uv run tradebot live-size
uv run tradebot live-size --normal
```

Perintah kedua ditolak sebelum live.min_cycles_before_normal siklus masuk-keluar selesai di ukuran minimum. Di testnet dan live, stop lapis 2 dipasang di exchange segera setelah posisi terbentuk, lebih lebar dari stop bot, dan dibatalkan dulu sebelum order keluar; paper tidak mensimulasikannya.

Periksa kesiapan live kapan saja tanpa menjalankan bot dan tanpa mengirim order:

```bash
uv run tradebot preflight
```

Preflight memeriksa tanggal terakhir Anda memverifikasi izin kunci di halaman API Management Tokocrypto (live.api_key_verified_date, maksimal live.max_key_age_days hari), kunci bisa membaca saldo, pasangan dan minimum notional, sizing, jam, dan dukungan stop order. Exit code 9 kalau ada yang gagal.

```bash
uv run tradebot run
uv run tradebot run --iterations 10
```

Loop berhenti sendiri hanya karena kill switch (exit code 6) atau error fatal. Setiap iterasi: cek file STOP, ambil harga dan saldo, cek batas rugi harian, cek stop lapis 1 dari harga, lalu satu keputusan per bar yang sudah tutup. Bar yang masih berjalan tidak pernah dipakai; bar yang stale atau datang setelah lubang data dilewati dan dicatat di log. Setiap order dicatat ke state/orders.jsonl sebelum dikirim; saat start, order yang jawabannya hilang dicari lewat client order id dan tidak pernah dikirim ulang. Catatan posisi (harga masuk, stop, target) dan bar terakhir yang sudah diputuskan ada di state/position.json, dicocokkan dengan saldo saat start; kalau catatan hilang, harga masuk diambil dari pembelian terakhir di ledger. Bot yang mulai di tengah jam tetap memutuskan bar yang baru tutup; yang disebut stale hanya bar yang belum diberikan exchange lebih lama dari live.stale_bar_tolerance_seconds. Jalankan paper beberapa hari, lalu bandingkan dengan backtest di periode yang sama lewat dua perintah di bawah.

## Menjalankan dari Finder (file .command)

Perintah operasional tersedia sebagai file `.command` di root repo yang bisa dibuka dua kali klik dari Finder: status, preflight, check-exchange, ledger-status, paper-start, paper-stop, paper-checklist, compare-paper, live-setup, live-start, live-stop, live-size, backtest, fetch-data, tests (check-config, run, dan local-set tetap dari Terminal). File-file itu TIDAK di-commit (dikecualikan lewat `.git/info/exclude`); yang di-commit adalah pemasangnya. Perintah yang membaca catatan per mode (status, preflight, checklist, compare, live-size) menambahkan flag --i-know-what-im-doing sendiri kalau .env berisi TRADING_MODE=live, tanpa pernah membaca nilai kunci. Pasang atau tambah yang hilang dengan:

```bash
scripts/install-commands.sh
```

Pemasang melewati file yang sudah ada kecuali diberi `--force`. Kalau macOS menolak membuka, klik kanan lalu Open sekali. Detail di scripts/commands/README.md.

## Menjalankan paper berhari-hari di macOS

Pilihan: LaunchAgent launchd yang menjalankan skrip supervisor, bukan nohup, karena launchd hidup lagi setelah login dan supervisor bisa memulai ulang bot yang mati karena error sementara tanpa pernah menimpa keputusan kill switch. Bot dibungkus `caffeinate -i -s` supaya Mac tidak tidur karena idle selama tersambung listrik, tetapi menutup tutup laptop TETAP membuat Mac tidur; caffeinate tidak bisa mencegahnya, jadi kalau tutupnya ingin ditutup, jalankan sekali `sudo pmset -a disablesleep 1` saat di listrik (kembalikan dengan `sudo pmset -a disablesleep 0`), atau biarkan tutupnya terbuka.

Mulai (mengunduh data dulu, lalu memasang dan menjalankan agent):

```bash
scripts/paper-start.sh
```

Cek keadaan, satu perintah:

```bash
uv run tradebot status
```

Ikuti log langsung:

```bash
tail -f logs/tradebot.log
tail -f logs/paper.out
```

Hentikan dengan rapi (membuat file STOP, menunggu bot berhenti lewat kill switch, melepas agent, lalu menghapus STOP):

```bash
scripts/paper-stop.sh
```

Yang perlu diketahui: supervisor memulai ulang bot setelah 60 detik kalau keluar dengan exit code selain 0, 2 (config), 6 (kill switch), dan 9 (preflight live gagal), paling banyak 10 kali; setelah berhenti tanpa mulai ulang ia melepas agent-nya dari launchd (dan untuk live mengembalikan live.enabled ke false), jadi login berikutnya tidak menghidupkan bot tanpa persetujuan; dan `tradebot status` selalu menampilkan berapa kali mulai ulang sudah terjadi, kapan terakhir, dan exit code terakhirnya (dari state/paper_supervisor.json), karena mulai ulang otomatis bisa menyembunyikan bug yang berulang. Kalau bot berhenti karena kill switch (exit code 6), supervisor ikut berhenti dan tidak memulai ulang; `tradebot status` menunjukkan baris "BOT BERHENTI" dan pemicunya. Setelah Mac tidur dan bangun, bot melanjutkan: bar yang terlewat ditandai stale dan dilewati, dan kalau jaringan butuh lebih dari beberapa iterasi untuk pulih, kill switch gagal koneksi bisa menyala; itu perilaku yang disengaja, jalankan `scripts/paper-start.sh` lagi. Nama agent-nya `com.tradebot.paper`; `launchctl print gui/$(id -u)/com.tradebot.paper` menunjukkan keadaannya. PID supervisor ada di state/paper_supervisor.pid. Skrip yang sama dipakai mode live dengan nama lain: `scripts/bot-start.sh live` memasang `com.tradebot.live` yang menjalankan `scripts/bot-supervisor.sh live`, PID di state/live_supervisor.pid, log supervisor di logs/live.out, log bot di logs/live/tradebot.log. Mode dipaksa lewat environment launchd, jadi paper tetap paper walau .env sudah berisi TRADING_MODE=live. Paper dan live tidak dijalankan bersamaan; skrip start menolak kalau supervisor yang lain masih hidup.

## Trial live kecil di Tokocrypto (dari Finder)

Jalur ini melewati paper run: modal kecil di akun asli menjadi pengganti paper, dengan kerugian maksimum dibatasi saldo yang disetor. Yang belum pernah teruji di venue asli sebelum jalur ini adalah keempat butir checklist; trial inilah yang mengujinya, dengan uang sungguhan. Urutannya:

1. Di aplikasi Tokocrypto: buat API key baru khusus bot (spot trading saja, withdrawal MATI, pembatasan IP kalau tersedia). Kunci yang pernah ditempel ke chat atau dokumen apa pun dihapus, bukan dipakai. Beli USDT dengan IDR di pasangan USDT/IDR sebesar modal trial; bot hanya memperdagangkan BTC/USDT dan tidak menyentuh IDR.
2. `live-setup.command`: meminta key dan secret tanpa menampilkannya (hanya ditulis ke .env), menanyakan tiga hal dari halaman API Management, mencatat tanggal verifikasi dan pecahan posisi trial ke config/local.yaml, lalu menjalankan preflight tanpa order.
3. `live-start.command`: preflight lagi, fetch-data (kalau jaringan mati, berhenti di sini sebelum apa pun diaktifkan), minta ketik SAYA SIAP, set live.enabled=true lewat `tradebot local-set`, lalu memasang LaunchAgent `com.tradebot.live`; kalau pemasangan gagal, live.enabled dikembalikan ke false. `tradebot local-unset section.key` menghapus key dari overlay dan tetap jalan saat overlay rusak. Order pertama berukuran minimum exchange sampai `live-size.command` menunjukkan siklus cukup dan Anda menjalankan `uv run tradebot live-size --normal --i-know-what-im-doing`.
4. `status.command` kapan saja; `live-stop.command` untuk berhenti (posisi yang dipegang tidak dijual otomatis; stop lapis 2 di exchange tetap terpasang selama bot mati, dan live.enabled dikembalikan ke false).

Yang tidak pernah terjadi di jalur ini: kunci muncul di layar, log, argumen proses, atau file selain .env; order dikirim sebelum preflight lulus; live aktif tanpa ketikan SAYA SIAP.

Menjalankan pytest lengkap termasuk test jaringan di Mac:

```bash
uv run pytest
```

## Kapan paper run selesai

Paper selesai bukan diukur dari jumlah hari, tapi dari apa yang sudah terlihat. Keempat butir ini diperiksa otomatis dari log, jurnal, dan ledger, dengan buktinya:

- satu restart di tengah posisi terbuka, pulih tanpa order ganda
- satu kegagalan jaringan yang tertangani (gagal, lalu pulih)
- satu kill switch menyala dan berhenti dengan exit code 6
- beberapa trade yang bisa ditelusuri dari niat di jurnal sampai hasil dan sampai baris ledger (minimal live.checklist_min_fills)

```bash
uv run tradebot paper-checklist
```

Exit code 0 kalau semua terlihat, 8 kalau belum; setiap butir mencetak baris log atau id order yang menjadi buktinya. Restart bisa dipicu sendiri dengan menghentikan proses saat posisi terbuka lalu menjalankan `run` lagi; kegagalan jaringan dengan mematikan koneksi sebentar; kill switch dengan membuat file STOP.

## Membandingkan paper dengan backtest

Yang penting bukan besar selisih harga isi, tapi arahnya. Kalau paper konsisten mengisi lebih buruk dari backtest, asumsi costs.slippage_rate terlalu longgar dan setiap backtest optimis.

```bash
uv run tradebot compare-paper
uv run tradebot compare-paper --start 2026-09-15 --end 2026-09-22
```

Perintah ini menjalankan backtest di periode fill paper (dari cache, dengan warmup), memasangkan setiap fill paper dengan fill backtest pada sisi dan bar yang sama, lalu mencetak selisih bertanda per trade dalam basis poin (positif = merugikan Anda), rata-rata, simpangan, persentase yang merugikan, nilai terburuk, uji tanda binomial, dan rinciannya per alasan keluar. Fill yang tidak punya pasangan dilaporkan, bukan dibuang. Kalau dari minimal live.bias_min_trades pasangan, pangsa yang merugikan (atau menguntungkan) mencapai live.bias_adverse_share, perintah mencetak "BIAS SATU ARAH TERDETEKSI" dan keluar dengan exit code 7. Selisih pada stop loss memang diharapkan positif, karena paper mengisi di harga yang terlihat sedangkan backtest di level stop; bias pada fill bersinyal adalah yang mengubah asumsi slippage.

## Menjalankan test

```bash
uv run pytest
```

Test yang butuh kunci testnet ditandai testnet. Kalau kunci belum ada di .env, test itu dilewati, dan di akhir run selalu tercetak ringkasan berapa yang dilewati dan alasannya. Hasil hijau tanpa membaca ringkasan itu tidak berarti semuanya teruji. Salah satu test testnet memasang limit order jauh di bawah harga pasar lalu membatalkannya; uang testnet palsu, dan kalau test gagal di tengah order itu tetap dibersihkan.

Test yang memanggil endpoint publik Tokocrypto sungguhan ditandai network. Di awal sesi pytest mengecek sekali apakah host Tokocrypto terjangkau lewat jalur HTTP yang sama dengan ccxt; kalau tidak, test network ditandai GAGAL DIJALANKAN dan dilaporkan di bagian merah tersendiri, terpisah dari yang dilewati karena kunci, karena angka "passed" tidak pernah mencakupnya. Baris ringkasan pytest sendiri tetap menghitungnya sebagai skipped; bagian di atasnya yang membedakan. Salah satunya, test tahap 3, mengunduh satu tahun penuh BTC/USDT 1h ke folder sementara, lalu menanyakan ulang setiap gap yang dilaporkan langsung ke exchange: gap hanya sah kalau exchange memang tidak punya bar di rentang itu, jadi bar yang dijatuhkan fetcher sendiri akan ketahuan. Saat bekerja offline, lewati dengan:

```bash
uv run pytest -m "not network"
```

## Biaya

Default di config/default.yaml adalah biaya Tokocrypto untuk pasangan USDT, dari artikel resmi Informasi Biaya Transaksi di Tokocrypto yang berlaku sejak 18 Juni 2026: taker 0,15 persen, PPh 22 final 0,21 persen yang dipungut di kedua sisi, dan biaya bursa ICEx 0,0444 persen. Jumlahnya 0,4044 persen per sisi, ditambah asumsi slippage 0,15 persen. Satu putaran masuk-keluar sekitar 1,11 persen. Angka fee ini realistis dari exchange, bukan margin aman. Yang pesimistis adalah perintah backtest --stress, yang menggandakan fee, biaya bursa, dan slippage; pajak tidak digandakan karena angkanya pasti.

Komponen biaya bursa paling sering berubah: untuk pasangan USDT 0,0444 persen sampai Februari 2026, turun ke 0,0222 persen pada 1 Maret 2026, lalu kembali ke 0,0444 persen pada 18 Juni 2026 saat pindah bursa. Cek ulang setiap kali Tokocrypto mengumumkan penyesuaian biaya, dan perbarui komentar tanggal di config.

Semua backtest yang pernah memakai asumsi fee Binance (0,1 persen per sisi tanpa pajak dan biaya bursa) tidak berlaku lagi. Angka lamanya tinggal sebagai komentar di config.

## Jaringan

Pada jaringan tempat project ini dibangun (Biznet, September 2026), api.binance.com dan api.binance.me dijawab DNS ISP dengan alamat halaman blokir, sertifikatnya tidak cocok, dan mengganti resolver ke 1.1.1.1 tidak menolong karena permintaan DNS di port 53 ikut dibelokkan. Ini tidak lagi menjadi masalah untuk mode live karena venue live sekarang Tokocrypto. Yang dipakai bot dan semuanya bisa dijangkau: testnet.binance.vision untuk mode testnet, www.tokocrypto.com untuk endpoint akun dan order Tokocrypto, dan www.tokocrypto.site untuk data pasar Tokocrypto.

Satu hal penting soal ccxt: implementasi tokocrypto di ccxt 4.5.78 merutekan ticker, OHLCV, order book, dan trades untuk pasangan seperti BTC/USDT ke api.binance.com. Host itu bukan host resmi Tokocrypto untuk data tersebut; dokumentasi API Tokocrypto menyebut www.tokocrypto.site. Adapter mengarahkan rute itu ke host resmi lewat config exchange.live.market_data_url. Kalau field itu dikosongkan, bot memperingatkan dan data pasar akan gagal di jaringan ini.

## Struktur folder saat runtime

Folder data, logs, state, dan trades dibuat otomatis dan tidak masuk git; pola di .gitignore dijangkar ke root supaya paket sumber src/tradebot/data tidak ikut terabaikan. Folder data berisi cache OHLCV, satu file per venue, pasangan, dan timeframe, misalnya data/tokocrypto/BTC-USDT_1h.parquet; laporan gap tertanam di metadata parquet itu, dengan salinan BTC-USDT_1h.gaps.json di sampingnya untuk dibaca orang. File ditulis atomik lewat file sementara bernama unik, fsync, lalu ganti nama, jadi proses yang mati di tengah tidak meninggalkan parquet setengah jadi dan dua proses yang menulis bersamaan masing-masing menghasilkan file utuh. Folder state berisi jurnal order (ditulis sebelum order dikirim), state harian risk manager, catatan posisi, dan akun paper. Folder trades berisi CSV yang mencatat setiap trade untuk rekonsiliasi dan pajak; file ini hanya ditambah, tidak pernah ditimpa. Simpan cadangannya di luar mesin ini.

File bernama STOP di root project adalah kill switch manual. Membuat file itu menghentikan bot pada iterasi berikutnya.

## Celah yang diketahui

Ini celah yang disadari dan belum ditutup. Masing-masing menyebut apa yang membuatnya muncul.

- Rekonsiliasi order lewat client order id di Tokocrypto memindai open orders lalu 200 order terbaru tanpa startTime. Muncul kalau bot mati beberapa jam setelah mengirim order yang jawabannya hilang sementara akun terus bertransaksi; order itu bisa tidak ditemukan dan disimpulkan tidak pernah masuk. Urutan hasil endpoint riwayat belum terverifikasi dengan kunci asli. Penutupnya meneruskan startTime dari waktu niat di jurnal; belum dikerjakan.
- Backtest dari CLI tidak punya batas pasar exchange, jadi sizing tidak dibulatkan ke step dan minimum notional tidak dicek di sana; runner paper dan live memakainya. Muncul sebagai selisih kecil jumlah antara backtest dan paper.
- Stop loss di paper dan live diisi di harga yang terlihat, backtest di level stop. Selisihnya selalu ke arah yang merugikan dan terlihat di compare-paper; bukan bug, tapi sumber bias yang harus diperhitungkan.
- Data historis hanya sejak data.history_start (September 2025). Menarik ke 2017 akan menabrak jendela pemeliharaan Binance yang lebih panjang dari data.max_gap_bars dan berhenti; menaikkan batas itu melonggarkan pemeriksaan di semua periode. Daftar downtime terkonfirmasi belum ada.
- Menutup tutup laptop membuat macOS tidur walau caffeinate jalan; bot melanjutkan setelah bangun, tetapi bar yang terlewat dilewati sebagai stale dan kill switch gagal koneksi bisa menyala kalau jaringan lambat pulih.
- Paper tidak mensimulasikan stop order di exchange (lapis 2); lapis 2 hanya bekerja di testnet dan live.
- Jalur trial live kecil melewati paper run, jadi keempat butir checklist (restart di tengah posisi, gangguan jaringan, kill switch, trade tertelusuri) pertama kali teruji di venue asli dengan uang sungguhan, bukan di simulasi. Muncul karena pemilik memilih modal kecil sebagai pengganti paper; kerugian dibatasi saldo yang disetor, bukan oleh kode.
- Konversi IDR ke USDT dilakukan manual di aplikasi Tokocrypto, bukan oleh bot. Muncul karena bot hanya punya jalur order BTC/USDT yang teruji; jalur order USDT/IDR tidak ditulis supaya tidak ada order tanpa test di akun asli.
- Test testnet (8) dan test Tokocrypto berkunci (1) hanya jalan kalau kuncinya ada di .env; test network (8) hanya jalan di mesin dengan jaringan ke Tokocrypto. Ringkasan pytest menyebut ketiganya terpisah.

## Status tahap

Tahap 1 selesai: setup project, config loader, logging, penanganan .env, gitignore.

Tahap 2 selesai: interface ExchangeAdapter, CcxtAdapter untuk Binance testnet, TokocryptoAdapter untuk venue live, dan factory yang memetakan mode ke venue. Adapter mencoba ulang gangguan jaringan dengan backoff dari config, tidak pernah mencoba ulang error autentikasi atau saldo, tidak pernah mengirim ulang order yang jawabannya hilang, memvalidasi pair saat connect, dan menolak membuat klien mainnet berkunci di luar jalur mode live. Test integrasi testnet, termasuk satu putaran order sungguhan, dilewati dengan ringkasan sampai kunci testnet ada di .env.

Tahap 3 selesai: fetcher OHLCV historis dari data publik Tokocrypto, cache parquet dengan penulisan atomik, laporan gap, dan perintah fetch-data yang inkremental. Gap dilaporkan dan tidak pernah diisi; yang lebih panjang dari data.max_gap_bars menghentikan proses tanpa menulis cache. Test unit memakai klien palsu dengan lubang yang diketahui posisinya; test network mengunduh satu tahun penuh dan menanyakan ulang setiap gap ke exchange.

Tahap 4 selesai: interface Strategy dengan Signal sebagai state target, EMA crossover dengan periode dari config, dan registry strategy.name. Sinyal adalah fungsi murni dari jendela tetap slow_period x lookback_multiplier bar terakhir, supaya backtest dan live identik; sebelum jendela penuh sinyalnya FLAT. Test memakai EMA acuan yang ditulis terpisah dari pandas dan data buatan dengan crossover yang diketahui posisinya.

Tahap 5 selesai: engine backtest event-driven tanpa lookahead, metrik, laporan dengan buy-and-hold dan biaya per komponen, perintah backtest dan --stress, serta RiskManager minimal (sizing berbasis pecahan equity, minimum notional, stop dan take profit lapis 1) yang dipakai backtest dan nanti live tanpa perubahan interface.

Tahap 6 selesai: semua kill switch di RiskManager yang sama dengan backtest. Batas rugi harian dari equity awal hari UTC yang dipersist di state/risk_state.json dan selamat dari restart; runaway order dengan jendela satu menit; gagal koneksi beruntun dengan reset saat sukses; file STOP. Setiap pemicu membawa keputusan flatten dari risk.flatten_on. Batas rugi harian juga menghentikan perdagangan hari itu di backtest.

Tahap 7 kode selesai: PaperAdapter dengan akun yang dipersist, jurnal order write-ahead, runner dengan rekonsiliasi saat start, perintah run, dan test paritas yang membuktikan runner paper dan backtest menghasilkan trade yang sama untuk data yang sama. Yang belum: menjalankannya beberapa hari di jaringan sungguhan dan membandingkan dengan backtest di periode yang sama; itu pekerjaan operator sebelum tahap 8.

Tahap 8 kode selesai, TIDAK diaktifkan: gerbang tiga kunci untuk live, lapis 2 di exchange dengan urutan batalkan-sebelum-keluar dan penanganan stop yang tereksekusi saat bot mati, preflight, ukuran minimum untuk order pertama dengan penanda dan perintah live-size, ledger dua fase di live. Jalur trial live kecil dari Finder (live-setup, live-start, live-stop) dengan catatan per mode dan overlay config/local.yaml sudah ada; yang belum, dan hanya bisa dilakukan pemilik di Mac dengan akun Tokocrypto: kunci baru, USDT di akun, pemeriksaan halaman API Management, dan ketikan SAYA SIAP. Lihat SIAP-PAKAI.md.
