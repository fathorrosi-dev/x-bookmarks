# Memindahkan dan memulihkan X Bookmarks

Lokasi pemakaian utama yang disarankan adalah **~/tools/x-bookmarks**. Alat, hasil media, dan catatan berada dalam satu folder yang bisa dipakai tanpa membuka Codex.

## Memindahkan alat ke lokasi baru

1. Tunggu scan/download selesai, atau hentikan sendiri dengan Ctrl+C. Migrasi menolak proses yang masih aktif.
2. Masuk ke folder alat yang sekarang. Jika masih memakai paket dalam Documents/Codex:

```bash
cd '/home/aldebaran/Documents/Codex/2026-10-06/pa-possible-ya-otomatisasi-download-bookmark/outputs/x-bookmarks'
```

3. Pilih tujuan kosong lalu jalankan:

```bash
bash migrate.sh "$HOME/tools/x-bookmarks"
```

Jika tools sudah berada di Home dan ingin pindah lagi, jalankan dari Home dengan tujuan lain:

```bash
cd "$HOME/tools/x-bookmarks"
bash migrate.sh "$HOME/tools/x-bookmarks-baru"
```

Tujuan harus kosong dan berada di luar folder asal. Script menyalin seluruh paket dan data, termasuk modul tampilan, worker, tes, folder tersembunyi, serta dua subfolder media. Lingkungan .venv tidak disalin; dibuat ulang dengan Python sistem di tujuan. Internet diperlukan untuk memasang dependensi yang dipatok.

4. Verifikasi salinan baru:

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks status
./x-bookmarks list --start 2026-08-31 --end 2026-10-06 --limit 10
./x-bookmarks check
```

Ganti cd jika tujuanmu berbeda. Cocokkan jumlah media dan lokasi folder, snapshot, pilihan tanggal, serta akun X. Jika membawa hasil versi 2 di akar media, jalankan:

```bash
./x-bookmarks organize
```

5. Gunakan salinan baru. Hasil berikutnya tetap masuk media/photo dan media/video milik alat, bukan folder tempat terminal dibuka. Folder asal boleh dibersihkan setelah semua data dan fungsi terverifikasi.

Jika pemasangan tujuan terputus, salinan asal tetap tersedia. Jalankan bash setup.sh di tujuan untuk melanjutkan. Jangan mengulangi migrate.sh ke tujuan yang sudah berisi file.

## Launcher dari folder mana pun

Opsional:

```bash
mkdir -p "$HOME/.local/bin"
ln -s "$HOME/tools/x-bookmarks/x-bookmarks" "$HOME/.local/bin/x-bookmarks"
```

ln -s tidak menimpa launcher yang sudah ada. Setelah login/terminal baru:

```bash
command -v x-bookmarks
x-bookmarks status
```

Jika PATH belum memuat ~/.local/bin, gunakan jalur lengkap launcher. Jika berpindah lagi, perbarui symlink ke lokasi baru.

## Pindah ke komputer Ubuntu lain

- Salin seluruh folder termasuk .bootstrap, .state, media, dan modul program; keluarkan .venv serta __pycache__.
- Pastikan Python 3.10+ dan modul venv tersedia. Jika diperlukan, pasang python3-venv melalui pengelola paket Ubuntu.
- Jalankan bash setup.sh di lokasi tujuan. Setup tidak memerlukan sudo dan tidak mengganti Python sistem.
- Login X sendiri melalui Firefox pada komputer baru. Sesi Firefox tidak diekspor oleh alat.
- Jika browser pada settings.json memakai jalur profil khusus, sesuaikan jalurnya; nilai firefox otomatis mencari profil lokal.
- Verifikasi status, list, dan check sebelum download.

## Pembaruan dan rollback

Versi 3.0.2 memperbarui palet warna. Pembaruan dari 3.0.1 tidak memerlukan `organize` ulang; media, pengaturan, snapshot, dan database dipertahankan. Rollback program mengembalikan palet sebelumnya.

Versi 3.0.1 memperbarui bentuk progress bar. Pembaruan dari versi 3.0 tidak memerlukan `organize` ulang; folder media, snapshot, dan database tetap dipakai.

Mulai 3.1.0, program memiliki self-update rilis stabil dari repo privat fathorrosi-dev/x-bookmarks:

```bash
cd "$HOME/tools/x-bookmarks"
./x-bookmarks update --check
./x-bookmarks update --wait
```

Di perangkat baru, login ulang GitHub CLI dengan `gh auth login`; login/kredensial perangkat lama tidak dimasukkan dalam paket. Ini terpisah dari profil Firefox untuk login X. Self-update bekerja tanpa membaca rentang tanggal atau settings pribadi. Update hanya berjalan saat dipanggil.

Jika sumber masih 3.0.2, gunakan paket manual 3.1.0 sekali. Setelah itu updater mengambil paket sendiri. Opsi setup --upgrade tetap hanya mengikuti dependensi rilis, bukan mencari rilis program.
Untuk memperbarui program, dapatkan paket rilis baru dahulu, lalu letakkan di folder terpisah dari tools utama. Paket boleh berada di mana pun; Documents/Codex tidak diperlukan. Gunakan `update.sh` dari **paket rilis baru**, bukan dari tools tujuan. Script menolak sumber dan tujuan yang merupakan folder yang sama.

Contoh berikut memakai `/lokasi/paket-rilis-baru` sebagai placeholder: ganti dengan lokasi paket yang benar.

```bash
cd '/lokasi/paket-rilis-baru'
bash update.sh "$HOME/tools/x-bookmarks"
```

Untuk menunggu scan/download selesai dan memasang rilis setelah kunci bebas:

```bash
bash update.sh "$HOME/tools/x-bookmarks" --wait
```

Sesudah pemasangan selesai:

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
./x-bookmarks status
.venv/bin/python tests/demo.py
```

Periksa versi dan lokasi pada `status`. Preview tidak mengakses X atau mengunduh media. Pembaruan antarversi 3 dengan struktur folder yang sama tidak memerlukan `organize` ulang.

`settings.json`, media, snapshot, dan database tools utama dipertahankan; data tersebut tidak disalin dari paket rilis. `gallery-dl.json` dan `requirements.txt` mengikuti rilis baru. Perubahan lokal pada konfigurasi gallery-dl dicadangkan, tetapi tidak digabung otomatis. Program lama disimpan di `.state/program-backups/` dan lokasi cadangannya tercatat dalam `.state/last-update.json`. Tetap buka terminal sampai proses pembaruan selesai. Ctrl+C selama pemasangan ditunda sampai transaksi selesai/pulih, lalu keluar 130.

Untuk rollback setelah proses alat berhenti:

```bash
cd "$HOME/tools/x-bookmarks"
bash rollback.sh
./x-bookmarks status
```

Rollback memulihkan program dan file konfigurasi rilis terakhir yang dicadangkan tanpa mengembalikan atau menghapus data media. Cadangan baru menyimpan versi dependensi dan salinan environment jika dependensi diubah; rollback memulihkannya. Cadangan lama yang belum memiliki snapshot environment tetap memerlukan `bash setup.sh --check`, lalu `bash setup.sh --upgrade` bila pin belum sesuai. Helper media_layout dipertahankan untuk rollback kompatibel versi 2. Rollback ke 3.0.2 menghapus kemampuan self-update; pemasangan kembali memakai paket manual.

Jika rollback menuju versi 2, file di photo/video tetap dikenali arsip sehingga tidak diunduh ulang. Download baru versi 2 masuk akar media dan menggunakan tampilan lama. Untuk kembali ke versi 3, ulangi update dari paket versi 3 lalu jalankan organize.

Pemindahan organize tidak perlu dibatalkan saat rollback: identitas arsip berasal dari ID tweet dan nomor media, bukan lokasi folder.

Cadangan program menyimpan jalur saat pembaruan dilakukan. Setelah migrate.sh berhasil, lokasi cadangan aktif untuk rollback disesuaikan ke folder tujuan jika cadangan tersebut ikut disalin.

## Memelihara dependensi tanpa mengganti program

Jalankan dari folder tools:

```bash
cd "$HOME/tools/x-bookmarks"
bash setup.sh --check
```

Jika perlu memperbarui dependensi sesuai rilis yang sedang dipakai:

```bash
bash setup.sh --upgrade
```

Opsi `--upgrade` ini milik setup dependensi. Kode X Bookmarks tidak berganti versi; gallery-dl dan Rich tetap mengikuti pin dalam `requirements.txt`, sedangkan dependensi tanpa pin dapat diperbarui sesuai batas paket. Perubahan pin atau adapter membutuhkan rilis program yang diperbarui dan diuji, lalu dipasang melalui `update.sh`.

## Penyimpanan

media/photo dan media/video berisi koleksi seluruh rentang. .state menyimpan snapshot, pilihan, database, log, serta cadangan. Simpan keduanya bersama. .venv dapat dibuat ulang; .bootstrap ikut disalin untuk Ubuntu tanpa ensurepip.

Duplikat identik yang dipindahkan organize berada dalam .state/layout-backups/. Verifikasi koleksi dahulu sebelum menghapus cadangan tersebut sendiri.


## Pemulihan pembaruan yang belum lengkap

Installer menyimpan `.state/update-transaction.json` sebelum perubahan. Jika pemasangan gagal, program/dependensi sebelumnya dipulihkan; file baru/parsial dibersihkan. Jika pemulihan gagal, tools menandai recovery_required dan tidak melaporkan sukses. Dari paket rilis terpisah jalankan `python3 release_tools.py recover /lokasi/tools`, kemudian `bash setup.sh --check` dari tools tujuan. Jangan menghapus backup sebelum pemulihan selesai.

Migrasi ikut menyesuaikan lokasi backup pada journal pembaruan dan transaksi. Settings, snapshot/seleksi, database, serta media ikut salinan; `.venv`, Git, dan hasil build tidak disalin. Environment dibuat ulang di lokasi tujuan. Paket publikasi hanya membawa settings contoh; settings pribadi yang ada tidak ditimpa.
