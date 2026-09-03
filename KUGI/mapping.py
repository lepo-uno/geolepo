# -*- coding: utf-8 -*-
"""Model status pemetaan.


    mapping : dict[str, FieldSource]   field KUGI, kunci tetap
    extras  : list[ExtraField]         kolom di luar skema, berurutan
"""

import re
from typing import Dict, List, Optional

from .compat import (DBF_MAX_FIELD_NAME, qvariant_for, types_compatible,
                     type_display_name)
from .kugi_model import VALUE_REQUIRED_FIELDS

MODE_EMPTY = "empty"
MODE_SOURCE = "source"
MODE_CONSTANT = "constant"
MODE_SEQUENCE = "sequence"
MODE_CRS = "crs"          # diisi dari CRS layer masukan

MODE_LABEL = {
    MODE_EMPTY: "kosong",
    MODE_SOURCE: "kolom eksisting",
    MODE_CONSTANT: "konstanta",
    MODE_SEQUENCE: "nomor urut",
    MODE_CRS: "CRS layer",
}

# Field yang otomatis terisi oleh plugin dan tidak perlu dipetakan.
# SRS_ID diambil dari CRS layer masukan, bukan dikosongkan, karena
# identitas sistem referensi sudah tersedia di properti layer.
AUTO_FIELDS = {"FCODE": MODE_CONSTANT, "OBJECTID": MODE_SEQUENCE,
               "SRS_ID": MODE_CRS}

# Field yang TIDAK boleh dipetakan dari kolom mana pun.
#
#   FCODE     penanda identitas unsur. Menimpanya dengan kolom lain membuat
#             keluaran mengaku sebagai unsur yang bukan dirinya.
#   OBJECTID  pengenal baris harus unik dan dijamin plugin. Kolom sumber
#             bernama OBJECTID juga disembunyikan, jadi tidak ada yang bisa
#             dipilih selain nomor urut.
LOCKED_FIELDS = ("FCODE", "OBJECTID")


def is_objectid_column(name: str) -> bool:
    """Apakah nama kolom merupakan OBJECTID warisan ArcGIS.

    Perbandingan dinormalkan supaya OBJECTID, OBJECT_ID, objectid, dan
    OBJECTID_1 semuanya tertangkap. Bentuk berakhiran angka itu nyata,
    terlihat di pedoman BIG halaman 44 sebagai hasil ArcGIS menambahkan
    kolom kedua saat nama pertama sudah terpakai.
    """
    flat = "".join(ch for ch in str(name).upper() if ch.isalnum())
    return "OBJECTID" in flat


# Batas pemindaian keterisian. Di atas ini angkanya jadi perkiraan dan
# ditandai dengan tilde, supaya layer besar tidak membuat dialog membeku.
FILL_SCAN_CAP = 20000


def is_blank(value) -> bool:
    """Nilai dianggap kosong bila None, QVariant null, atau string hampa."""
    if value is None:
        return True
    try:
        if hasattr(value, "isNull") and value.isNull():
            return True
    except (AttributeError, TypeError):
        pass
    return str(value).strip() == ""


def sanitize_name(name: str, limit: Optional[int] = None) -> str:
    """Bersihkan nama kolom agar aman di DBF."""
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "FIELD"
    if text[0].isdigit():
        text = "F" + text
    if limit:
        text = text[:limit]
    return text


class FieldSource:
    """Bagaimana satu field KUGI diisi."""

    def __init__(self, mode: str = MODE_EMPTY, source: str = "",
                 constant: str = ""):
        self.mode = mode
        self.source = source
        self.constant = constant

    def describe(self) -> str:
        if self.mode == MODE_SOURCE:
            return self.source
        if self.mode == MODE_CONSTANT:
            return self.constant
        return MODE_LABEL.get(self.mode, "kosong")

    def to_dict(self) -> dict:
        return {"mode": self.mode, "source": self.source,
                "constant": self.constant}

    @classmethod
    def from_dict(cls, data: dict) -> "FieldSource":
        return cls(data.get("mode", MODE_EMPTY), data.get("source", ""),
                   data.get("constant", ""))


class ExtraField:
    """Kolom eksisting yang dibawa apa adanya ke keluaran."""

    def __init__(self, source: str, output: str = ""):
        self.source = source
        self.output = output or sanitize_name(source)

    def to_dict(self) -> dict:
        return {"source": self.source, "output": self.output}

    @classmethod
    def from_dict(cls, data: dict) -> "ExtraField":
        return cls(data.get("source", ""), data.get("output", ""))


class MappingIssue:
    LEVEL_ERROR = "error"
    LEVEL_WARNING = "warning"

    def __init__(self, level: str, target: str, message: str):
        self.level = level
        self.target = target
        self.message = message


class MappingState:
    """Status pemetaan untuk satu pasangan layer dan unsur."""

    def __init__(self, schema, source_fields):
        """source_fields adalah QgsFields dari layer masukan."""
        self.schema = schema
        self.source_fields = source_fields
        self.mapping: Dict[str, FieldSource] = {}
        self.extras: List[ExtraField] = []
        # Ditampilkan di dropdown sebagai "<otomatis: EPSG:32749>" supaya
        # user tahu nilai apa yang akan masuk, bukan sekadar kata otomatis.
        self.crs_identifier = ""
        self.shapefile_target = True
        # Keterisian data per kolom sumber. Diisi sekali lewat
        # compute_fill_counts() saat layer dipilih, bukan tiap pemetaan berubah.
        self.fill_counts: Dict[str, int] = {}
        self.feature_total = 0
        self.fill_estimated = False
        self.reset()

    # ------------------------------------------------------------ keterisian

    def compute_fill_counts(self, layer, cap: int = FILL_SCAN_CAP):
        """Hitung berapa fitur yang punya nilai di tiap kolom sumber.

        Satu lintasan saja, dibatasi cap fitur untuk layer besar. Angka ini
        murni informasi: menampilkan kolom mana yang benar-benar berisi data
        sebelum user memutuskan memetakannya. Tidak pernah menjadi
        peringatan maupun error.
        """
        names = self.source_field_names()
        counts = {name: 0 for name in names}
        indexes = {name: self.source_fields.indexFromName(name) for name in names}
        indexes = {k: v for k, v in indexes.items() if v >= 0}

        scanned = 0
        for feature in layer.getFeatures():
            values = feature.attributes()
            for name, position in indexes.items():
                if position < len(values) and not is_blank(values[position]):
                    counts[name] += 1
            scanned += 1
            if scanned >= cap:
                break

        self.fill_counts = counts
        self.feature_total = scanned
        self.fill_estimated = scanned >= cap and layer.featureCount() > scanned

    def source_filled(self, source_name: str) -> int:
        return self.fill_counts.get(source_name, 0)

    def target_filled(self, kugi_field: str) -> int:
        """Keterisian field keluaran, diturunkan bukan diukur ulang.

        Field yang disalin dari kolom mewarisi angka kolom itu. Konstanta,
        nomor urut, dan CRS selalu penuh karena plugin yang mengisinya.
        """
        spec = self.mapping.get(kugi_field)
        if spec is None or spec.mode == MODE_EMPTY:
            return 0
        if spec.mode == MODE_SOURCE:
            return self.source_filled(spec.source)
        return self.feature_total

    def fill_text(self, count: int) -> str:
        if not self.feature_total:
            return ""
        return ("~%d" % count) if self.fill_estimated else str(count)

    # ---------------------------------------------------------------- dasar

    def reset(self):
        self.mapping = {}
        self.extras = []
        for att in self.schema.attributes:
            self.mapping[att.name] = self._default_for(att.name)

    def source_field_names(self) -> List[str]:
        """Kolom sumber yang boleh dipilih user.

        Kolom OBJECTID warisan disaring keluar. Penomoran barisnya dijamin
        plugin, jadi menawarkan kolom lama hanya membuka peluang penomoran
        yang tidak unik.
        """
        return [f.name() for f in self.source_fields
                if not is_objectid_column(f.name())]

    def hidden_source_count(self) -> int:
        """Berapa kolom sumber yang disembunyikan, untuk dilaporkan ke user."""
        return sum(1 for f in self.source_fields
                   if is_objectid_column(f.name()))

    def source_type(self, name: str):
        idx = self.source_fields.indexFromName(name)
        if idx < 0:
            return None
        return self.source_fields.at(idx).type()

    def source_type_name(self, name: str) -> str:
        qtype = self.source_type(name)
        return type_display_name(qtype) if qtype is not None else "?"

    # ------------------------------------------------------------ pemetaan

    def target_of(self, source_name: str) -> str:
        for field, spec in self.mapping.items():
            if spec.mode == MODE_SOURCE and spec.source == source_name:
                return field
        return ""

    def is_extra(self, source_name: str) -> bool:
        return any(e.source == source_name for e in self.extras)

    def is_locked(self, kugi_field: str) -> bool:
        return kugi_field in LOCKED_FIELDS

    def assign(self, kugi_field: str, source_name: str):
        if self.is_locked(kugi_field):
            return
        """Pasangkan kolom eksisting ke satu field KUGI.

        Satu kolom eksisting hanya boleh menuju satu field KUGI. Bila
        kolom itu sudah dipakai di tempat lain, pemetaan lama dilepas.
        """
        if kugi_field not in self.mapping:
            return
        previous = self.target_of(source_name)
        if previous and previous != kugi_field:
            self.clear(previous)
        self.mapping[kugi_field] = FieldSource(MODE_SOURCE, source=source_name)

    def set_constant(self, kugi_field: str, value: str):
        if kugi_field in self.mapping:
            self.mapping[kugi_field] = FieldSource(MODE_CONSTANT,
                                                   constant=value)

    def clear(self, kugi_field: str):
        if kugi_field not in self.mapping:
            return
        self.mapping[kugi_field] = self._default_for(kugi_field)

    def _default_for(self, kugi_field: str) -> FieldSource:
        """Nilai bawaan sebuah field bila tidak dipetakan dari kolom."""
        default = AUTO_FIELDS.get(kugi_field)
        if default == MODE_CONSTANT:
            return FieldSource(MODE_CONSTANT, constant=self.schema.code)
        if default == MODE_SEQUENCE:
            return FieldSource(MODE_SEQUENCE)
        if default == MODE_CRS:
            return FieldSource(MODE_CRS)
        return FieldSource(MODE_EMPTY)

    def clear_all(self):
        for name in list(self.mapping.keys()):
            self.clear(name)

    # --------------------------------------------------------- field ekstra

    def add_extra(self, source_name: str) -> Optional[ExtraField]:
        if self.is_extra(source_name):
            return None
        extra = ExtraField(source_name, self.suggest_extra_name(source_name))
        self.extras.append(extra)
        return extra

    def remove_extra(self, source_name: str):
        self.extras = [e for e in self.extras if e.source != source_name]

    def add_all_remaining_extras(self) -> int:
        added = 0
        for name in self.source_field_names():
            if self.target_of(name) or self.is_extra(name):
                continue
            if self.add_extra(name):
                added += 1
        return added

    def suggest_extra_name(self, source_name: str) -> str:
        """Usulkan nama keluaran yang tidak bertabrakan.

        Panjang maksimum bergantung pada format keluaran. Bila shapefile
        termasuk target, batas 10 karakter berlaku untuk KEDUA format
        supaya skema keduanya identik.
        """
        limit = DBF_MAX_FIELD_NAME if self.shapefile_target else None
        base = sanitize_name(source_name, limit)
        taken = self._taken_names(exclude_source=source_name)
        if base.upper() not in taken:
            return base
        for i in range(1, 100):
            suffix = "_%d" % i
            trimmed = base[:limit - len(suffix)] if limit else base
            candidate = trimmed + suffix
            if candidate.upper() not in taken:
                return candidate
        return base

    def _taken_names(self, exclude_source: str = "") -> set:
        taken = {name.upper() for name in self.mapping.keys()}
        for extra in self.extras:
            if extra.source == exclude_source:
                continue
            taken.add(extra.output.upper())
        return taken

    def rename_extra(self, source_name: str, new_name: str):
        for extra in self.extras:
            if extra.source == source_name:
                limit = DBF_MAX_FIELD_NAME if self.shapefile_target else None
                extra.output = sanitize_name(new_name, limit)
                return

    def refresh_extra_names(self):
        """Hitung ulang nama keluaran setelah format target berubah.

        Ini keterkaitan lintas bagian dialog yang mudah terlewat: mencentang
        atau melepas Shapefile mengubah batas panjang nama seluruh field
        tambahan.
        """
        for extra in self.extras:
            extra.output = self.suggest_extra_name(extra.source)

    # ----------------------------------------------------- pencocokan otomatis

    def auto_match(self) -> int:
        """Cocokkan nama persis lebih dulu, lalu longgar."""
        matched = 0
        available = [n for n in self.source_field_names()
                     if not self.target_of(n)]
        kugi_names = [a.name for a in self.schema.attributes]

        lookup = {}
        for name in available:
            lookup.setdefault(name.upper(), name)

        for field in kugi_names:
            if self.mapping[field].mode != MODE_EMPTY:
                continue
            hit = lookup.get(field.upper())
            if hit and not self.target_of(hit):
                self.assign(field, hit)
                matched += 1

        loose = {}
        for name in self.source_field_names():
            if self.target_of(name):
                continue
            key = re.sub(r"[^A-Z0-9]", "", name.upper())
            loose.setdefault(key, name)

        for field in kugi_names:
            if self.mapping[field].mode != MODE_EMPTY:
                continue
            key = re.sub(r"[^A-Z0-9]", "", field.upper())
            hit = loose.get(key)
            if hit and not self.target_of(hit):
                self.assign(field, hit)
                matched += 1

        return matched

    # ------------------------------------------------------------- validasi

    def validate(self) -> List[MappingIssue]:
        issues = []

        used = {}
        for field, spec in self.mapping.items():
            if spec.mode != MODE_SOURCE:
                continue
            used.setdefault(spec.source, []).append(field)
        for source, targets in used.items():
            if len(targets) > 1:
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_ERROR, ", ".join(targets),
                    "Kolom %s dipetakan ke lebih dari satu field" % source))

        for field, spec in self.mapping.items():
            if spec.mode != MODE_SOURCE:
                continue
            att = self.schema.attribute(field)
            if att is None:
                continue
            target_type = qvariant_for(att.value_type)
            source_type = self.source_type(spec.source)
            if target_type is None or source_type is None:
                continue
            if not types_compatible(source_type, target_type):
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_WARNING, field,
                    "Tipe %s dari kolom %s tidak cocok dengan %s" % (
                        type_display_name(source_type), spec.source,
                        att.value_type)))

        kugi_upper = {name.upper() for name in self.mapping.keys()}
        limit = DBF_MAX_FIELD_NAME if self.shapefile_target else None
        seen = {}
        for extra in self.extras:
            key = extra.output.upper()

            # Kolom eksisting yang namanya sama dengan field KUGI otomatis
            # diberi akhiran angka supaya keluaran tetap sah. Penggantian itu
            # harus terlihat, bukan senyap seperti yang dilakukan OGR.
            natural = sanitize_name(extra.source, limit)
            if natural.upper() in kugi_upper and key != natural.upper():
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_WARNING, extra.output,
                    "Kolom '%s' bernama sama dengan field KUGI, dinamai ulang "
                    "menjadi %s" % (extra.source, extra.output)))

            if key in kugi_upper:
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_ERROR, extra.output,
                    "Nama field tambahan bertabrakan dengan field KUGI. "
                    "Ganti namanya."))
            if key in seen:
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_ERROR, extra.output,
                    "Nama field tambahan bentrok dengan kolom %s" % seen[key]))
            seen[key] = extra.source
            if self.shapefile_target and len(extra.output) > DBF_MAX_FIELD_NAME:
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_WARNING, extra.output,
                    "Nama melebihi %d karakter, akan dipotong di shapefile"
                    % DBF_MAX_FIELD_NAME))

        # Hanya FCODE dan NAMOBJ yang isinya wajib. FCODE selalu terisi
        # otomatis, jadi praktis ini hanya menyentuh NAMOBJ. METADATA dan
        # SRS_ID sengaja tidak diperiksa: pedoman BIG halaman 40 memang
        # mencantumkan METADATA dengan query null, dan SRS_ID kini diisi
        # sendiri dari CRS layer.
        for name in VALUE_REQUIRED_FIELDS:
            spec = self.mapping.get(name)
            if spec is not None and spec.mode == MODE_EMPTY:
                issues.append(MappingIssue(
                    MappingIssue.LEVEL_WARNING, name,
                    "Isinya wajib menurut standar, tapi belum dipetakan"))

        return issues

    # ------------------------------------------------------------- template

    def to_template(self) -> dict:
        return {
            "version": 1,
            "schema_code": self.schema.code,
            "schema_name": self.schema.type_name,
            "mapping": {k: v.to_dict() for k, v in self.mapping.items()},
            "extras": [e.to_dict() for e in self.extras],
        }

    def load_template(self, data: dict) -> int:
        """Terapkan template. Hanya field dan kolom yang ada yang dipakai."""
        applied = 0
        available = set(self.source_field_names())
        for field, spec_data in (data.get("mapping") or {}).items():
            if field not in self.mapping:
                continue
            spec = FieldSource.from_dict(spec_data)
            if spec.mode == MODE_SOURCE and spec.source not in available:
                continue
            self.mapping[field] = spec
            applied += 1
        self.extras = []
        for extra_data in (data.get("extras") or []):
            extra = ExtraField.from_dict(extra_data)
            if extra.source in available:
                self.extras.append(extra)
                applied += 1
        self.refresh_extra_names()
        return applied

    # -------------------------------------------------------------- statistik

    def filled_count(self) -> int:
        return sum(1 for s in self.mapping.values() if s.mode != MODE_EMPTY)

    def unmapped_sources(self) -> List[str]:
        return [n for n in self.source_field_names()
                if not self.target_of(n) and not self.is_extra(n)]
