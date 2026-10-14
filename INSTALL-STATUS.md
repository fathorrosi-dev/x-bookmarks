# Pemasangan X Bookmarks 3.1.1

Paket 3.1.1 siap dipasang setelah seluruh tes lokal dan CI Ubuntu 24.04/Python 3.10, 3.13, 3.14 lulus. Paket ini hanya berisi program, contoh settings, dokumentasi, bootstrap pip, dan tes.

Untuk pemasangan pertama dari 3.0.2, jalankan updater dari paket baru:

```bash
bash update.sh "$HOME/tools/x-bookmarks" --wait
```

Program sebelumnya dicadangkan di `.state/program-backups/`; settings, snapshot, pilihan bookmark, arsip, dan media dipertahankan. Setelah terpasang, jalankan:

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks status --plain
./x-bookmarks update --check
```

Dokumen dalam paket rilis merupakan petunjuk pemasangan umum dan tidak memuat data perangkat. Catatan verifikasi untuk perangkatmu sendiri (lokasi pemasangan, versi OS dan Python, jumlah media) simpan di `CATATAN-PERANGKAT.md` pada folder alat. Berkas itu diabaikan Git, tidak masuk `release.json`, dan tidak ikut paket.

Untuk memeriksa versi, gunakan `./x-bookmarks status --plain` atau `./x-bookmarks update --check`; CLI ini tidak memiliki opsi `--version`. Perintah `--version` pada catatan pemasangan awal paket rilis digantikan oleh `status --plain` pada dokumentasi lokal ini.
