# -*- coding: utf-8 -*-
"""Model data KUGI dan parser respons API.

Modul ini tidak mengimpor apa pun dari PyQt supaya bisa diuji tanpa GUI.

STRUKTUR KODE UNSUR KUGI (10 karakter), terverifikasi terhadap 200 kode
kategori BATAS WILAYAH ditambah tiga kode dari kategori lain:

    B   A   03   06   0060
    |   |   |    |    +--- nomor urut unsur
    |   |   |    +-------- kode skala (01..10)
    |   |   +------------- tipe geometri (01=PT, 02=LN, 03=AR)
    |   +----------------- sub-tema
    +--------------------- kode katalog (sama dengan featureCatalogCode)

Korelasi posisi 3-4 dengan tipe geometri sempurna pada 200 baris:
01 -> 38 unsur PT, 02 -> 70 unsur LN, 03 -> 92 unsur AR. Ini berarti
geometri dan skala bisa ditentukan tanpa mengurai typeName sama sekali.
"""

from typing import List, Optional, Tuple

SCALE_MAP = {
    "01": "1:1.000.000",
    "02": "1:500.000",
    "03": "1:250.000",
    "04": "1:100.000",
    "05": "1:50.000",
    "06": "1:25.000",
    "07": "1:10.000",
    "08": "1:5.000",
    "09": "1:2.500",
    "10": "1:1.000",
}

SCALE_SHORT = {
    "01": "1jt", "02": "500k", "03": "250k", "04": "100k", "05": "50k",
    "06": "25k", "07": "10k", "08": "5k", "09": "2.5k", "10": "1k",
}

GEOMETRY_BY_CODE = {"01": "PT", "02": "LN", "03": "AR"}
GEOMETRY_LABEL = {"AR": "Area (poligon)", "LN": "Garis", "PT": "Titik"}

# Padanan token KUGI dengan QgsWkbTypes.GeometryType.
QGIS_GEOMETRY_TO_TOKEN = {0: "PT", 1: "LN", 2: "AR"}

# Field yang NILAINYA wajib terisi. Dibedakan tegas dari kewajiban
# strukturalnya: seluruh field KUGI wajib ADA sebagai kolom, tapi hanya
# dua yang isinya wajib.
#
# API tidak menyediakan dasar apa pun untuk ini. ptCardinality bernilai '-'
# di seluruh 97 baris dump JALAN_LN dan null di endpoint featuretype.
#
#   FCODE   tanpa ini tipe unsur tidak bisa dikenali
#   NAMOBJ  nama objek, isi utama tiap fitur
#
# METADATA TIDAK termasuk. Buku pedoman BIG halaman 40 mencantumkannya
# dengan query bernilai null, dan data contoh di halaman 22 memang
# mengosongkannya.
VALUE_REQUIRED_FIELDS = ("FCODE", "NAMOBJ")

# Field yang plugin isi sendiri, jadi tidak perlu dipetakan dari kolom mana
# pun. Keunikan OBJECTID tetap diperiksa saat QC atas layer dari luar.
AUTO_FILLED_FIELDS = ("FCODE", "OBJECTID", "SRS_ID")

NULL_TOKENS = {"", "-", "null", "NULL", "None"}

# faValueType yang BUKAN kolom atribut. SHAPE bertipe Geometry adalah
# geometri layer, bukan field. Membuatnya sebagai field merusak keluaran.
# Didefinisikan di sini, bukan di compat.py, supaya modul ini tetap bisa
# diimpor tanpa QGIS untuk pengujian.
NON_ATTRIBUTE_TYPES = {"Geometry", "GM_Object", "GM_Curve",
                       "GM_Point", "GM_Surface"}


def strip_en(value) -> str:
    """Buang sufiks bahasa '@en' bila memang ada.

    Jangan pernah memakai str.strip('@en'). strip() menghapus HIMPUNAN
    karakter, bukan sufiks, sehingga 'Akan Dibangun' menjadi 'Akan Dibangu'
    dan 'Sedang Dibangun' menjadi 'Sedang Dibangu'. Dua label domain nyata
    di unsur JALAN_LN rusak karenanya.

    Pembersihan harus kondisional karena kedua endpoint berbeda perilaku:
    featuretype dan featurecatalog memakai @en di semua nilai, sedangkan
    featuretypegetbycode sama sekali tidak memakainya.

    Nilai None juga umum: 15 dari 36 kunci bernilai null di endpoint
    featuretype.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > 3 and text[-3] == "@" and text[-2:].isalpha():
        return text[:-3].strip()
    return text


def is_null_token(value) -> bool:
    return strip_en(value) in NULL_TOKENS


def geometry_token_from_code(code: str) -> str:
    """Tipe geometri dari posisi 3-4 kode unsur."""
    if len(code) >= 4:
        return GEOMETRY_BY_CODE.get(code[2:4], "")
    return ""


def geometry_token_from_name(type_name: str) -> str:
    """Tipe geometri dari token nama, sebagai cadangan.

    Harus berbasis token, bukan sufiks. 'JALAN_LN' berakhiran _LN
    sedangkan 'ADMINISTRASI_AR_25K' pada penamaan lama menaruh _AR_
    di tengah.
    """
    for part in type_name.split("_"):
        if part.upper() in GEOMETRY_LABEL:
            return part.upper()
    return ""


def scale_code_from_code(code: str) -> str:
    return code[4:6] if len(code) >= 6 else ""


def normalize_code(value) -> str:
    """Bentuk baku sebuah kode domain untuk perbandingan.

    Nilai bisa datang sebagai 1, "1", atau 1.0 tergantung tipe kolom dan
    penyedia data. Ketiganya harus dianggap sama.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if number == int(number):
        return str(int(number))
    return text


class KugiCategory:
    """Satu kategori katalog, misal BATAS WILAYAH."""

    def __init__(self, cat_id: str, code: str, name: str, version: str = ""):
        self.id = cat_id
        self.code = code
        self.name = name
        self.version = version

    @classmethod
    def from_json(cls, row: dict) -> Optional["KugiCategory"]:
        raw_id = strip_en(row.get("id") or row.get("featureCatalogId"))
        digits = "".join(ch for ch in raw_id if ch.isdigit())
        if not digits:
            return None
        return cls(
            cat_id=digits,
            code=strip_en(row.get("featureCatalogCode")),
            name=strip_en(row.get("name")),
            version=strip_en(row.get("versionNumber")),
        )

    def __repr__(self):
        return "<KugiCategory %s %s>" % (self.id, self.name)


class KugiFeatureTypeRef:
    """Ringkasan unsur dalam daftar per kategori.

    Identitas unsur adalah `code`, TIDAK PERNAH `type_name`. Pada KUGI
    5.1.2026 sufiks skala dihapus dari typeName, sehingga 200 kode di
    kategori BATAS WILAYAH hanya menghasilkan 34 typeName unik. Satu nama
    seperti ADMINISTRASI_LN muncul di sembilan skala berbeda dengan
    sembilan kode berbeda.
    """

    def __init__(self, code: str, type_name: str, definition: str = "",
                 aliases: str = "", producer: str = "", version: str = ""):
        self.code = code
        self.type_name = type_name
        self.definition = definition
        self.aliases = aliases
        self.producer = producer
        self.version = version
        self.category = ""

    # Peringkat kecocokan pencarian. Yang cocok di nama unsur harus muncul
    # lebih dulu daripada yang cocok di deskripsi. Situs KUGI tidak
    # melakukan ini, sehingga mencari "jalan" menaruh GORONG_PT di atas
    # JALAN_AR padahal yang dicari hampir pasti yang kedua.
    MATCH_NAME = 0
    MATCH_CODE = 1
    MATCH_ALIAS = 2
    MATCH_DEFINITION = 3

    MATCH_LABELS = {
        MATCH_NAME: "Cocok di nama unsur",
        MATCH_CODE: "Cocok di kode",
        MATCH_ALIAS: "Cocok di alias",
        MATCH_DEFINITION: "Cocok di deskripsi",
    }

    def match_rank(self, needle: str):
        """Peringkat kecocokan, atau None bila tidak cocok sama sekali."""
        text = (needle or "").strip().lower()
        if not text:
            return self.MATCH_NAME
        if text in (self.type_name or "").lower():
            return self.MATCH_NAME
        if text in (self.code or "").lower():
            return self.MATCH_CODE
        if text in (self.aliases or "").lower():
            return self.MATCH_ALIAS
        if text in (self.definition or "").lower():
            return self.MATCH_DEFINITION
        return None

    @property
    def scale_code(self) -> str:
        return scale_code_from_code(self.code)

    @property
    def scale_label(self) -> str:
        return SCALE_MAP.get(self.scale_code, "")

    @property
    def scale_short(self) -> str:
        return SCALE_SHORT.get(self.scale_code, "")

    @property
    def geometry_token(self) -> str:
        return (geometry_token_from_code(self.code)
                or geometry_token_from_name(self.type_name))

    @property
    def display(self) -> str:
        """Label dropdown. Skala wajib ikut karena nama tidak unik."""
        scale = self.scale_label or "skala tidak dikenal"
        return "%s  ·  %s  ·  %s" % (self.type_name, scale, self.code)

    @classmethod
    def from_json(cls, row: dict) -> Optional["KugiFeatureTypeRef"]:
        code = strip_en(row.get("code"))
        type_name = strip_en(row.get("typeName") or row.get("name"))
        if not code or not type_name:
            return None
        ref = cls(
            code=code,
            type_name=type_name,
            definition=strip_en(row.get("definition")) or "",
            aliases=strip_en(row.get("aliases")) or "",
            producer=strip_en(row.get("fcProducer")) or "",
            version=strip_en(row.get("attributVersionNumber")) or "",
        )
        ref.category = strip_en(row.get("featureCatalogue")) or ""
        return ref

    def __repr__(self):
        return "<KugiFeatureTypeRef %s %s>" % (self.code, self.type_name)


class KugiAttribute:
    """Satu atribut dalam skema unsur, lengkap dengan domainnya."""

    def __init__(self, name: str, definition: str, value_type: str,
                 lv_code: str = "",
                 domain: Optional[List[Tuple[str, str]]] = None):
        self.name = name
        self.definition = definition
        self.value_type = value_type
        self.lv_code = lv_code
        self.domain = domain or []

    @property
    def has_domain(self) -> bool:
        return bool(self.domain)

    @property
    def domain_values(self) -> List[str]:
        return [code for code, _ in self.domain]

    def cast_code(self, code):
        """Ubah lvCodeDetail ke tipe yang sesuai faValueType.

        API selalu mengirim lvCodeDetail sebagai string, termasuk untuk
        atribut bertipe Integer: KLSRJL bertipe Integer tapi nilainya
        datang sebagai "1", bukan 1. Seluruh 16 field berdomain pada
        unsur JALAN_LN bertipe Integer, dan tidak satu pun nilainya
        berawalan nol, jadi konversi ke angka aman dan tidak menghilangkan
        informasi.

        Ini penting karena ValueMap di .qml menyimpan nilai apa adanya.
        Kalau "1" tersimpan sebagai teks sementara kolomnya Integer, QGIS
        harus menebak saat menulis dan bisa menghasilkan NULL.
        """
        if self.value_type in ("Integer", "Int64"):
            try:
                return int(str(code).strip())
            except (TypeError, ValueError):
                return code
        if self.value_type == "Double":
            try:
                return float(str(code).strip())
            except (TypeError, ValueError):
                return code
        return code

    @property
    def typed_domain(self) -> List[tuple]:
        """Pasangan (nilai bertipe benar, label)."""
        return [(self.cast_code(code), label) for code, label in self.domain]

    def normalized_domain_values(self) -> set:
        """Nilai domain dalam bentuk teks yang sudah dinormalkan.

        Dipakai validator supaya 1, "1", dan 1.0 dianggap sama.
        """
        return {normalize_code(code) for code, _ in self.domain}

    def domain_label(self, value) -> str:
        text = strip_en(value)
        for code, label in self.domain:
            if code == text:
                return label
        return ""

    def __repr__(self):
        return "<KugiAttribute %s %s dom=%d>" % (
            self.name, self.value_type, len(self.domain))


class KugiFeatureType:
    """Skema lengkap satu unsur KUGI."""

    def __init__(self, code: str, type_name: str, definition: str = "",
                 aliases: str = "", category: str = "", version: str = "",
                 producer: str = "",
                 attributes: Optional[List[KugiAttribute]] = None,
                 skipped: Optional[List[str]] = None):
        self.code = code
        self.type_name = type_name
        self.definition = definition
        self.aliases = aliases
        self.category = category
        self.version = version
        self.producer = producer
        self.attributes = attributes or []
        self.skipped = skipped or []

    @property
    def scale_code(self) -> str:
        return scale_code_from_code(self.code)

    @property
    def scale_label(self) -> str:
        return SCALE_MAP.get(self.scale_code, "")

    @property
    def geometry_token(self) -> str:
        return (geometry_token_from_code(self.code)
                or geometry_token_from_name(self.type_name))

    @property
    def display(self) -> str:
        return "%s  ·  %s  ·  %s" % (
            self.type_name, self.scale_label or "-", self.code)

    def attribute(self, name: str) -> Optional[KugiAttribute]:
        for att in self.attributes:
            if att.name == name:
                return att
        return None

    @property
    def field_names(self) -> List[str]:
        return [a.name for a in self.attributes]

    @property
    def domain_attributes(self) -> List[KugiAttribute]:
        return [a for a in self.attributes if a.has_domain]

    @classmethod
    def from_json(cls, rows: list) -> Optional["KugiFeatureType"]:
        """Susun skema dari respons featuretypegetbycode.

        Responsnya adalah daftar datar dengan SATU BARIS PER PASANGAN
        atribut-nilai domain. Unsur JALAN_LN mengembalikan 97 baris untuk
        34 atribut; JPARJL sendiri muncul 11 kali.

        Jangan pakai set() untuk dedup baris seperti pada script rujukan.
        Cara itu meruntuhkan 97 baris menjadi 34 dan menghapus seluruh
        informasi domain untuk 16 dari 34 field.

        Baris satu grup terbukti kontigu sehingga urutan katalog terjaga
        tanpa sorting. Urutan itu dipertahankan agar cocok dengan Buku 2
        KUGI.
        """
        if not rows:
            return None

        head = rows[0]
        # Kunci level unsur terbukti konstan di seluruh baris. Perhatikan
        # bahwa featureTypeId TIDAK konstan (97 nilai berbeda) karena itu
        # id baris, bukan identitas unsur. Nama kuncinya pun berbeda antar
        # endpoint: featureTypeId di sini, featureTypeID di featuretype.
        obj = cls(
            code=strip_en(head.get("code")),
            type_name=strip_en(head.get("typeName")),
            definition=strip_en(head.get("definition")),
            aliases=strip_en(head.get("aliases")),
            category=strip_en(head.get("featureCatalogue") or head.get("fcName")),
            version=strip_en(head.get("attributVersionNumber")),
            producer=strip_en(head.get("fcProducer")),
        )

        order = []
        grouped = {}
        for row in rows:
            name = strip_en(row.get("ptMemberName"))
            if not name:
                continue
            if name not in grouped:
                grouped[name] = []
                order.append(name)
            grouped[name].append(row)

        for name in order:
            group = grouped[name]
            first = group[0]
            value_type = strip_en(first.get("faValueType"))

            # SHAPE bertipe Geometry adalah geometri layer, bukan kolom.
            if value_type in NON_ATTRIBUTE_TYPES:
                obj.skipped.append(name)
                continue

            domain = []
            seen = set()
            for row in group:
                detail = strip_en(row.get("lvCodeDetail"))
                label = strip_en(row.get("lvLabel"))
                if detail in NULL_TOKENS or label in NULL_TOKENS:
                    continue
                if detail in seen:
                    continue
                seen.add(detail)
                domain.append((detail, label))

            lv_code = strip_en(first.get("lvCode"))
            if lv_code in NULL_TOKENS:
                lv_code = ""

            obj.attributes.append(KugiAttribute(
                name=name,
                definition=strip_en(first.get("ptDefinition")),
                value_type=value_type,
                lv_code=lv_code,
                domain=domain,
            ))

        return obj

    @classmethod
    def from_nested(cls, payload) -> Optional["KugiFeatureType"]:
        """Susun skema dari bentuk bersarang.

        Bentuk ini menulis unsur sekali lalu atribut dan domainnya
        bersarang, alih-alih satu objek per pasangan atribut-nilai domain.
        Muatannya 96 persen lebih kecil untuk isi yang sama persis.

        Domain diterima sebagai larik pasangan [nilai, label] maupun
        sebagai objek {"nilai": "label"}. Larik lebih disukai karena
        kunci objek JSON selalu string, sehingga kode Integer akan
        kembali menjadi teks, dan karena urutannya tidak dijamin.
        """
        if not payload or not payload.get("code"):
            return None

        obj = cls(
            code=strip_en(payload.get("code")),
            type_name=strip_en(payload.get("typeName")),
            definition=strip_en(payload.get("definition")),
            aliases=strip_en(payload.get("aliases")),
            category=strip_en(payload.get("featureCatalogue")),
            version=strip_en(payload.get("version")
                             or payload.get("attributVersionNumber")),
            producer=strip_en(payload.get("producer")
                              or payload.get("fcProducer")),
        )

        for entry in payload.get("attributes") or []:
            name = strip_en(entry.get("name"))
            if not name:
                continue
            value_type = strip_en(entry.get("type")
                                  or entry.get("faValueType"))
            if value_type in NON_ATTRIBUTE_TYPES:
                obj.skipped.append(name)
                continue

            raw = entry.get("domain")
            domain = []
            if isinstance(raw, dict):
                pairs = list(raw.items())
            elif isinstance(raw, list):
                pairs = [(p[0], p[1]) for p in raw if len(p) >= 2]
            else:
                pairs = []
            seen = set()
            for code, label in pairs:
                text = "" if code is None else str(code).strip()
                if not text or text in seen or label in (None, ""):
                    continue
                seen.add(text)
                domain.append((text, strip_en(label)))

            obj.attributes.append(KugiAttribute(
                name=name,
                definition=strip_en(entry.get("definition")) or "",
                value_type=value_type,
                lv_code=strip_en(entry.get("codelist")
                                 or entry.get("lvCode")) or "",
                domain=domain,
            ))
        return obj

    def __repr__(self):
        return "<KugiFeatureType %s %s attrs=%d>" % (
            self.code, self.type_name, len(self.attributes))
