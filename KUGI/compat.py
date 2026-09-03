# -*- coding: utf-8 -*-
"""Lapisan kompatibilitas.

Semua penggunaan QVariant dikurung di sini supaya migrasi ke QGIS 4 / Qt6
(yang memakai QMetaType) hanya menyentuh satu berkas.

Target minimum: QGIS 3.28 LTR, Python 3.9.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QAbstractItemView, QComboBox, QFrame,
                                 QHeaderView, QMessageBox)
from qgis.core import (Qgis, QgsBlockingNetworkRequest, QgsMapLayer,
                       QgsMessageLog, QgsVectorFileWriter, QgsWkbTypes)

# QGIS 4 membuang QVariant untuk tipe field dan memakai QMetaType. QGIS 3.38
# sudah menerima keduanya, di bawah itu hanya QVariant. Resolusinya dilakukan
# sekali di sini, dan seluruh berkas lain memakai konstanta TYPE_* saja.
try:
    from qgis.PyQt.QtCore import QMetaType
    _META = QMetaType.Type
    TYPE_STRING = _META.QString
    TYPE_INT = _META.Int
    TYPE_LONGLONG = _META.LongLong
    TYPE_DOUBLE = _META.Double
    TYPE_DATE = _META.QDate
    TYPE_DATETIME = _META.QDateTime
    TYPE_BOOL = _META.Bool
    USING_QMETATYPE = True
except (ImportError, AttributeError):
    from qgis.PyQt.QtCore import QVariant
    TYPE_STRING = QVariant.String
    TYPE_INT = QVariant.Int
    TYPE_LONGLONG = QVariant.LongLong
    TYPE_DOUBLE = QVariant.Double
    TYPE_DATE = QVariant.Date
    TYPE_DATETIME = QVariant.DateTime
    TYPE_BOOL = QVariant.Bool
    USING_QMETATYPE = False

PLUGIN_NAME = "KUGI"

QGIS_VERSION = Qgis.QGIS_VERSION_INT


def log(message, level=None):
    if level is None:
        level = MSG_INFO
    QgsMessageLog.logMessage(str(message), PLUGIN_NAME, level)


def log_warning(message):
    log(message, MSG_WARNING)


# Nilai faValueType yang muncul di API KUGI, dipetakan ke tipe field QGIS.
# Sumber verifikasi: dump featuretypegetbycode?code=CA02040160 (97 baris,
# 34 atribut) yang memuat Integer, String, Double, OID, Date, dan Geometry.
_TYPE_MAP = {
    "String": TYPE_STRING,
    "Integer": TYPE_INT,
    "Int64": TYPE_LONGLONG,
    "Double": TYPE_DOUBLE,
    "Date": TYPE_DATE,
    "DateTime": TYPE_DATETIME,
    "Boolean": TYPE_BOOL,
    # OBJECTID dikelola ArcGIS. Di sini diperlakukan sebagai Integer biasa
    # dan diisi nomor urut bila tidak dipetakan dari kolom eksisting.
    "OID": TYPE_INT,
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
    return TYPE_STRING


def length_precision_for(qvariant_type, requested_length=None):
    """Kembalikan pasangan (length, precision) untuk QgsField."""
    if qvariant_type == TYPE_STRING:
        length = requested_length or DEFAULT_STRING_LENGTH
        return min(int(length), DBF_MAX_STRING), 0
    if qvariant_type == TYPE_DOUBLE:
        return DEFAULT_DOUBLE_LENGTH, DEFAULT_DOUBLE_PRECISION
    if qvariant_type == TYPE_LONGLONG:
        return DEFAULT_INT64_LENGTH, 0
    if qvariant_type == TYPE_INT:
        return DEFAULT_INT_LENGTH, 0
    return 0, 0


def type_display_name(qvariant_type):
    names = {
        TYPE_STRING: "String",
        TYPE_INT: "Integer",
        TYPE_LONGLONG: "Int64",
        TYPE_DOUBLE: "Double",
        TYPE_DATE: "Date",
        TYPE_DATETIME: "DateTime",
        TYPE_BOOL: "Boolean",
    }
    return names.get(qvariant_type, "Tidak diketahui")


def types_compatible(source_type, target_type):
    """Apakah nilai bertipe source aman dimasukkan ke field bertipe target."""
    if source_type == target_type:
        return True
    numeric = {TYPE_INT, TYPE_LONGLONG, TYPE_DOUBLE}
    if source_type in numeric and target_type in numeric:
        return True
    # Apa pun bisa diubah menjadi teks.
    if target_type == TYPE_STRING:
        return True
    return False

# --------------------------------------------------------------------------
# Kompatibilitas Qt5 dan Qt6
#
# QGIS 4 memakai PyQt6, yang menghapus akses enum tanpa cakupan. Qt.UserRole
# tidak lagi ada, yang ada Qt.ItemDataRole.UserRole. PyQt5 sebenarnya sudah
# menyediakan bentuk bercakupan itu, tapi tidak untuk semua versi, jadi
# resolusinya dilakukan saat jalan dengan jatuh balik ke bentuk lama.
#
# Semua konstanta dikumpulkan di sini, bukan disebar di seluruh berkas,
# supaya perubahan Qt berikutnya cukup menyentuh satu tempat.
# --------------------------------------------------------------------------


def enum_of(owner, group, name):
    """Ambil anggota enum, dari bentuk bercakupan maupun bentuk lama."""
    scoped = getattr(owner, group, None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(owner, name)


ROLE_USER = enum_of(Qt, "ItemDataRole", "UserRole")
CURSOR_WAIT = enum_of(Qt, "CursorShape", "WaitCursor")
SCROLLBAR_OFF = enum_of(Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
ITEM_EDITABLE = enum_of(Qt, "ItemFlag", "ItemIsEditable")
ITEM_NO_FLAGS = enum_of(Qt, "ItemFlag", "NoItemFlags")
DATE_ISO = enum_of(Qt, "DateFormat", "ISODate")

RESIZE_TO_CONTENTS = enum_of(QHeaderView, "ResizeMode", "ResizeToContents")
RESIZE_STRETCH = enum_of(QHeaderView, "ResizeMode", "Stretch")
RESIZE_FIXED = enum_of(QHeaderView, "ResizeMode", "Fixed")

SELECT_SINGLE = enum_of(QAbstractItemView, "SelectionMode", "SingleSelection")
SELECT_NONE = enum_of(QAbstractItemView, "SelectionMode", "NoSelection")
SELECT_ROWS = enum_of(QAbstractItemView, "SelectionBehavior", "SelectRows")
NO_EDIT_TRIGGERS = enum_of(QAbstractItemView, "EditTrigger", "NoEditTriggers")

BUTTON_YES = enum_of(QMessageBox, "StandardButton", "Yes")
BUTTON_NO = enum_of(QMessageBox, "StandardButton", "No")
ROLE_ACCEPT = enum_of(QMessageBox, "ButtonRole", "AcceptRole")
ROLE_ACTION = enum_of(QMessageBox, "ButtonRole", "ActionRole")
ROLE_REJECT = enum_of(QMessageBox, "ButtonRole", "RejectRole")

COMBO_NO_INSERT = enum_of(QComboBox, "InsertPolicy", "NoInsert")


def vector_layer_filter():
    """Penyaring layer vektor.

    QgsMapLayerProxyModel.VectorLayer diganti Qgis.LayerFilter.VectorLayer
    sejak QGIS 3.34 dan dibuang di QGIS 4.
    """
    layer_filter = getattr(Qgis, "LayerFilter", None)
    if layer_filter is not None and hasattr(layer_filter, "VectorLayer"):
        return layer_filter.VectorLayer
    from qgis.core import QgsMapLayerProxyModel
    return enum_of(QgsMapLayerProxyModel, "Filter", "VectorLayer")


def show_modal(dialog):
    """Jalankan dialog secara modal. PyQt6 membuang exec_()."""
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return runner()

# Enum milik kelas QGIS, bukan Qt. Di Qt6 semuanya ikut bercakupan.
# Dinamai MSG_* dan bukan LEVEL_*, karena validator.py sudah memakai
# LEVEL_ERROR dan kawan-kawan untuk tingkat temuannya sendiri.
MSG_INFO = enum_of(Qgis, "MessageLevel", "Info")
MSG_WARNING = enum_of(Qgis, "MessageLevel", "Warning")
MSG_CRITICAL = enum_of(Qgis, "MessageLevel", "Critical")

GEOM_POINT = enum_of(QgsWkbTypes, "GeometryType", "PointGeometry")
GEOM_LINE = enum_of(QgsWkbTypes, "GeometryType", "LineGeometry")
GEOM_POLYGON = enum_of(QgsWkbTypes, "GeometryType", "PolygonGeometry")

STYLE_FIELDS = enum_of(QgsMapLayer, "StyleCategory", "Fields")
STYLE_FORMS = enum_of(QgsMapLayer, "StyleCategory", "Forms")
STYLE_SYMBOLOGY = enum_of(QgsMapLayer, "StyleCategory", "Symbology")

WRITER_OVERWRITE = enum_of(QgsVectorFileWriter, "ActionOnExistingFile",
                           "CreateOrOverwriteFile")
WRITER_NO_ERROR = enum_of(QgsVectorFileWriter, "WriterError", "NoError")

NETWORK_NO_ERROR = enum_of(QgsBlockingNetworkRequest, "ErrorCode", "NoError")

FRAME_NO_FRAME = enum_of(QFrame, "Shape", "NoFrame")
