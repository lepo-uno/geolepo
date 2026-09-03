# KUGI

Plugin QGIS untuk mengubah struktur atribut data spasial menjadi standar
KUGI (Katalog Unsur Geografi Indonesia), lengkap dengan mode QC untuk
memvalidasi data terhadap katalog.

Target: QGIS 3.28 LTR ke atas, Windows dan Linux.

---

## Pemasangan

1. Salin folder `kugi_standardizer` ke folder plugin QGIS:
   - Windows: `C:\Users\<nama>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
2. Buka QGIS, masuk **Plugins > Manage and Install Plugins > Installed**,
   lalu centang **KUGI**.

Atau pasang dari berkas ZIP lewat **Plugins > Install from ZIP**.

---

## Alur kerja

### Tab Standardisasi

1. **Data masukan** — pilih layer vektor dari project. Tipe geometri
   layer otomatis mengisi filter geometri di langkah berikutnya.
2. **Unsur KUGI tujuan** — pilih kategori, saring berdasarkan geometri
   dan skala, lalu klik **Muat skema**.
3. **Pemetaan kolom** — seret kolom dari panel kiri ke panel kanan.
   Menjatuhkan ke bagian *Field standar KUGI* berarti memasangkan.
   Menjatuhkan ke bagian *Field tambahan* berarti membawa kolom apa
   adanya. Hasilnya adalah skema KUGI ditambah kolom eksisting.
4. **Keluaran** — pilih Shapefile, GeoPackage, atau keduanya.
5. **Proses dan periksa** — membangun layer sementara di memori,
   menjalankan QC, lalu berpindah ke tab QC. Tombol **Simpan** baru
   aktif setelah tidak ada Error.

### Tab QC

Bisa dipakai berdiri sendiri tanpa menyentuh tab Standardisasi. Menerima
tiga sumber: hasil standardisasi di memori, layer yang sudah ada di
project, atau berkas dari disk.

Tombol **Deteksi dari FCODE** membaca nilai FCODE lalu menentukan unsur
pembandingnya sendiri.

Pemeriksaan yang dijalankan:

| Pemeriksaan | Level |
|---|---|
| Nilai di luar daftar domain katalog | Error |
| Field inti (FCODE, NAMOBJ, SRS_ID, METADATA) tidak ada | Error |
| Nama field beda huruf besar-kecil | Error |
| Tipe data tidak sepadan | Error |
| Nilai melebihi panjang field | Error |
| FCODE tidak seragam atau salah kode | Error |
| OBJECTID kosong atau duplikat | Error |
| Geometri layer tidak cocok dengan tipe unsur | Error |
| Field KUGI non-inti tidak ada | Peringatan |
| Nama field lebih dari 10 karakter untuk shapefile | Peringatan |
| Field inti bernilai kosong | Peringatan |
| Kolom di luar skema KUGI | Info |

Tombol **Pilih fitur bermasalah** menyeleksi fitur terkait di kanvas.
Laporan bisa diekspor ke CSV.

---

## Keluaran

Untuk satu unsur dengan kedua format dicentang:

```
JALAN_LN.shp  .shx  .dbf  .prj  .cpg
JALAN_LN.gpkg
JALAN_LN.qml
```

Berkas `.qml` memuat ValueMap untuk setiap field berdomain. Unsur
`JALAN_LN` misalnya punya 16 field berdomain dari 33 atribut, sehingga
pengisian atribut di QGIS memakai dropdown, bukan mengetik kode angka
dari hafalan.

---

## Temuan API yang menjadi dasar implementasi

Semuanya diverifikasi terhadap respons nyata, bukan asumsi.

**Struktur kode unsur bisa didekode penuh secara offline.** Terverifikasi
pada 200 kode kategori BATAS WILAYAH dan tiga kode dari kategori lain:

```
B   A   03   06   0060
|   |   |    |    +--- nomor urut unsur
|   |   |    +-------- kode skala (01..10)
|   |   +------------- tipe geometri (01=PT, 02=LN, 03=AR)
|   +----------------- sub-tema
+--------------------- kode katalog
```

Korelasi posisi 3-4 dengan geometri sempurna: 01 pada 38 unsur PT,
02 pada 70 unsur LN, 03 pada 92 unsur AR.

**`typeName` tidak unik.** Pada KUGI 5.1.2026 sufiks skala dihapus dari
nama, sehingga 200 kode di satu kategori hanya menghasilkan 34 nama.
`ADMINISTRASI_LN` muncul di sembilan skala berbeda. Identitas unsur
adalah `code`, tidak pernah `typeName`.

**Sufiks `@en` berbeda antar endpoint.** `featurecatalog` dan
`featuretype` memakainya di semua nilai, `featuretypegetbycode` tidak
sama sekali. Pembersihan karena itu kondisional. Memakai
`str.strip('@en')` merusak data nyata: `'Akan Dibangun'` menjadi
`'Akan Dibangu'` karena `strip()` menghapus himpunan karakter, bukan
sufiks.

**Respons skema adalah satu baris per pasangan atribut-domain.**
`JALAN_LN` mengembalikan 97 baris untuk 34 atribut. Grouping by
`ptMemberName` wajib, dan barisnya kontigu sehingga urutan katalog
terjaga tanpa sorting.

**`faValueType` bernilai `Geometry` bukan kolom atribut.** `SHAPE`
difilter, karena membuatnya sebagai field akan merusak keluaran. Tipe
yang tidak dikenal jatuh ke String disertai peringatan di Log Messages.

**`featureTypeId` bukan identitas unsur.** Nilainya berbeda di setiap
baris (97 nilai untuk satu unsur). Nama kuncinya pun berbeda antar
endpoint: `featureTypeId` versus `featureTypeID`.

**API tidak menyediakan panjang field maupun kardinalitas.**
`ptCardinality` bernilai `-` atau null di seluruh baris, sehingga tidak
ada dasar untuk menentukan field wajib dari katalog. Daftar field inti
ditetapkan di sisi plugin. Panjang field diambil dari
`resources/field_length_overrides.json` yang diturunkan dari Buku 2
KUGI, dengan default 254.

**Endpoint `featuretype` menolak klien tanpa header browser** dan
membalas HTTP 500. Plugin memasang `User-Agent` dan `Accept` secara
eksplisit.

---

## Catatan teknis

**Mengapa panjang field tidak diseragamkan 254.** DBF berformat
fixed-width, sehingga `String(254)` memakan 254 byte per record apa pun
isinya. Untuk data desa nasional sekitar 83.000 poligon dengan 30 field,
selisihnya sekitar 632 MB versus 150 MB.

**Mengapa batas 10 karakter berlaku untuk kedua format.** Bila Shapefile
termasuk target, batas nama DBF diterapkan juga ke GeoPackage supaya
skema kedua keluaran identik. Mencentang atau melepas Shapefile akan
menghitung ulang seluruh nama field tambahan.

**Mengapa OBJECTID diperlakukan sebagai field biasa.** Sebagian data
warisan ArcGIS sudah punya penomoran sendiri. Bila tidak dipetakan,
kolom diisi nomor urut 1 sampai N. Bila dipetakan dari kolom eksisting,
validator memeriksa keunikan dan kekosongannya.

**Struktur modul.** `kugi_api`, `kugi_model`, `mapping`, `builder`, dan
`validator` tidak mengimpor widget apa pun. Panel dual list hanyalah view
di atas `MappingState`, sehingga mengganti bentuk antarmuka tidak
menyentuh builder maupun validator.

---

## Batasan yang diketahui

- Panggilan jaringan memakai `QgsBlockingNetworkRequest` dengan kursor
  tunggu. Untuk katalog sebesar ini responsnya cepat, tapi migrasi ke
  `QgsTask` akan lebih baik bila katalog membesar.
- Belum tersedia sebagai Processing algorithm. Modul `builder` dan
  `validator` sudah berbentuk fungsi murni sehingga pembungkusnya
  tinggal ditambahkan.
- Kolom luas dan panjang (`LUASWH`, `SHAPE_Length`, `SHAPE_Area`) tidak
  dihitung otomatis. Diisi dari kolom eksisting bila ada, selain itu
  dikosongkan untuk diproses terpisah oleh pengguna.
- `Int64` didukung terbatas oleh shapefile. Plugin memberi peringatan
  bila tipe itu muncul dengan target SHP.
- Snapshot offline hanya memuat katalog kategori, daftar unsur kategori
  BATAS WILAYAH, dan skema `CA02040160`.

---

## Lisensi

GNU General Public License v3.0 atau versi setelahnya. Lihat `LICENSE`.
Teks lengkap lisensi perlu ditempel dari https://www.gnu.org/licenses/gpl-3.0.txt
sebelum publikasi ke repositori resmi QGIS.
