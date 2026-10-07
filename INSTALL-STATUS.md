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
./x-bookmarks status --plain
./x-bookmarks update --check
```

Dokumen dalam paket rilis merupakan petunjuk pemasangan umum. Catatan verifikasi perangkat pengguna ditambahkan pada salinan lokal setelah pemasangan; belum merupakan klaim bahwa paket baru telah dipasang pada perangkat lain.

## Verifikasi perangkat pengguna — 7 Oktober 2026

- **3.1.0 terpasang** di `/home/aldebaran/tools/x-bookmarks` pada Ubuntu 26.04.1 x64, Python 3.14.4.
- Paket ZIP dan checksum diambil dari [rilis privat v3.1.0](https://github.com/fathorrosi-dev/x-bookmarks/releases/tag/v3.1.0) melalui updater; installer terpisah memakai kunci yang sama sepanjang transaksi.
- Program 3.0.2 dicadangkan. Lokasi tepat tersedia dalam `.state/last-update.json`; log pemasangan di `.state/update.log`.
- **26 catatan/settings dan 564 file media tetap sama** pada pemeriksaan sebelum/sesudah pemasangan di bawah kunci alat. Koleksi: 247 foto dan 317 video; tidak ada file sementara atau media yang perlu organize ulang.
- Pemeriksaan dependensi lulus: gallery-dl 1.32.15 dan Rich 14.3.4. Tidak ada perubahan environment dependensi saat pembaruan ini.
- `status --plain`, `update --check --plain`, dan `python3 self_update.py --check` lulus terhadap GitHub nyata, menampilkan terpasang/stabil 3.1.0.
- Preview terminal interaktif 100 kolom lulus: blok padat, palet, pembaruan di tempat, dan pemulihan kursor. Pemeriksaan rilis interaktif juga lulus. Akses langsung Warp tidak digunakan.
- Panduan, migrasi, laporan tes, dan catatan pemasangan disinkronkan ke home setelah verifikasi.

Untuk memeriksa versi, gunakan `./x-bookmarks status --plain` atau `./x-bookmarks update --check`; CLI ini tidak memiliki opsi `--version`. Perintah `--version` pada catatan pemasangan awal paket rilis digantikan oleh `status --plain` pada dokumentasi lokal ini.
