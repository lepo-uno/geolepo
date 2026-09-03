# -*- coding: utf-8 -*-
"""Mesin QC.

Fungsi murni tanpa Qt UI, sehingga bisa dipanggil dari dialog, dari
Processing algorithm, maupun dari unit test tanpa GUI. Menerima
QgsVectorLayer apa pun: hasil standardisasi di memori, layer di project,
atau berkas dari disk.
"""

from typing import List

from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsWkbTypes

from .compat import (DBF_MAX_FIELD_NAME, DBF_MAX_STRING, qvariant_for,
                     types_compatible, type_display_name)
from .kugi_model import (VALUE_REQUIRED_FIELDS, QGIS_GEOMETRY_TO_TOKEN,
                         GEOMETRY_LABEL, normalize_code, strip_en)

# Aturan tiga tingkat, dipegang konsisten:
#
#   Error       keluaran tidak berdiri sebagai data KUGI. Kolom yang tidak
#               ada, nama yang beda huruf besar-kecil, geometri yang tidak
#               cocok dengan unsur, OBJECTID duplikat, dan gagal tulis.
#   Peringatan  nilai bertentangan dengan katalog tapi datanya tetap
#               terbentuk. Di luar domain, tipe tidak cocok, kelewat
#               panjang, FCODE keliru, SRS_ID beda dari CRS berkas.
#   Info        seluruh urusan keterisian. Berapa fitur yang terisi, kolom
#               mana yang kosong. Angka saja, tanpa penilaian, karena skema
#               KUGI memang menyediakan banyak kolom yang sengaja dikosongkan
#               menurut pedoman BIG halaman 40.
LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"

LEVEL_ORDER = {LEVEL_ERROR: 0, LEVEL_WARNING: 1, LEVEL_INFO: 2}
LEVEL_LABEL = {LEVEL_ERROR: "Error", LEVEL_WARNING: "Peringatan",
               LEVEL_INFO: "Info"}

MAX_TRACKED_FEATURES = 5000


class Issue:
    def __init__(self, level, field, message, feature_ids=None):
        self.level = level
        self.field = field
        self.message = message
        self.feature_ids = feature_ids or []

    @property
    def affected(self) -> int:
        return len(self.feature_ids)

    def as_row(self):
        return [LEVEL_LABEL[self.level], self.field, self.message,
                str(self.affected) if self.feature_ids else ""]


def _is_null(value) -> bool:
    if value is None:
        return True
    try:
        if hasattr(value, "isNull") and value.isNull():
            return True
    except (AttributeError, TypeError):
        pass
    return str(value).strip() == ""


def validate(layer, schema, shapefile_target=True) -> List[Issue]:
    """Bandingkan layer terhadap skema KUGI."""
    issues = []
    if layer is None or not layer.isValid():
        return [Issue(LEVEL_ERROR, "", "Layer tidak valid")]
    if schema is None:
        return [Issue(LEVEL_ERROR, "", "Skema KUGI belum dipilih")]

    fields = layer.fields()
    present = {f.name(): f for f in fields}
    present_upper = {n.upper(): n for n in present}

    expected = [a for a in schema.attributes
                if qvariant_for(a.value_type) is not None]
    expected_names = {a.name for a in expected}

    issues.extend(_check_geometry(layer, schema))
    issues.extend(_check_structure(expected, present, present_upper,
                                   shapefile_target))
    issues.extend(_check_values(layer, schema, expected, present))
    issues.extend(_check_srs(layer, present))
    issues.extend(_check_extras(fields, expected_names, shapefile_target))

    issues.sort(key=lambda i: (LEVEL_ORDER[i.level], i.field))
    return issues


def _check_geometry(layer, schema) -> List[Issue]:
    token = schema.geometry_token
    if not token:
        return []
    actual = QGIS_GEOMETRY_TO_TOKEN.get(layer.geometryType())
    if actual and actual != token:
        return [Issue(
            LEVEL_ERROR, "",
            "Geometri layer %s tidak cocok dengan unsur %s yang bertipe %s" % (
                GEOMETRY_LABEL.get(actual, actual), schema.type_name,
                GEOMETRY_LABEL.get(token, token)))]
    return []


def _check_structure(expected, present, present_upper, shapefile_target):
    issues = []
    for att in expected:
        if att.name in present:
            field = present[att.name]
            target_type = qvariant_for(att.value_type)
            if target_type is not None and field.type() != target_type:
                if not types_compatible(field.type(), target_type):
                    issues.append(Issue(
                        LEVEL_WARNING, att.name,
                        "Tipe data %s, seharusnya %s" % (
                            type_display_name(field.type()), att.value_type)))
                else:
                    issues.append(Issue(
                        LEVEL_INFO, att.name,
                        "Tipe data %s, katalog menyebut %s" % (
                            type_display_name(field.type()), att.value_type)))
            continue

        alias = present_upper.get(att.name.upper())
        if alias:
            issues.append(Issue(
                LEVEL_ERROR, alias,
                "Nama field berbeda huruf besar-kecil, seharusnya %s"
                % att.name))
            continue

        # Kolom hilang selalu Error, untuk field mana pun. Skema KUGI adalah
        # kontrak lengkap, jadi kolom yang tidak ada berarti data belum
        # sesuai standar. Penilaian ini murni dari katalog, tanpa tebakan.
        issues.append(Issue(LEVEL_ERROR, att.name, "Field KUGI tidak ditemukan"))

    if shapefile_target:
        for name in present:
            if len(name) > DBF_MAX_FIELD_NAME:
                issues.append(Issue(
                    LEVEL_WARNING, name,
                    "Nama melebihi %d karakter, akan dipotong di shapefile"
                    % DBF_MAX_FIELD_NAME))
    return issues


def _check_values(layer, schema, expected, present) -> List[Issue]:
    """Satu kali iterasi fitur untuk semua pemeriksaan berbasis nilai.

    Indeks kolom dihitung sekali di depan dan tiap fitur dibaca lewat
    attributes() sekali saja, bukan attribute(nama) berulang kali. Untuk
    33 kolom pada 83.000 fitur, selisihnya jutaan pencarian nama.
    """
    issues = []
    fields = layer.fields()

    def index_of(name):
        return fields.indexFromName(name)

    present_kugi = [a for a in expected if a.name in present]
    idx = {a.name: index_of(a.name) for a in present_kugi}
    idx = {k: v for k, v in idx.items() if v >= 0}

    domain_atts = [a for a in present_kugi if a.has_domain and a.name in idx]
    string_atts = [a for a in present_kugi
                   if a.name in idx and present[a.name].type() == QVariant.String]

    domain_sets = {a.name: a.normalized_domain_values() for a in domain_atts}
    length_limits = {}
    for att in string_atts:
        limit = present[att.name].length()
        length_limits[att.name] = limit if limit and limit > 0 else DBF_MAX_STRING

    domain_bad = {a.name: [] for a in domain_atts}
    length_bad = {a.name: [] for a in string_atts}
    nonnull = {name: 0 for name in idx}

    check_objectid = "OBJECTID" in idx
    check_fcode = "FCODE" in idx
    objectid_seen = {}
    objectid_null = []
    objectid_dup = []
    fcode_values = {}
    total = 0

    for feature in layer.getFeatures():
        total += 1
        fid = feature.id()
        values = feature.attributes()

        for name, position in idx.items():
            if position < len(values) and not _is_null(values[position]):
                nonnull[name] += 1

        for att in domain_atts:
            value = values[idx[att.name]]
            if _is_null(value):
                continue
            if normalize_code(strip_en(value)) not in domain_sets[att.name]:
                if len(domain_bad[att.name]) < MAX_TRACKED_FEATURES:
                    domain_bad[att.name].append(fid)

        for att in string_atts:
            value = values[idx[att.name]]
            if _is_null(value):
                continue
            if len(str(value)) > length_limits[att.name]:
                if len(length_bad[att.name]) < MAX_TRACKED_FEATURES:
                    length_bad[att.name].append(fid)

        if check_objectid:
            value = values[idx["OBJECTID"]]
            if _is_null(value):
                objectid_null.append(fid)
            else:
                key = str(value)
                if key in objectid_seen:
                    if len(objectid_dup) < MAX_TRACKED_FEATURES:
                        objectid_dup.append(fid)
                else:
                    objectid_seen[key] = fid

        if check_fcode:
            fcode_values.setdefault(strip_en(values[idx["FCODE"]]), []).append(fid)

    for att in domain_atts:
        ids = domain_bad[att.name]
        if ids:
            sample = ", ".join("%s=%s" % (c, l) for c, l in att.domain[:3])
            issues.append(Issue(
                LEVEL_WARNING, att.name,
                "Nilai di luar domain %s (%s%s)" % (
                    att.lv_code or "-", sample,
                    ", ..." if len(att.domain) > 3 else ""),
                ids))

    for att in string_atts:
        ids = length_bad[att.name]
        if ids:
            issues.append(Issue(
                LEVEL_WARNING, att.name,
                "Nilai melebihi panjang %d karakter" % length_limits[att.name],
                ids))

    # Kolom kosong seluruhnya dilaporkan sebagai SATU baris Info, bukan satu
    # baris per kolom. Skema KUGI memang menyediakan banyak kolom yang sengaja
    # dikosongkan: pedoman BIG halaman 40 mencantumkan query null untuk
    # METADATA, SRS_ID, seluruh KD*BPS, KD*PUM, WADM*, dan WIAD*. Melaporkan
    # masing-masing akan menenggelamkan temuan yang benar-benar penting.
    if total:
        empty = sorted(name for name, count in nonnull.items()
                       if count == 0 and name not in VALUE_REQUIRED_FIELDS)
        if empty:
            preview = ", ".join(empty[:8])
            if len(empty) > 8:
                preview += ", dan %d lainnya" % (len(empty) - 8)
            issues.append(Issue(
                LEVEL_INFO, "",
                "%d kolom KUGI kosong di seluruh fitur: %s. Ini sah menurut "
                "pedoman selama kolomnya ada." % (len(empty), preview)))

    for name in VALUE_REQUIRED_FIELDS:
        if name == "FCODE" or name not in idx or not total:
            continue
        filled = nonnull[name]
        if filled < total:
            issues.append(Issue(
                LEVEL_INFO, name,
                "Terisi %d dari %d fitur" % (filled, total)))

    if check_objectid:
        if objectid_null:
            issues.append(Issue(LEVEL_INFO, "OBJECTID",
                                "Terisi %d dari %d fitur"
                                % (total - len(objectid_null), total),
                                objectid_null))
        if objectid_dup:
            issues.append(Issue(LEVEL_ERROR, "OBJECTID",
                                "Nilai duplikat, pengenal baris harus unik",
                                objectid_dup))

    if check_fcode:
        real = {k: v for k, v in fcode_values.items() if k not in ("", "-")}
        if len(real) > 1:
            issues.append(Issue(
                LEVEL_WARNING, "FCODE",
                "Berisi %d nilai berbeda, seharusnya seragam" % len(real),
                [ids[0] for ids in real.values()]))
        elif real:
            only = list(real.keys())[0]
            if only != schema.code:
                issues.append(Issue(
                    LEVEL_WARNING, "FCODE",
                    "Bernilai %s, seharusnya %s" % (only, schema.code)))
        empty_fcode = fcode_values.get("", []) + fcode_values.get("-", [])
        if empty_fcode:
            issues.append(Issue(LEVEL_INFO, "FCODE",
                                "Terisi %d dari %d fitur"
                                % (total - len(empty_fcode), total),
                                empty_fcode))

    return issues


def _check_srs(layer, present) -> List[Issue]:
    """Bandingkan isi SRS_ID dengan CRS layer yang sebenarnya.

    Bukan pelanggaran standar, tapi SRS_ID yang tidak cocok dengan CRS
    berkasnya adalah sumber kebingungan klasik saat data dipertukarkan.
    """
    if "SRS_ID" not in present:
        return []
    crs = layer.crs()
    authid = crs.authid() if crs.isValid() else ""
    values = set()
    scanned = 0
    for feature in layer.getFeatures():
        scanned += 1
        value = strip_en(feature.attribute("SRS_ID"))
        if value:
            values.add(value)
        # Dibatasi supaya tidak menambah satu lintasan penuh atas layer besar.
        # Ketidakseragaman SRS_ID praktis selalu terlihat di ribuan fitur awal.
        if len(values) > 4 or scanned >= MAX_TRACKED_FEATURES:
            break
    if not values:
        return [Issue(LEVEL_INFO, "SRS_ID",
                      "Kosong. Bisa diisi dari CRS layer (%s)."
                      % (authid or "tidak dikenali"))]
    if len(values) > 1:
        return [Issue(LEVEL_WARNING, "SRS_ID",
                      "Berisi lebih dari satu nilai, seharusnya seragam")]
    only = values.pop()
    if authid and only not in (authid, authid.split(":")[-1]):
        return [Issue(LEVEL_WARNING, "SRS_ID",
                      "Bernilai %s sedangkan CRS layer %s" % (only, authid))]
    return []


def _check_extras(fields, expected_names, shapefile_target) -> List[Issue]:
    issues = []
    extras = [f.name() for f in fields if f.name() not in expected_names]
    if extras:
        preview = ", ".join(extras[:6])
        if len(extras) > 6:
            preview += ", dan %d lainnya" % (len(extras) - 6)
        issues.append(Issue(
            LEVEL_INFO, "",
            "%d kolom di luar skema KUGI dipertahankan: %s"
            % (len(extras), preview)))
    return issues


def summarize(issues):
    counts = {LEVEL_ERROR: 0, LEVEL_WARNING: 0, LEVEL_INFO: 0}
    for issue in issues:
        counts[issue.level] += 1
    return counts
