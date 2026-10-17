# X Bookmarks 3.1.1

CLI Ubuntu/Linux x64 untuk mengarsipkan media utas berdasarkan tanggal bookmark WIB. Mendukung self-update rilis stabil, backup/rollback, dan pemisahan photo/video. Python standar 3.10–3.14. Gallery-dl 1.32.15 dan Rich 14.3.4 dipatok di `requirements.txt`; dependensi yang mereka bawa dipatok di berkas yang sama.

Baca [PANDUAN.md](PANDUAN.md), [MIGRASI.md](MIGRASI.md), dan [TEST-REPORT.md](TEST-REPORT.md).

```bash
bash setup.sh
./x-bookmarks status
./x-bookmarks update --check
./x-bookmarks update
```

`./x-bookmarks --help` memuat alur lima langkah dan `--version` mencetak versi. Update memakai token GitHub bila ada (`gh auth login`, atau GH_TOKEN/GITHUB_TOKEN dengan izin Contents: read); tanpa token, repo rilis publik tetap terbaca dengan batas 60 permintaan per jam. Pengambilan rilis hanya terjadi saat update dipanggil. Cookie Firefox tidak dipakai untuk update.

## Pengembang

Ubah VERSION di bookmarks.py dan versi release.json bersama panduan. Daftar file release.json eksplisit; settings pribadi, cookie, state, environment, dan media tidak boleh masuk paket. Tes memakai settings.example.json, bukan settings pribadi. Jalankan tes lalu push ke main. Tag vX.Y.Z yang sesuai versi memicu lima job CI Ubuntu (Python 3.10, 3.11, 3.12, 3.13, 3.14); publikasi hanya berjalan setelah seluruhnya lulus. Satu job juga menjalankan ruff (`ruff.toml`, hanya untuk pengembangan dan tidak masuk paket) dan shellcheck. Workflow `canary.yml` menjalankan seluruh tes tiap minggu terhadap gallery-dl dan Rich terbaru sebagai peringatan dini; boleh merah dan tidak pernah memblokir rilis. Paket lokal: `python3 release_bundle.py /jalur/output`. Jangan mengganti aset rilis stabil dengan kode baru; buat versi baru. Repo rilis tidak berisi data pengguna: catatan perangkat disimpan di `CATATAN-PERANGKAT.md` (diabaikan Git, tidak masuk paket), dan dokumen memakai jalur contoh, bukan jalur pribadi.

### Menaikkan gallery-dl atau Rich

Adapter menempel ke internal gallery-dl (`path.PathFormat`, `job.DownloadJob`, `job.DataJob`, `Extractor.sleep` dan `wait`, `output.initialize_logging`) dan membaca format pesan log untuk mendeteksi retry. Naikkan versi hanya bersama pengujian:

1. Ubah versi di `requirements.txt`. Hanya di sana: `setup.sh` dan `gallery_worker.py` membacanya dari berkas itu. Lalu perbarui baris di bawahnya (dependensi kedua paket itu) dari `pip freeze` lingkungan bersih: semua baris kecuali gallery-dl, rich, dan pip. `test_transitive_pins_cover_exactly_the_dependencies_of_the_direct_pins` memeriksanya. Pin sengaja berada di `requirements.txt`, bukan berkas baru: updater rilis yang sudah terpasang hanya menerima nama berkas yang dikenalnya (`test_release_files_are_accepted_by_the_previous_updater`).
2. Ubah angka yang sama di kalimat README dan PANDUAN. `test_dependency_pins_have_one_source` gagal jika ada yang tertinggal.
3. Jalankan seluruh tes dengan gallery-dl baru (`.venv/bin/python -m unittest discover -s tests -v`). Tes HTTP lokal memakai gallery-dl asli untuk progres, retry, dan routing.
4. Naikkan VERSION di `bookmarks.py` dan `release.json` serta judul README, PANDUAN, dan INSTALL-STATUS (`test_documents_name_the_release_version`), push ke `main`, tunggu CI lulus, lalu beri tag.
