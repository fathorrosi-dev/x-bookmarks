# Pemasangan X Bookmarks 3.1.0

Paket 3.1.0 siap dipasang setelah seluruh 45 tes lokal dan CI Ubuntu 24.04/Python 3.10, 3.13, 3.14 lulus. Paket ini hanya berisi program, contoh settings, dokumentasi, bootstrap pip, dan tes.

Untuk pemasangan pertama dari 3.0.2, jalankan updater dari paket baru:

```bash
bash update.sh "$HOME/tools/x-bookmarks" --wait
```

Program sebelumnya dicadangkan di `.state/program-backups/`; settings, snapshot, pilihan bookmark, arsip, dan media dipertahankan. Setelah terpasang, jalankan:

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks --version
./x-bookmarks update --check
```

Dokumen dalam paket rilis merupakan petunjuk pemasangan umum. Catatan verifikasi perangkat pengguna ditambahkan pada salinan lokal setelah pemasangan; belum merupakan klaim bahwa paket baru telah dipasang pada perangkat lain.
