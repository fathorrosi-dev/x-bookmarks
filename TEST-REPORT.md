# Verifikasi X Bookmarks 3.1.0

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
