# -*- coding: utf-8 -*-
"""Lapisan kompatibilitas.

Semua penggunaan QVariant dikurung di sini supaya migrasi ke QGIS 4 / Qt6
(yang memakai QMetaType) hanya menyentuh satu berkas.

Target minimum: QGIS 3.28 LTR, Python 3.9.
"""

from qgis.PyQt.QtCore import QVariant
from qgis.core import Qgis, QgsMessageLog

PLUGIN_NAME = "KUGI Converter"

QGIS_VERSION = Qgis.QGIS_VERSION_INT


def log(message, level=Qgis.Info):
    QgsMessageLog.logMessage(str(message), PLUGIN_NAME, level)


def log_warning(message):
    log(message, Qgis.Warning)


# Nilai faValueType yang muncul di API KUGI, dipetakan ke tipe field QGIS.
# Sumber verifikasi: dump featuretypegetbycode?code=CA02040160 (97 baris,
# 34 atribut) yang memuat Integer, String, Double, OID, Date, dan Geometry.
_TYPE_MAP = {
    "String": QVariant.String,
    "Integer": QVariant.Int,
    "Int64": QVariant.LongLong,
    "Double": QVariant.Double,
    "Date": QVariant.Date,
    "DateTime": QVariant.DateTime,
    "Boolean": QVariant.Bool,
    # OBJECTID dikelola ArcGIS. Di sini diperlakukan sebagai Integer biasa
    # dan diisi nomor urut bila tidak dipetakan dari kolom eksisting.
    "OID": QVariant.Int,
}

from .kugi_model import NON_ATTRIBUTE_TYPES  # noqa: F401

# Batas keras format keluaran.
DBF_MAX_STRING = 254
DBF_MAX_FIELD_NAME = 10
DBF_MAX_RECORD_BYTES = 65535

DEFAULT_STRING_LENGTH = 254
DEFAULT_DOUBLE_LENGTH = 24
DEFAULT_DOUBLE_PRECISION = 15
DEFAULT_INT_LENGTH = 10
DEFAULT_INT64_LENGTH = 18


def qvariant_for(value_type):
    """Kembalikan tipe QVariant untuk sebuah faValueType.

    Mengembalikan None bila tipe tersebut bukan kolom atribut.
    Tipe yang tidak dikenal jatuh ke String disertai peringatan di log,
    supaya perubahan katalog di masa depan tidak lolos diam-diam.
    """
    if value_type in NON_ATTRIBUTE_TYPES:
        return None
    if value_type in _TYPE_MAP:
        return _TYPE_MAP[value_type]
    log_warning(
        "faValueType tidak dikenal: '%s'. Dibuat sebagai String." % value_type
    )
    return QVariant.String


def length_precision_for(qvariant_type, requested_length=None):
    """Kembalikan pasangan (length, precision) untuk QgsField."""
    if qvariant_type == QVariant.String:
        length = requested_length or DEFAULT_STRING_LENGTH
        return min(int(length), DBF_MAX_STRING), 0
    if qvariant_type == QVariant.Double:
        return DEFAULT_DOUBLE_LENGTH, DEFAULT_DOUBLE_PRECISION
    if qvariant_type == QVariant.LongLong:
        return DEFAULT_INT64_LENGTH, 0
    if qvariant_type == QVariant.Int:
        return DEFAULT_INT_LENGTH, 0
    return 0, 0


def type_display_name(qvariant_type):
    names = {
        QVariant.String: "String",
        QVariant.Int: "Integer",
        QVariant.LongLong: "Int64",
        QVariant.Double: "Double",
        QVariant.Date: "Date",
        QVariant.DateTime: "DateTime",
        QVariant.Bool: "Boolean",
    }
    return names.get(qvariant_type, "Tidak diketahui")


def types_compatible(source_type, target_type):
    """Apakah nilai bertipe source aman dimasukkan ke field bertipe target."""
    if source_type == target_type:
        return True
    numeric = {QVariant.Int, QVariant.LongLong, QVariant.Double}
    if source_type in numeric and target_type in numeric:
        return True
    # Apa pun bisa diubah menjadi teks.
    if target_type == QVariant.String:
        return True
    return False
