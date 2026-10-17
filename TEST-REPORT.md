# Verifikasi X Bookmarks

## Perubahan setelah tinjauan repositori (belum dirilis)

Menerapkan 19 rekomendasi tinjauan: AUD-001 sampai AUD-008, UX-001 sampai UX-005, FEAT-001 sampai FEAT-004, DX-001, DX-002. VERSION belum dinaikkan; menaikkannya adalah keputusan rilis.

Pengujian lokal: **89 tes lulus** (57 di `test_tools.py`, 32 di `test_release.py`) dengan Python 3.12.3, gallery-dl 1.32.15, Rich 14.3.4, `TERM=linux`, tanpa mengakses X. Sebelum perubahan, 57 tes yang ada lulus kecuali dua tes UI yang bergantung pada `TERM` host; keduanya kini memasang `TERM` sendiri. Tes baru mencakup: orkestrasi `download()`, `extract()` dan `resolve_browser()` dengan job fixture lokal; locale ASCII tanpa mode UTF-8; lock baca-saja; pengaman reconcile massal beserta cadangan entri; sel CSV mirip formula; kontrak stdout/stderr; tema terang dan kontrasnya; subcommand, `--version`, hint langkah berikut; skema `--json`; baris Run terakhir; handler error tak terduga; `clean`; token opsional; rentang Python di `setup.sh`; kelengkapan pin dependensi transitif; aturan nama berkas yang diterima updater rilis sebelumnya; judul dokumen. Logika utama tiap perubahan perilaku diperiksa dengan mutation check manual: logikanya dirusak sengaja dan test harus gagal.

Lint: `ruff check .`, `ruff format --check` untuk tiga modul rilis, dan `shellcheck` bersih. Resolusi pin di `requirements.txt` diperiksa untuk Python 3.10, 3.11, 3.12, 3.13, 3.14 dengan `pip download --python-version`; hanya 3.12 yang dijalankan. Bundle rilis yang dibangun dari pohon ini juga diuji terhadap `safe_unpack` milik updater 3.1.1 asli: diterima, sedangkan versi awal yang memuat berkas baru `constraints.txt` ditolak, sehingga pin dipindah ke `requirements.txt`. Hasil CI dan pemeriksaan perangkat belum dicatat; tambahkan setelah workflow tag selesai.

## Perubahan pada 3.1.1

Tes baru mencakup: panel dan lebar bar sama pada 40/60/100/160 kolom, simbol satu sel dan cadangan ASCII, gerak comet tanpa lompatan, berhenti saat tidak ada data, hitung mundur penantian, riwayat kecepatan, struk per utas, mode tanpa gerak dan tanpa judul terminal, pesan settings.json yang hilang atau rusak, satu sumber versi dependensi, dan kecocokan daftar ekstensi `gallery-dl.json` dengan `media_layout.py`. Tes rilis tidak lagi menulis versi program secara langsung.

Hasil CI dan pemeriksaan perangkat untuk tag v3.1.1 belum dicatat di sini; tambahkan setelah workflow tag selesai. Bagian di bawah adalah verifikasi 3.1.0.

## Verifikasi 3.1.0

Pengujian lokal: **45 tes lulus** dengan Python 3.14.4, gallery-dl 1.32.15, dan Rich 14.3.4. Ubuntu perangkat pengembang: 26.04.1 x64.

CI: **Ubuntu 24.04 x64, Python 3.10, 3.13, dan 3.14 lulus**. [Hasil CI](https://github.com/fathorrosi-dev/x-bookmarks/actions/runs/37635385136).

## Cakupan

- 18 tes regresi sebelumnya dipertahankan, ditambah pemeriksaan SHA-256 kompatibel Python 3.10.
- 26 tes rilis mencakup autentikasi, rilis stabil/versi lama, checksum, ZIP berbahaya, ukuran paket, platform/Python, dan paket tidak lengkap.
- Kunci antarproses, menunggu, pemasangan terpisah, kegagalan/pemulihan dependensi, penghentian tertunda, serta pemulihan yang belum lengkap diuji lokal.
- Alur update ke versi lebih baru dan rollback diuji pada salinan terisolasi, dengan data fixture tetap dipertahankan.
- Setup bersih, migrasi folder dengan spasi, HTTP lokal menggunakan gallery-dl asli, serta pengecualian data pribadi dari ZIP diuji.
- Tampilan 40/60/100/160 kolom, palet, plain, NO_COLOR, dan Ctrl+C termasuk tes regresi. Preview tidak menggunakan jaringan atau login X.

Rilis stabil dipublikasikan oleh workflow tag hanya setelah ketiga pekerjaan CI lulus kembali. Pengujian memakai terminal interaktif; pemeriksaan langsung melalui Warp belum dilakukan. Windows/macOS berada di luar dukungan rilis ini.

Pemasangan dan pemeriksaan perangkat pengguna dicatat terpisah dalam INSTALL-STATUS.md. Laporan ini tidak mengklaim melakukan download X nyata dalam pengujian rilis.

## Verifikasi rilis dan home

[CI tag v3.1.0](https://github.com/fathorrosi-dev/x-bookmarks/actions/runs/37636081902) lulus pada ketiga versi Python dan berhasil mempublikasikan ZIP serta SHA-256. Rilis bukan draft/prerelease.

Upgrader mengambil paket GitHub nyata, memverifikasinya, dan memasang 3.1.0 ke home dari 3.0.2. Backup dan journal transaksi dikonfirmasi; dependensi tetap sama. Inventaris catatan/settings dan media di bawah kunci sebelum/sesudah pemasangan cocok.

Pemeriksaan rilis home lulus melalui CLI plain, CLI interaktif, dan jalur Python standar. Preview home memverifikasi ketiga warna, blok padat, pembaruan di tempat, serta kursor kembali tampil. Tes update ke versi sintetis yang lebih baru dan rollback tetap memakai salinan terisolasi, tidak memakai data utama atau menerbitkan rilis simulasi.
