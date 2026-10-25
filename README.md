# Ingestion Transjakarta

Memuat 189.500 baris transaksi dari PostgreSQL ke landing tanpa cleaning.
Python menyalin dan memvalidasi data; Pentaho mengatur hasil sukses/gagal.
Batch yang sudah sukses tidak dimuat ulang.

## Prasyarat

- Windows, Python 3.14, PostgreSQL 17, Pentaho PDI 9.4 dan Java 17.
- PostgreSQL aktif. Akun memiliki izin membuat schema/tabel di database tujuan.

## 1. Setup pertama

Ekstrak/clone proyek ke folder pilihan, lalu buka PowerShell di folder itu.
Jangan menyalin `.venv` dari komputer lain.

1. Di pgAdmin, buat database kosong bernama `transjakarta_oltp`.
2. Salin `config.example.json` menjadi `config.json`. Isi koneksi dan password lokal.
   Pertahankan `expected_rows` sebesar `189500` untuk dataset.
3. Jalankan:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare.ps1 -InitializeSourceCsv '.\Raw Data\dfTransjakarta180kRows.csv'
```

Perintah ini membuat `.venv`, memasang dependensi, mengimpor CSV ke sumber,
membuat landing dan kontrol batch, lalu menghasilkan `pentaho/ingestion.kjb`
sesuai lokasi folder. Belum ada ingestion ke landing.
Setup CSV menolak tabel yang sudah ada dan membatalkan impor jika jumlah baris salah.

**Jika tabel sumber dan landing sudah tersedia**, cukup jalankan:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare.ps1
```

Jika `python` tidak ditemukan, tambahkan `-PythonExe 'C:\lokasi\python.exe'`.
Database lama tidak perlu dibuat ulang.

## 2. Jalankan melalui Spoon

1. Buka Spoon dengan Java 17. Bila diperlukan, atur `PENTAHO_JAVA_HOME`
   ke folder JDK 17 komputer sendiri sebelum membuka `Spoon.bat`.
2. Buka **`pentaho/ingestion.kjb`**, bukan file template.
3. Klik Run, pilih Local, dan periksa Parameters. Gunakan `BATCH_ID = M2_001`;
   `CONFIG_PATH` dan `PYTHON_EXE` harus sesuai folder proyek sekarang.
4. Jalankan. Password dibaca dari `config.json`, tanpa prompt PowerShell.

Hasil pertama: `SUCCESS`, `source_rows = landing_rows = 189500`, `mismatch_rows = 0`.
Jalankan ID yang sama lagi: `ALREADY_SUCCESS` dan jumlah landing tetap sama.
Gunakan ID baru hanya untuk snapshot baru yang disengaja.
Query pemeriksaan tersedia di `sql/02_verify_batch.sql` (format skrip psql).

## 3. Jika folder dipindahkan

Tutup job di Spoon, lalu jalankan dari lokasi baru:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare.ps1 -JobOnly
```

Buka ulang job dan sesuaikan parameter Run bila Spoon masih menyimpan nilai lama.
Di komputer baru, ulangi setup pertama dan buat `.venv` baru.
Template tidak menyimpan path komputer. Job lokal dibuat saat setup dan tidak masuk Git.
Perubahan alur job untuk dibagikan perlu diterapkan pada `ingestion.template.kjb`;
job lokal akan ditimpa setiap setup.

## Kendala umum

| Pesan/kondisi | Tindakan |
|---|---|
| `OperationalError` | Periksa layanan PostgreSQL, host, port, database, dan password di `config.json`. |
| `CONFIG ERROR` atau path lama | Jalankan `prepare.ps1 -JobOnly`, buka ulang job, periksa parameter Run. |
| Tabel sudah ada saat impor CSV | Jika sumber sudah siap, jalankan `prepare.ps1` tanpa opsi impor. Jangan hapus sumber. |
| Jumlah/header CSV tidak sesuai | Gunakan dataset  yang benar, bukan CSV dengan jumlah baris berbeda. |
| `ALREADY_SUCCESS` | Normal: batch tersebut sudah berhasil dimuat. |

## Isi repository

`scripts/` berisi ingestion dan setup, `pentaho/ingestion.template.kjb` template job,
`sql/` kontrol/validasi, dan `tests/` pengujian.
Jangan unggah `config.json`, `.venv`, log, atau dataset tanpa izin distribusi.
Tes integrasi memakai database kosong terpisah bernama `transjakarta_m2_test*`.
Atur `M2_TEST_DSN` dan password melalui environment pengujian,
lalu jalankan `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`.
Tanpa `M2_TEST_DSN`, tes database dilewati.

## Pengujian sebelum dibagikan

Setelah setup, jalankan:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_portability.py --csv '.\Raw Data\dfTransjakarta180kRows.csv'
```

Akun memerlukan izin CREATE DATABASE. Pemeriksaan membuat database uji sementara,
menjalankan 27 tes, mengimpor seluruh CSV, memeriksa ingestion dan retry,
lalu menghapus database uji yang dibuatnya. Sumber asli tidak ditulis.
Target: seluruh tes lulus tanpa skipped, kemudian pesan PASS.
Pengujian ini belum menggantikan percobaan job di Spoon dari lokasi baru.
