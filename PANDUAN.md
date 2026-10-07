# Panduan X Bookmarks — versi 3.1.0

Alat ini mengambil media utas X berdasarkan **tanggal nge-bookmark**, dengan tanggal awal dan akhir inklusif dalam **WIB / Asia/Jakarta**. Hasil seluruh rentang digabung dalam satu koleksi, dibedakan berdasarkan format file.

```text
x-bookmarks/
├── x-bookmarks          perintah utama
├── setup.sh             pemasangan dependensi
├── migrate.sh           pindah ke folder/komputer baru
├── update.sh            memasang rilis ini ke alat lama
├── rollback.sh          memulihkan program sebelum pembaruan
├── settings.json        tanggal default, zona waktu, browser
├── gallery-dl.json      kualitas dan pengaturan gallery-dl
├── bookmarks.py
├── cli_ui.py
├── gallery_worker.py
├── media_layout.py
├── requirements.txt
├── tests/               tes lokal dan preview tampilan
├── media/
│   ├── photo/           JPG, JPEG, PNG, WEBP, GIF, AVIF
│   └── video/           MP4, WEBM, MOV, M4V, MKV, TS
├── .state/              snapshot, pilihan, database, log, cadangan
├── .venv/               lingkungan Python lokal
└── .bootstrap/          paket pip untuk Ubuntu tanpa ensurepip
```

Animasi yang disebut “GIF” di X dapat disimpan sebagai MP4; file tersebut masuk `video/`. Alat tidak mengonversi atau mengompres ulang media. Format yang belum dikenali tetap disimpan di akar `media/`, dengan peringatan di log. File `.part` adalah download sementara. JSON, log, dan database tidak dibuat di folder media.

## Pemakaian sekarang dan jenis pembaruan

Mulai **3.1.0**, program memiliki self-update rilis stabil. Perintah download/scan/list yang sudah dipakai tetap berlaku. Jika tools masih 3.0.2, pasang paket 3.1.0 sekali melalui updater manual; setelah itu tidak perlu mengambil paket secara manual untuk pembaruan berikutnya.

| Kebutuhan | Cara | Yang dilakukan |
|---|---|---|
| Memeriksa pemasangan | `bash setup.sh --check` | Memeriksa lingkungan dan dependensi; bukan server rilis |
| Setup awal | `bash setup.sh` | Memasang dependensi dan membuat settings dari contoh hanya jika belum ada |
| Menyesuaikan dependensi | `bash setup.sh --upgrade` | Mengikuti requirements rilis terpasang; bukan mencari versi program baru |
| Memeriksa rilis program | `./x-bookmarks update --check` | Menampilkan versi terpasang/stabil tanpa memasang atau mengubah data |
| Memasang rilis terbaru | `./x-bookmarks update` | Mengambil, memverifikasi, dan memasang paket dengan backup |
| Memasang saat alat sedang aktif | `./x-bookmarks update --wait` | Menunggu kunci bebas sebelum pemasangan |
| Update manual | `bash update.sh /lokasi/tools` dari paket baru | Alternatif dan pemasangan pertama dari versi 3.0.2 |
| Rollback | `bash rollback.sh` | Memulihkan cadangan program/dependensi terakhir; media tetap tersedia |

### Self-update dari home

```bash
cd "$HOME/tools/x-bookmarks"
./x-bookmarks update --check
./x-bookmarks update
# opsional jika proses scan/download masih berjalan:
./x-bookmarks update --wait
```

`./x-bookmarks check` masih memeriksa sesi X. `./x-bookmarks update --check` hanya memeriksa rilis program: tidak membutuhkan tanggal yang valid, settings, login X, atau profil Firefox. Tambahkan `--plain` untuk teks sederhana; NO_COLOR dihormati. Opsi --check/--wait hanya berlaku pada update.

Repo rilis **fathorrosi-dev/x-bookmarks** privat. Pasang GitHub CLI (`sudo apt install gh`), lalu `gh auth login` dengan akun yang punya akses repo. Login laptop ini dapat dipakai juga untuk tools YouTube. Alternatif otomasi adalah GH_TOKEN, lalu GITHUB_TOKEN, dengan izin Contents: read; token tidak ditulis ke settings atau log. Login GitHub berbeda dari login X/Firefox.

Pembaruan hanya berjalan saat dipanggil. Tools mengambil rilis stabil yang dipublikasikan setelah CI lulus, bukan isi branch pengembangan atau prerelease. ZIP dan SHA-256 diverifikasi sebelum ekstraksi; versi, Linux x64, serta Python standar 3.10–3.14 diperiksa. Versi sama/lebih lama dilewati. Checksum memeriksa integritas unduhan; repo GitHub privat tetap menjadi sumber rilis tepercaya.

Output memakai tahap pemeriksaan, unduhan/verifikasi, dan pemasangan tanpa mencetak tiap byte. Koneksi terputus, login ditolak, paket tidak lengkap, atau checksum salah tidak mengganti program. Log pemasangan ada di `.state/update.log`; backup di `.state/program-backups/`.

Tools aktif dilindungi kunci yang sama. Tanpa --wait, pembaruan berhenti dengan pesan; dengan --wait, proses aktif dibiarkan selesai. Ctrl+C sebelum pemasangan membatalkan. Setelah transaksi pemasangan mulai, Ctrl+C ditunda sampai transaksi selesai/pulih, lalu keluar 130. Setelah sukses, ringkasan menunjukkan versi dan lokasi backup. Settings, snapshot, pilihan, arsip, media, dan cookie tidak diambil dari paket.

Jika tampilan/dependensi bermasalah, jalankan dari folder terpasang:

```bash
python3 self_update.py --check
python3 self_update.py
```

Jalur ini memakai pustaka standar Python tanpa Rich/gallery-dl. Jika pemulihan setelah gagal belum lengkap, alat tidak melaporkan sukses dan akan menolak pembaruan berikutnya. Gunakan `python3 release_tools.py recover /lokasi/tools` **dari paket rilis baru yang terpisah**, lalu periksa `bash setup.sh --check`. Backup dan log dipertahankan untuk pemulihan. Penghentian paksa/padam listrik bukan penghentian normal; journal transaksi dipakai untuk mendeteksi transaksi yang belum selesai.

### Pemasangan pertama dari 3.0.2

```bash
cd /jalur/paket-rilis-3.1.0
bash update.sh "$HOME/tools/x-bookmarks" --wait
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks status
```

Paket boleh berada di folder mana pun; tidak harus Documents/Codex. Tidak perlu organize ulang jika photo/video sudah tersusun.

## Migrasi hasil versi 2 yang sudah ada

Bagian ini khusus hasil versi 2 yang masih berada di akar `media/`. Untuk pemasangan versi 3 saat ini dengan hasil di `photo/` dan `video/`, lewati bagian ini.

**Tunggu download lama selesai.** Jalankan pembaruan dari folder paket versi 3, bukan dari folder alat lama:

```bash
cd /jalur/paket-rilis-3.1.0
bash update.sh "$HOME/tools/x-bookmarks"
```

Jika alat masih berjalan, pembaruan berhenti. Untuk menunggu dan memasang otomatis setelah proses selesai, tambahkan `--wait`:

```bash
bash update.sh "$HOME/tools/x-bookmarks" --wait
```

Pembaruan mencadangkan program dalam `.state/program-backups/`, memasang dependensi, dan mempertahankan `settings.json`, snapshot, media, serta database. `gallery-dl.json` mengikuti paket rilis; perubahan lokal pada file itu tersimpan dalam cadangan program, tetapi tidak digabung otomatis. Tetap buka terminal sampai pembaruan selesai.

Setelah muncul versi rilis yang baru terpasang:

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks organize
./x-bookmarks status
```

`organize` memindahkan file lama dari akar media ke dua subfolder, tanpa download ulang. File sementara dilewati. Perintah aman diulang setelah terputus.

Benturan nama dengan isi berbeda menghentikan migrasi sebelum file dipindahkan. Jika kedua isi identik, salinan sumber dicadangkan di `.state/layout-backups/`. Laporan tersedia di `.state/last-organize.json`. Database download tidak diubah oleh pemindahan. Jika masih ada hasil lama di akar media, download versi 3 meminta `organize` terlebih dahulu.

## Penggunaan dari awal

### 1. Masuk ke lokasi tools dan periksa pemasangan

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks status
```

Untuk pemasangan baru, jalankan `bash setup.sh` terlebih dahulu. Internet diperlukan saat memasang paket; setup menggunakan lingkungan lokal dan tidak memerlukan sudo. gallery-dl dipatok ke **1.32.15**, Rich ke **14.3.4**, sesuai adapter yang diuji.

Jika masih memakai salinan di Documents/Codex, ganti lokasi pada langkah pertama. Ikuti [MIGRASI.md](MIGRASI.md) untuk pindah permanen.

### 2. Pastikan akun X di Firefox benar

Buka bookmark X di Firefox, pastikan akun yang dipakai benar, lalu tutup Firefox secara normal sambil tetap login.

```bash
./x-bookmarks check
```

Alat membaca lima bookmark sebagai sampel; media belum diunduh. Cocokkan penulis, URL, dan tanggal bookmark dengan yang kamu ingat. Satu profil Firefox yang memiliki sesi X dipilih otomatis. Jika beberapa profil cocok, pilih sendiri:

```bash
./x-bookmarks check --browser 'firefox/.x.com:/jalur/profil'
```

Ganti jalur contoh dengan profil sebenarnya. Gunakan pilihan yang sama saat scan/download, atau simpan dalam `settings.json`. Cookie/password tidak perlu diekspor atau disalin ke chat.

Tanggal bookmark diperoleh gallery-dl dari urutan bookmark yang dikirim X. Jika tanggal sampel janggal, periksa profil dan hasil sebelum melanjutkan.

### 3. Ambil snapshot dan pilih rentang

```bash
./x-bookmarks scan --start 2026-08-31 --end 2026-10-06
```

Scan mengambil metadata bookmark yang tersedia, termasuk tweet pembuka tanpa media. Media belum diunduh. Spinner menunjukkan proses berjalan; status menampilkan penantian rate limit jika diperlukan.

Rentang ini berarti:

```text
31 Agustus 2026 00:00:00 WIB <= tanggal bookmark < 7 Oktober 2026 00:00:00 WIB
```

Tanggal posting tweet bukan filter. Media dalam utas yang terpilih tidak dipotong berdasarkan tanggal posting.

Jika snapshot yang ada masih sesuai, pilih tanggal tanpa mengakses X lagi:

```bash
./x-bookmarks select --start 2026-08-31 --end 2026-10-06
```

Snapshot sebelumnya dipertahankan jika scan gagal atau menghasilkan data parsial. Hasil scan baru baru menggantikannya setelah tervalidasi.

### 4. Periksa daftar

```bash
./x-bookmarks list --start 2026-08-31 --end 2026-10-06 --limit 10
```

Tanpa batas, tampil dua puluh bookmark. CSV lengkap berada di lokasi yang dicetak, dalam `.state/selections/`, dan bisa dibuka di LibreOffice Calc.

### 5. Coba tiga utas

```bash
./x-bookmarks download --start 2026-08-31 --end 2026-10-06 --limit 3
```

Periksa hasil di `media/photo/` dan `media/video/`. Nama file mengikuti:

```text
tanggal-postingUTC_penulis_IDpercakapan_IDtweet_nomormedia.ext
```

Tanggal pada nama file adalah tanggal posting UTC. Tanggal bookmark WIB berada dalam daftar pilihan. Jangan mengubah nama file yang ingin tetap dikenali arsip download.

### 6. Download seluruh daftar

```bash
./x-bookmarks download --start 2026-08-31 --end 2026-10-06
```

File yang sudah tersedia dilewati. Kamu dapat mengulangi perintah yang sama untuk melanjutkan setelah gangguan. Tekan `Ctrl+C` untuk berhenti; ringkasan menampilkan hasil sampai saat itu.

Jika file dihapus, alat menyesuaikan arsip sebelum download berikutnya sehingga file dapat diambil lagi saat utasnya masuk pilihan. Memindahkan file keluar dari lokasi yang dikelola alat juga dianggap sebagai file hilang. Pemindahan lewat `organize` tetap dikenali.

### 7. Pilih cakupan penulis

Default mengambil media dari penulis tweet yang kamu bookmark, dalam percakapan yang sama. Jika beberapa penulis dibookmark dalam percakapan tersebut, semuanya terpilih.

Untuk menyertakan penulis lain dalam percakapan:

```bash
./x-bookmarks download --start 2026-08-31 --end 2026-10-06 --all-authors --limit 3
./x-bookmarks download --start 2026-08-31 --end 2026-10-06 --all-authors
```

Media yang sudah tersedia tetap dilewati. Beralih mode tidak menghapus media lama.

## Membaca tampilan baru

Palet versi 3.0.2 berlaku juga pada spinner, header tabel, dan ringkasan. Teks sekunder memakai `#94A3B8`. Pembaruan dari 3.0.1/3.0.2 tidak memerlukan `organize` ulang.

- **Pink `#FFC2D4`:** persentase dan jumlah utas selesai diproses dibanding total setelah `--limit`. Utas gagal tetap termasuk “diproses”; jumlah gagal terpisah.
- **Red `#E8384F`:** file aktif. Persentase dan ETA hanya ditampilkan jika ukuran/kecepatan tersedia. Ukuran belum diketahui ditandai tiga blok yang bergerak bolak-balik, tanpa persentase atau ETA. Blok hanya bergerak saat mengunduh; diam saat menunggu, retry, gagal, atau selesai.
- **Sky blue `#A8D8F0`:** judul dan informasi pendukung.
- **Foto/video:** file baru yang berhasil disimpan pada pemanggilan saat ini, bukan jumlah seluruh koleksi.
- **Dilewati:** file yang sudah tersedia.
- **GAGAL:** masalah yang perlu diperiksa di log; warna merah sendiri bukan tanda error.
- **Menunggu:** batas permintaan X atau jeda panjang. Countdown hanya muncul jika waktunya diketahui.
- Sisa waktu hanya untuk file aktif. Durasi tampil dengan `j` (jam), `m` (menit), `d` (detik).

Kedua bar memakai blok padat `█`, bagian kosong `░` abu-abu gelap `#334155`, dan pecahan blok pada ujung isian agar pergerakannya halus. Lebar bar mengikuti ruang terminal. Di bawah 60 kolom, bar file menampilkan persentase saja; ukuran pindah ke baris kecepatan. `—%` berarti ukuran belum diketahui. Nama dan informasi panjang dipendekkan agar panel tidak membungkus.

Panel diperbarui di tempat yang sama sekitar empat kali per detik. Setelah selesai, panel diganti ringkasan permanen. Tidak diperlukan pengaturan khusus pada Warp.

Untuk mencoba tampilan tanpa mengakses X atau mengunduh file:

```bash
.venv/bin/python tests/demo.py
```

Angka dalam preview adalah simulasi. Preview mencakup ukuran belum diketahui, jeda menunggu dengan animasi berhenti, lalu progres normal. Coba memperkecil terminal untuk melihat tata letak ringkas.

Untuk teks sederhana:

```bash
./x-bookmarks download --start 2026-08-31 --end 2026-10-06 --plain
```

Mode plain juga otomatis dipilih saat output dialihkan atau terminal tidak mendukung panel. Output CLI memakai stderr; untuk menyimpan semua tampilan:

```bash
./x-bookmarks status --plain > status.txt 2>&1
```

`NO_COLOR=1 ./x-bookmarks status` menghilangkan warna. Pada terminal interaktif, panel tetap diperbarui dengan bentuk blok tanpa warna.

## Mengganti tanggal di masa depan

Gunakan rentang yang bookmark-nya sudah tersedia:

```bash
./x-bookmarks scan --start 2026-11-01 --end 2026-11-30
./x-bookmarks list --start 2026-11-01 --end 2026-11-30 --limit 10
./x-bookmarks download --start 2026-11-01 --end 2026-11-30 --limit 3
./x-bookmarks download --start 2026-11-01 --end 2026-11-30
```

Jika memakai snapshot lama yang masih sesuai, ganti `scan` dengan `select`. Gunakan tanggal yang sama pada pemilihan, pemeriksaan, dan download. Pilihan tiap rentang tersimpan terpisah, tetapi hasil semua rentang masuk koleksi foto/video yang sama; rentang tumpang tindih tidak membuat salinan tambahan.

Untuk default permanen, edit `start` dan `end` pada `settings.json` dengan format `YYYY-MM-DD`. Zona waktu default `Asia/Jakarta`.

Jika hari terakhir belum berakhir saat scan, ulangi scan/download setelah hari itu selesai untuk mengambil bookmark tambahan. Tidak ada penjadwalan scan otomatis.

## Kualitas, log, dan pemeliharaan

Foto mencoba `orig`, lalu `4096x4096` dan `large` jika ukuran sebelumnya tidak tersedia. Video memakai bitrate tertinggi yang dikirim X. Tidak ada pengubahan resolusi atau kompresi ulang.

Tweet terhapus, media tidak tersedia, akun terlindungi yang tidak dapat diakses, atau balasan yang tidak dikembalikan X dapat membuat hasil tidak lengkap. Selesai memproses seluruh utas tidak menjamin semua media historis tersedia.

Jeda permintaan dan retry tetap dikelola gallery-dl. Saat rate limit, tunggu reset. Jika akun dikunci, selesaikan verifikasi di Firefox sebelum mencoba lagi.

Log terpusat: `.state/download.log` dan `.state/scan.log`. Laporan terakhir tiap rentang berada di `.state/selections/.../last-run-*.json`. Media, snapshot, dan database merupakan data pribadi; jangan mengirim cookie atau profil Firefox.

Untuk memeriksa dependensi:

```bash
bash setup.sh --check
```

`bash setup.sh --upgrade` memperbarui dependensi sesuai `requirements.txt`, bukan memperbarui kode program. gallery-dl dan Rich tetap dipatok; dependensi lain yang tidak dipatok dapat diperbarui sesuai batas paket. Jika X berubah, adapter dan pin versi perlu diperbarui serta diuji bersama dalam rilis program baru.

Tes lokal tanpa mengakses X:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Simpan `.state`, `media`, dan `.bootstrap` saat memindahkan alat. Jangan menjalankan beberapa proses sekaligus; setup, download, organize, update, rollback, dan migrasi memakai kunci yang sama.

Referensi: [pengaturan foto](https://gdl-org.github.io/docs/configuration.html#extractor-twitter-size), [kode extractor X](https://github.com/mikf/gallery-dl/blob/v1.32.15/gallery_dl/extractor/twitter.py), [Rich Live](https://rich.readthedocs.io/en/stable/live.html).
