# X Bookmarks 3.1.1

CLI Ubuntu/Linux x64 untuk mengarsipkan media utas berdasarkan tanggal bookmark WIB. Mendukung self-update rilis stabil, backup/rollback, dan pemisahan photo/video. Python standar 3.10–3.14. Gallery-dl 1.32.15 dan Rich 14.3.4 dipatok.

Baca [PANDUAN.md](PANDUAN.md), [MIGRASI.md](MIGRASI.md), dan [TEST-REPORT.md](TEST-REPORT.md).

```bash
bash setup.sh
./x-bookmarks status
./x-bookmarks update --check
./x-bookmarks update
```

Update membutuhkan GitHub CLI yang sudah login (`gh auth login`) atau GH_TOKEN/GITHUB_TOKEN dengan izin Contents: read. Pengambilan rilis hanya terjadi saat update dipanggil. Cookie Firefox tidak dipakai untuk update.

## Pengembang

Ubah VERSION di bookmarks.py dan versi release.json bersama panduan. Daftar file release.json eksplisit; settings pribadi, cookie, state, environment, dan media tidak boleh masuk paket. Tes memakai settings.example.json, bukan settings pribadi. Jalankan tes lalu push ke main. Tag vX.Y.Z yang sesuai versi memicu tiga job CI Ubuntu; publikasi hanya berjalan setelah seluruhnya lulus. Paket lokal: `python3 release_bundle.py /jalur/output`. Jangan mengganti aset rilis stabil dengan kode baru; buat versi baru. Repo rilis tidak berisi data pengguna: catatan perangkat disimpan di `CATATAN-PERANGKAT.md` (diabaikan Git, tidak masuk paket), dan dokumen memakai jalur contoh, bukan jalur pribadi.

### Menaikkan gallery-dl atau Rich

Adapter menempel ke internal gallery-dl (`path.PathFormat`, `job.DownloadJob`, `job.DataJob`, `Extractor.sleep` dan `wait`, `output.initialize_logging`) dan membaca format pesan log untuk mendeteksi retry. Naikkan versi hanya bersama pengujian:

1. Ubah versi di `requirements.txt`. Hanya di sana: `setup.sh` dan `gallery_worker.py` membacanya dari berkas itu.
2. Ubah angka yang sama di kalimat README dan PANDUAN. `test_dependency_pins_have_one_source` gagal jika ada yang tertinggal.
3. Jalankan seluruh tes dengan gallery-dl baru (`.venv/bin/python -m unittest discover -s tests -v`). Tes HTTP lokal memakai gallery-dl asli untuk progres, retry, dan routing.
4. Naikkan VERSION di `bookmarks.py` dan `release.json`, push ke `main`, tunggu CI lulus, lalu beri tag.
