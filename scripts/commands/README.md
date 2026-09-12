# File .command untuk Finder

Pemilik menjalankan bot dari Finder, bukan terminal. File `.command` di root repo
dikecualikan lewat `.git/info/exclude` dan TIDAK di-commit; yang di-commit adalah
template di folder ini dan pemasangnya. Pasang atau perbarui dengan:

```bash
scripts/install-commands.sh
```

Pemasang tidak menimpa file yang sudah ada kecuali diberi `--force`. Setiap
`.command` pindah ke folder repo, menjalankan satu perintah lewat `uv run`,
lalu menunggu tombol ditekan supaya jendelanya tidak langsung tertutup.
