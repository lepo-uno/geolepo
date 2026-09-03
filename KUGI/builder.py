# -*- coding: utf-8 -*-
"""Pembangun layer keluaran dan penulis berkas.

Alurnya: bangun QgsFields dari skema, materialkan layer memori, jalankan
validator, baru tulis ke disk. Layer memori adalah tahap antara supaya
masalah ketahuan sebelum berkas dibuat.
"""

import json
import os
from typing import List, Optional

from qgis.PyQt.QtCore import QDate, QDateTime
from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry,
                       QgsProject,
                       QgsVectorLayer, QgsVectorFileWriter, QgsWkbTypes,
                       QgsEditorWidgetSetup, QgsMapLayer)

from .compat import (TYPE_BOOL, TYPE_DATE, TYPE_DATETIME,
                     TYPE_DOUBLE, TYPE_INT, TYPE_LONGLONG, TYPE_STRING,
                     DATE_ISO, DBF_MAX_FIELD_NAME, DBF_MAX_STRING, log, log_warning,
                     length_precision_for, qvariant_for)
from .mapping import MODE_CONSTANT, MODE_CRS, MODE_SEQUENCE, MODE_SOURCE

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "resources",
                              "field_length_overrides.json")

_overrides_cache = None


def field_length_overrides() -> dict:
    """Panjang field per nama, karena API tidak menyediakannya.

    Tidak satu pun dari 36 kunci respons featuretypegetbycode memuat
    panjang field. Nilai di sini diturunkan dari Buku 2 KUGI dan pedoman
    BIG. Field yang tidak tercantum jatuh ke default 254.
    """
    global _overrides_cache
    if _overrides_cache is None:
        try:
            with open(OVERRIDES_PATH, "r", encoding="utf-8") as handle:
                _overrides_cache = json.load(handle)
        except (OSError, ValueError) as exc:
            log_warning("Gagal membaca override panjang field: %s" % exc)
            _overrides_cache = {}
    return _overrides_cache


class BuildResult:
    def __init__(self):
        self.layer = None
        self.written = []
        self.messages = []
        self.ok = False


def build_fields(state) -> QgsFields:
    """Susun QgsFields: field KUGI sesuai urutan katalog, lalu field tambahan."""
    fields = QgsFields()
    overrides = field_length_overrides()

    for att in state.schema.attributes:
        qtype = qvariant_for(att.value_type)
        if qtype is None:
            continue
        requested = overrides.get(att.name)
        if state.mapping[att.name].mode == MODE_SOURCE:
            src_idx = state.source_fields.indexFromName(
                state.mapping[att.name].source)
            if src_idx >= 0 and qtype == TYPE_STRING:
                src_len = state.source_fields.at(src_idx).length()
                if src_len and src_len > 0:
                    requested = max(requested or 0, src_len)
        length, precision = length_precision_for(qtype, requested)
        name = att.name
        if state.shapefile_target and len(name) > DBF_MAX_FIELD_NAME:
            log("Nama field %s akan dipotong oleh OGR di shapefile" % name)
        field = QgsField(name, qtype)
        field.setLength(length)
        field.setPrecision(precision)
        field.setComment(att.definition)
        fields.append(field)

    for extra in state.extras:
        src_idx = state.source_fields.indexFromName(extra.source)
        if src_idx < 0:
            continue
        original = state.source_fields.at(src_idx)
        field = QgsField(extra.output, original.type())
        field.setLength(original.length())
        field.setPrecision(original.precision())
        if field.type() == TYPE_STRING and field.length() > DBF_MAX_STRING:
            field.setLength(DBF_MAX_STRING)
        fields.append(field)

    return fields


def srs_identifier(layer) -> str:
    """Identitas sistem referensi layer untuk field SRS_ID.

    authid() mengembalikan bentuk "EPSG:4326". Bila CRS tidak punya kode
    resmi, authid() kosong dan kita jatuh ke deskripsinya supaya kolom
    tidak dibiarkan hampa tanpa alasan.
    """
    crs = layer.crs()
    if not crs.isValid():
        return ""
    authid = crs.authid()
    if authid:
        return authid
    return crs.description() or ""


# Penanda "tidak ada nilai" yang muncul di data nyata. NaT berasal dari
# pandas, NaN dari perhitungan numerik, sisanya dari ekspor berbagai
# perkakas. Semuanya harus jadi NULL, bukan diteruskan apa adanya.
NULL_TEXTS = {"", "-", "nat", "nan", "none", "null", "nil", "n/a", "na",
              "<null>", "0000-00-00", "1900-01-01t00:00:00"}

# Format tanggal yang lazim ditemui pada data spasial Indonesia. Bentuk
# yyyyMMdd dipakai kolom UPDATED pada contoh di pedoman BIG halaman 29.
DATE_FORMATS = ("yyyy-MM-dd", "yyyyMMdd", "dd/MM/yyyy", "dd-MM-yyyy",
                "MM/dd/yyyy", "yyyy/MM/dd", "dd MMMM yyyy")


def _to_date(value, want_datetime=False):
    """Ubah nilai apa pun menjadi QDate atau QDateTime, atau None."""
    if isinstance(value, QDateTime):
        return value if want_datetime else value.date()
    if isinstance(value, QDate):
        return QDateTime(value) if want_datetime else value

    text = str(value).strip()
    iso = QDateTime.fromString(text, DATE_ISO)
    if iso.isValid():
        return iso if want_datetime else iso.date()

    for fmt in DATE_FORMATS:
        parsed = QDate.fromString(text, fmt)
        if parsed.isValid():
            return QDateTime(parsed) if want_datetime else parsed

    # Angka polos seperti 20161115 kadang tersimpan sebagai bilangan.
    digits = text.split(".")[0]
    if digits.isdigit() and len(digits) == 8:
        parsed = QDate.fromString(digits, "yyyyMMdd")
        if parsed.isValid():
            return QDateTime(parsed) if want_datetime else parsed

    return None


def _parse_number(text):
    """Baca angka dari teks, termasuk yang memakai konvensi Indonesia.

    Data Indonesia lazim menulis "7,5" untuk tujuh setengah dan "1.250,75"
    untuk seribu dua ratus lima puluh koma tujuh lima, kebalikan dari
    konvensi Inggris. Aturan yang dipakai:

      ada titik dan koma  -> yang paling kanan adalah pemisah desimal
      hanya koma          -> koma dianggap pemisah desimal
      hanya titik         -> titik dianggap pemisah desimal

    Kasus "1,250" memang ambigu: bisa seribu dua ratus lima puluh dalam
    konvensi Inggris, bisa satu koma dua lima nol dalam konvensi Indonesia.
    Dipilih tafsir Indonesia karena itu asal datanya, dan penafsiran ini
    dihitung lalu dilaporkan supaya bisa diperiksa user.
    """
    cleaned = str(text).strip().replace(" ", "")
    if not cleaned:
        return None, False

    interpreted = False
    dot = cleaned.rfind(".")
    comma = cleaned.rfind(",")

    if dot >= 0 and comma >= 0:
        if comma > dot:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
        interpreted = True
    elif comma >= 0:
        cleaned = cleaned.replace(",", ".")
        interpreted = True

    try:
        number = float(cleaned)
    except (TypeError, ValueError):
        return None, False
    if number != number or number in (float("inf"), float("-inf")):
        return None, False
    return number, interpreted


def _coerce(value, qtype):
    """Ubah nilai ke tipe target, kembalikan (nilai, berhasil).

    Kegagalan konversi harus berakhir sebagai NULL, tidak pernah sebagai
    nilai mentah yang diteruskan. Provider menolak SELURUH potongan fitur
    kalau satu nilai saja tidak bisa disimpan, jadi satu sel "NaT" di
    kolom tanggal sanggup menghanguskan 2000 baris sekaligus.
    """
    if value is None:
        return None, True
    try:
        if hasattr(value, "isNull") and value.isNull():
            return None, True
    except (AttributeError, TypeError):
        pass

    if isinstance(value, (QDate, QDateTime)):
        if qtype == TYPE_DATE:
            return _to_date(value), True
        if qtype == TYPE_DATETIME:
            return _to_date(value, want_datetime=True), True
        if qtype == TYPE_STRING:
            return value.toString(DATE_ISO), True

    text = str(value).strip()
    if text.lower() in NULL_TEXTS:
        return None, True

    try:
        if qtype == TYPE_STRING:
            return text, True
        if qtype in (TYPE_INT, TYPE_LONGLONG):
            number, _ = _parse_number(text)
            if number is None:
                return None, False
            # Pembulatan ke bilangan bulat membuang bagian pecahan, dan itu
            # kehilangan data yang harus terlihat, bukan diam-diam terjadi.
            return int(number), number == int(number)
        if qtype == TYPE_DOUBLE:
            number, _ = _parse_number(text)
            if number is None:
                return None, False
            return number, True
        if qtype in (TYPE_DATE, TYPE_DATETIME):
            parsed = _to_date(text, want_datetime=(qtype == TYPE_DATETIME))
            return (parsed, True) if parsed is not None else (None, False)
        if qtype == TYPE_BOOL:
            if text.lower() in ("1", "true", "ya", "yes", "y"):
                return True, True
            if text.lower() in ("0", "false", "tidak", "no", "n"):
                return False, True
            return None, False
        return text, True
    except (TypeError, ValueError):
        return None, False


ADD_CHUNK = 2000


def _align_geometry(geometry, target_wkb):
    """Selaraskan geometri dengan tipe layer tujuan.

    Provider memori menolak fitur yang tipe geometrinya tidak cocok, dan
    penolakan itu tidak memunculkan error. Tiga sumbu ketidakcocokan harus
    diurus sekaligus:

      multipart   shapefile kerap melaporkan Polygon padahal isinya
                  MultiPolygon
      dimensi Z   data dari ArcGIS hampir selalu ber-Z karena opsi
                  "Include z-dimension" aktif saat diekspor
      dimensi M   lebih jarang, tapi ikut menolak dengan cara yang sama

    Mengurus multipart saja tidak cukup. Layer 2D yang menerima geometri
    3D tetap menolak seluruh fiturnya.
    """
    if geometry is None or geometry.isNull():
        return None

    clone = QgsGeometry(geometry)

    if QgsWkbTypes.isMultiType(target_wkb) and not clone.isMultipart():
        clone.convertToMultiType()
    elif not QgsWkbTypes.isMultiType(target_wkb) and clone.isMultipart():
        clone.convertToSingleType()

    abstract = clone.get()
    if abstract is not None:
        if QgsWkbTypes.hasZ(clone.wkbType()) and not QgsWkbTypes.hasZ(target_wkb):
            abstract.dropZValue()
        elif not QgsWkbTypes.hasZ(clone.wkbType()) and QgsWkbTypes.hasZ(target_wkb):
            abstract.addZValue(0.0)
        if QgsWkbTypes.hasM(clone.wkbType()) and not QgsWkbTypes.hasM(target_wkb):
            abstract.dropMValue()
        elif not QgsWkbTypes.hasM(clone.wkbType()) and QgsWkbTypes.hasM(target_wkb):
            abstract.addMValue(0.0)

    return clone


def _memory_uri_candidates(source_layer):
    """Urutan tipe geometri yang dicoba untuk layer memori.

    Multipart didahulukan. Shapefile kerap melaporkan Polygon padahal
    isinya MultiPolygon, dan data dari ArcGIS sering ber-Z karena opsi
    "Include z-dimension" aktif saat diekspor. Layer memori single-part
    akan menolak geometri multipart, dan penolakan itu tidak memunculkan
    error apa pun: barisnya hilang begitu saja.
    """
    wkb = source_layer.wkbType()
    candidates = []
    try:
        candidates.append(QgsWkbTypes.displayString(QgsWkbTypes.multiType(wkb)))
    except (AttributeError, TypeError):
        pass
    candidates.append(QgsWkbTypes.displayString(wkb))
    fallback = {
        QgsWkbTypes.PointGeometry: "MultiPoint",
        QgsWkbTypes.LineGeometry: "MultiLineString",
        QgsWkbTypes.PolygonGeometry: "MultiPolygon",
    }
    candidates.append(fallback.get(source_layer.geometryType(), "MultiPoint"))

    seen = set()
    ordered = []
    for name in candidates:
        if name and name != "Unknown" and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _add_batch(provider, batch) -> bool:
    """Tambahkan sekumpulan fitur, kembalikan True bila berhasil."""
    outcome = provider.addFeatures(batch)
    if isinstance(outcome, tuple):
        outcome = outcome[0]
    return bool(outcome)


def _diagnose_add_failure(provider, features, source_layer, layer,
                          stored, expected) -> str:
    """Susun pesan yang menyebut sebabnya, bukan sekadar jumlahnya.

    Kalau semua fitur ditolak, satu fitur dicoba lagi sendirian supaya
    lastError() dari provider bisa dibaca. Tanpa ini user hanya tahu
    datanya hilang, tidak tahu kenapa.
    """
    bits = ["Hanya %d dari %d fitur masuk ke layer sementara." % (stored, expected)]
    bits.append("Geometri sumber %s, layer sementara %s." % (
        QgsWkbTypes.displayString(source_layer.wkbType()),
        QgsWkbTypes.displayString(layer.wkbType())))

    if stored == 0 and features:
        try:
            provider.addFeatures([features[0]])
        except Exception as exc:  # noqa: BLE001 - diagnosa, apa pun sebabnya
            bits.append("Percobaan satu fitur melempar %s." % exc)
        error = ""
        try:
            error = provider.lastError()
        except AttributeError:
            pass
        if error:
            bits.append("Provider: %s" % error)
        first = features[0].geometry()
        if first is not None and not first.isNull():
            bits.append("Geometri fitur pertama %s."
                        % QgsWkbTypes.displayString(first.wkbType()))
        else:
            bits.append("Fitur pertama tidak punya geometri.")
    return " ".join(bits)


def build_memory_layer(state, source_layer, feedback=None) -> BuildResult:
    """Bangun layer memori berisi skema KUGI plus field tambahan."""
    result = BuildResult()
    fields = build_fields(state)

    crs_authid = source_layer.crs().authid()
    layer = None
    tried = []
    for geom_name in _memory_uri_candidates(source_layer):
        uri = "%s?crs=%s" % (geom_name, crs_authid) if crs_authid else geom_name
        tried.append(uri)
        candidate = QgsVectorLayer(uri, state.schema.type_name, "memory")
        if candidate.isValid():
            layer = candidate
            break
    if layer is None:
        result.messages.append(
            "Gagal membuat layer sementara. Tipe yang dicoba: %s"
            % ", ".join(tried))
        return result

    target_wkb = layer.wkbType()

    provider = layer.dataProvider()
    provider.addAttributes(fields.toList())
    layer.updateFields()

    out_fields = layer.fields()
    kugi_names = [a.name for a in state.schema.attributes
                  if qvariant_for(a.value_type) is not None]

    conversion_failures = {}
    features = []
    counter = 0

    # SRS_ID diambil dari properti CRS layer masukan. Dipakai authid
    # ("EPSG:32749") karena itu bentuk yang tidak ambigu dan langsung bisa
    # dibaca ulang oleh QGIS maupun GDAL. Kalau serahan menuntut angka
    # telanjang, ubah ke crs.postgisSrid().
    #
    # Jangan sekali-kali memotongnya dengan str(crs).strip("<QgsCoordinate...>")
    # seperti script rujukan: strip() menghapus himpunan karakter, bukan awalan.
    srs_value = srs_identifier(source_layer)

    for src_feature in source_layer.getFeatures():
        counter += 1
        new_feature = QgsFeature(out_fields)

        geometry = _align_geometry(src_feature.geometry(), target_wkb)
        if geometry is not None:
            new_feature.setGeometry(geometry)

        for name in kugi_names:
            idx = out_fields.indexFromName(name)
            if idx < 0:
                continue
            spec = state.mapping[name]
            qtype = out_fields.at(idx).type()

            if spec.mode == MODE_SEQUENCE:
                new_feature.setAttribute(idx, counter)
            elif spec.mode == MODE_CRS:
                value, _ = _coerce(srs_value, qtype)
                new_feature.setAttribute(idx, value)
            elif spec.mode == MODE_CONSTANT:
                value, ok = _coerce(spec.constant, qtype)
                new_feature.setAttribute(idx, value)
                if not ok:
                    conversion_failures[name] = conversion_failures.get(name, 0) + 1
            elif spec.mode == MODE_SOURCE:
                raw = src_feature.attribute(spec.source)
                value, ok = _coerce(raw, qtype)
                if qtype == TYPE_STRING and value is not None:
                    limit = out_fields.at(idx).length()
                    if limit and len(value) > limit:
                        value = value[:limit]
                        conversion_failures[name] = conversion_failures.get(name, 0) + 1
                new_feature.setAttribute(idx, value)
                if not ok:
                    conversion_failures[name] = conversion_failures.get(name, 0) + 1
            else:
                new_feature.setAttribute(idx, None)

        for extra in state.extras:
            idx = out_fields.indexFromName(extra.output)
            if idx < 0:
                continue
            raw = src_feature.attribute(extra.source)
            value, ok = _coerce(raw, out_fields.at(idx).type())
            new_feature.setAttribute(idx, value)
            if not ok:
                conversion_failures[extra.output] = conversion_failures.get(
                    extra.output, 0) + 1

        features.append(new_feature)

        if feedback is not None and counter % 100 == 0:
            feedback(counter)

    # Ditambahkan bertahap. Provider memori bersifat semua-atau-tidak sama
    # sekali per panggilan, jadi satu fitur bermasalah di antara 16 ribu akan
    # menghanguskan seluruhnya kalau dikirim sekaligus. Dengan potongan,
    # yang gugur hanya potongannya.
    added = True
    rejected = []
    for start in range(0, len(features), ADD_CHUNK):
        batch = features[start:start + ADD_CHUNK]
        if not _add_batch(provider, batch):
            # Potongan gugur karena satu nilai yang tidak bisa disimpan.
            # Sisanya diselamatkan satu per satu, dan yang benar-benar
            # bermasalah dicatat alih-alih menyeret 1999 baris lain.
            added = False
            for feature in batch:
                if not _add_batch(provider, [feature]):
                    rejected.append(feature.id())
        if feedback is not None:
            feedback(min(start + len(batch), counter))
    layer.updateExtents()

    if rejected:
        result.messages.append(
            "%d fitur ditolak provider dan tidak ikut ke keluaran."
            % len(rejected))

    # Jumlahnya diperiksa, bukan diasumsikan. Kehilangan baris di sini
    # adalah kegagalan senyap yang paling mahal: berkas tetap terbentuk,
    # hanya isinya tidak ada.
    stored = provider.featureCount()
    if not added or stored != counter:
        result.messages.append(_diagnose_add_failure(
            provider, features, source_layer, layer, stored, counter))
        if stored == 0:
            return result

    apply_value_maps(layer, state.schema)

    for name, count in conversion_failures.items():
        result.messages.append(
            "Field %s: %d nilai gagal dikonversi atau terpotong" % (name, count))

    result.layer = layer
    result.ok = True
    return result


def apply_value_maps(layer, schema):
    """Pasang ValueMap untuk field berdomain.

    Inilah isi berkas .qml. User mengisi nilainya nanti lewat attribute
    table, dan ValueMap yang memberi mereka dropdown. Tanpa ini mereka
    harus mengetik angka 1 sampai 999 dari hafalan.

    QGIS 3 memakai list berisi dict satu entri agar urutan nilai terjaga.
    Kuncinya label yang dilihat user, nilainya kode yang tersimpan.
    """
    fields = layer.fields()
    applied = 0
    for att in schema.domain_attributes:
        idx = fields.indexFromName(att.name)
        if idx < 0:
            continue
        # Nilainya dicetak ke tipe field, bukan dibiarkan sebagai teks.
        # KLSRJL bertipe Integer sementara API mengirim "1" sebagai string.
        entries = [{label: code} for code, label in att.typed_domain]
        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup(
            "ValueMap", {"map": entries}))
        applied += 1
    return applied


def _save_style(layer, target_path) -> Optional[str]:
    """Tulis .qml dengan basename yang sama seperti berkas data."""
    qml_path = os.path.splitext(target_path)[0] + ".qml"
    try:
        categories = (QgsMapLayer.Fields | QgsMapLayer.Forms
                      | QgsMapLayer.Symbology)
        layer.saveNamedStyle(qml_path, categories)
    except TypeError:
        layer.saveNamedStyle(qml_path)
    if os.path.isfile(qml_path):
        return qml_path
    return None


def write_outputs(layer, folder, basename, formats, write_qml=True) -> BuildResult:
    """Tulis layer ke shapefile dan atau geopackage."""
    result = BuildResult()
    result.layer = layer

    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError as exc:
            result.messages.append("Folder keluaran tidak bisa dibuat: %s" % exc)
            return result

    transform_context = QgsProject.instance().transformContext()

    targets = []
    if "shp" in formats:
        targets.append(("ESRI Shapefile", os.path.join(folder, basename + ".shp")))
    if "gpkg" in formats:
        targets.append(("GPKG", os.path.join(folder, basename + ".gpkg")))

    for driver, path in targets:
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        if driver == "ESRI Shapefile":
            # Tulis .cpg supaya nama wilayah non-ASCII terbaca benar.
            options.layerOptions = ["ENCODING=UTF-8"]
        else:
            options.layerName = basename
            options.actionOnExistingFile = (
                QgsVectorFileWriter.CreateOrOverwriteFile)

        try:
            written = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, path, transform_context, options)
        except AttributeError:
            written = QgsVectorFileWriter.writeAsVectorFormatV2(
                layer, path, transform_context, options)

        error = written[0]
        message = written[1] if len(written) > 1 else ""
        if error != QgsVectorFileWriter.NoError:
            result.messages.append("Gagal menulis %s: %s" % (path, message))
            continue

        result.written.append(path)

        if write_qml:
            qml = _save_style(layer, path)
            if qml:
                result.written.append(qml)
            else:
                result.messages.append("Berkas .qml gagal ditulis untuk %s" % path)

    result.ok = bool(result.written)
    return result
