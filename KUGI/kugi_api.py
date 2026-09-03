# -*- coding: utf-8 -*-
"""Klien WebAPI KUGI.

Tidak ada widget di sini. Modul ini murni pengambilan data, cache, dan
parsing sehingga bisa dipakai dari dialog, dari Processing, maupun dari
skrip uji.
"""

import datetime
import json
import os
import time

from qgis.PyQt.QtCore import QUrl, QByteArray
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (QgsApplication, QgsSettings, QgsBlockingNetworkRequest)

from .compat import log, log_warning
from .kugi_model import (KugiCategory, KugiFeatureTypeRef,
                         KugiFeatureType, strip_en)

SETTINGS_PREFIX = "kugi_standardizer/"
DEFAULT_BASE_URL = "https://kugi.ina-sdi.or.id/kugiapi"
DEFAULT_TIMEOUT = 30
CACHE_TTL_SECONDS = 7 * 24 * 3600

# Endpoint featuretype menolak klien tanpa header browser dan membalas 500.
# Header ini wajib dipasang manual, QgsBlockingNetworkRequest tidak
# mengirimkannya secara default.
USER_AGENT = "QGIS KUGI Converter/1.0 (+https://qgis.org)"


class KugiApiError(Exception):
    pass


def base_url() -> str:
    value = QgsSettings().value(SETTINGS_PREFIX + "base_url", DEFAULT_BASE_URL)
    return str(value).rstrip("/")


def set_base_url(value: str):
    QgsSettings().setValue(SETTINGS_PREFIX + "base_url", value.rstrip("/"))


def timeout_seconds() -> int:
    try:
        return int(QgsSettings().value(SETTINGS_PREFIX + "timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def use_offline_fallback() -> bool:
    value = QgsSettings().value(SETTINGS_PREFIX + "offline_fallback", True)
    return str(value).lower() in ("true", "1", "yes")


def cache_dir() -> str:
    path = os.path.join(QgsApplication.qgisSettingsDirPath(), "kugi_cache")
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError as exc:
            log_warning("Gagal membuat folder cache: %s" % exc)
    return path


def snapshot_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "resources", "snapshot")


def _cache_path(key: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
    return os.path.join(cache_dir(), safe + ".json")


def _read_cache(key: str, ignore_ttl: bool = False):
    path = _cache_path(key)
    if not os.path.isfile(path):
        return None
    if not ignore_ttl and (time.time() - os.path.getmtime(path)) > CACHE_TTL_SECONDS:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        log_warning("Cache rusak untuk %s: %s" % (key, exc))
        return None


def _write_cache(key: str, payload):
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except (OSError, TypeError) as exc:
        log_warning("Gagal menulis cache %s: %s" % (key, exc))


def _read_snapshot(key: str):
    path = os.path.join(snapshot_dir(), key + ".json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def clear_cache() -> int:
    removed = 0
    folder = cache_dir()
    if not os.path.isdir(folder):
        return 0
    for name in os.listdir(folder):
        if name.endswith(".json"):
            try:
                os.remove(os.path.join(folder, name))
                removed += 1
            except OSError:
                pass
    return removed


def cache_info():
    folder = cache_dir()
    if not os.path.isdir(folder):
        return 0, None
    files = [f for f in os.listdir(folder) if f.endswith(".json")]
    if not files:
        return 0, None
    newest = max(os.path.getmtime(os.path.join(folder, f)) for f in files)
    return len(files), newest


def _fetch_json(url: str, attempts: int = 2):
    """Ambil JSON dari URL dengan header eksplisit dan satu kali percobaan ulang."""
    last_error = ""
    for attempt in range(attempts):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(QByteArray(b"User-Agent"),
                             QByteArray(USER_AGENT.encode("utf-8")))
        request.setRawHeader(QByteArray(b"Accept"),
                             QByteArray(b"application/json, text/plain, */*"))

        blocking = QgsBlockingNetworkRequest()
        try:
            blocking.setTimeout(timeout_seconds() * 1000)
        except AttributeError:
            pass

        code = blocking.get(request, forceRefresh=True)
        if code == QgsBlockingNetworkRequest.NoError:
            body = bytes(blocking.reply().content())
            try:
                return json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise KugiApiError(
                    "Respons bukan JSON yang sah dari %s (%s)" % (url, exc))
        last_error = blocking.errorMessage() or "kode kesalahan %s" % code
        if attempt + 1 < attempts:
            time.sleep(1.0)

    raise KugiApiError("Gagal mengakses %s: %s" % (url, last_error))


def _cached_fetch(key: str, url: str, force: bool = False):
    if not force:
        cached = _read_cache(key)
        if cached is not None:
            return cached
    try:
        payload = _fetch_json(url)
        _write_cache(key, payload)
        return payload
    except KugiApiError as exc:
        log_warning(str(exc))
        stale = _read_cache(key, ignore_ttl=True)
        if stale is not None:
            log("Memakai cache lama untuk %s" % key)
            return stale
        if use_offline_fallback():
            snap = _read_snapshot(key)
            if snap is not None:
                log("Memakai snapshot offline untuk %s" % key)
                return snap
        raise


def test_connection():
    """Kembalikan pasangan (berhasil, pesan) untuk tombol uji koneksi."""
    try:
        payload = _fetch_json(base_url() + "/featurecatalog")
        return True, "Berhasil. %d kategori terbaca." % len(payload)
    except KugiApiError as exc:
        return False, str(exc)


def fetch_categories(force: bool = False):
    payload = _cached_fetch("featurecatalog", base_url() + "/featurecatalog", force)
    items = []
    for row in payload or []:
        cat = KugiCategory.from_json(row)
        if cat:
            items.append(cat)
    items.sort(key=lambda c: c.name)
    return items


def fetch_feature_types(category_id: str, force: bool = False):
    url = "%s/featuretype?fcid=%s" % (base_url(), category_id)
    payload = _cached_fetch("featuretype_%s" % category_id, url, force)
    items = []
    for row in payload or []:
        ref = KugiFeatureTypeRef.from_json(row)
        if ref:
            items.append(ref)
    items.sort(key=lambda f: f.type_name)
    return items


def fetch_schema(code: str, force: bool = False):
    url = "%s/featuretypegetbycode?code=%s" % (base_url(), code)
    payload = _cached_fetch("schema_%s" % code, url, force)
    if not payload:
        raise KugiApiError(
            "Skema untuk kode %s kosong. Coba kode unsur lain." % code)
    schema = KugiFeatureType.from_json(payload)
    if schema is None or not schema.attributes:
        raise KugiApiError("Skema untuk kode %s tidak bisa diurai." % code)
    return schema


# --------------------------------------------------------------------------
# Indeks katalog penuh
#
# API tidak punya endpoint pencarian lintas kategori. featuretype?fcid=N
# hanya mengembalikan unsur untuk satu kategori. Supaya pencarian bisa
# menjangkau seluruh katalog, 15 kategori diunduh sekali lalu disimpan
# sebagai indeks ringkas.
#
# Respons mentahnya 232 KB per kategori, sekitar 3,4 MB untuk 15 kategori.
# Yang disimpan hanya empat field yang benar-benar dipakai, sehingga
# indeksnya sekitar 787 KB, empat kali lebih kecil.
# --------------------------------------------------------------------------

INDEX_KEY = "catalog_index"


def index_path() -> str:
    return _cache_path(INDEX_KEY)


def load_index():
    """Baca indeks katalog dari cache. Kembalikan None bila belum ada."""
    return _read_cache(INDEX_KEY, ignore_ttl=True)


def index_summary():
    """Ringkasan untuk ditampilkan di Pengaturan."""
    data = load_index()
    if not data:
        return None
    path = index_path()
    return {
        "version": data.get("version") or "tidak diketahui",
        "fetched": data.get("fetched") or "",
        "categories": len(data.get("categories") or []),
        "features": len(data.get("features") or []),
        "schemas": sum(1 for name in os.listdir(cache_dir())
                       if name.startswith("schema_")) if os.path.isdir(cache_dir()) else 0,
        "bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
    }


def build_index(progress=None):
    """Unduh seluruh kategori lalu simpan indeks ringkas.

    Indeks lama tidak dibuang sebelum yang baru lengkap. Endpoint
    featuretype terbukti bisa membalas 500, dan user tidak boleh berakhir
    tanpa katalog sama sekali karena satu permintaan gagal di tengah.
    """
    categories = fetch_categories(force=True)
    total = len(categories)
    collected = []
    version = ""

    for position, category in enumerate(categories, start=1):
        if progress is not None:
            progress(position, total, category.name)
        url = "%s/featuretype?fcid=%s" % (base_url(), category.id)
        payload = _fetch_json(url)
        for row in payload or []:
            code = strip_en(row.get("code"))
            type_name = strip_en(row.get("typeName"))
            if not code or not type_name:
                continue
            if not version:
                version = strip_en(row.get("attributVersionNumber")) or ""
            collected.append({
                "c": code,
                "n": type_name,
                "a": strip_en(row.get("aliases")) or "",
                "d": strip_en(row.get("definition")) or "",
                "k": category.name,
            })

    index = {
        "version": version,
        "fetched": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "categories": [{"id": c.id, "name": c.name} for c in categories],
        "features": collected,
    }
    _write_cache(INDEX_KEY, index)
    _invalidate_schemas()
    return index


def _invalidate_schemas() -> int:
    """Tandai skema tersimpan sebagai basi setelah katalog diperbarui.

    Dihapus, bukan diunduh ulang. Bisa saja ratusan skema tersimpan
    sementara user hanya akan memakai beberapa, jadi mengambilnya lagi
    saat dibutuhkan lebih murah daripada mengunduh semuanya di muka.
    """
    folder = cache_dir()
    removed = 0
    if not os.path.isdir(folder):
        return 0
    for name in os.listdir(folder):
        if name.startswith("schema_") and name.endswith(".json"):
            try:
                os.remove(os.path.join(folder, name))
                removed += 1
            except OSError:
                pass
    return removed


def index_features():
    """Daftar unsur dari indeks sebagai objek KugiFeatureTypeRef."""
    data = load_index()
    if not data:
        return []
    items = []
    for row in data.get("features") or []:
        ref = KugiFeatureTypeRef(row["c"], row["n"],
                                 row.get("d") or "", row.get("a") or "")
        ref.category = row.get("k") or ""
        items.append(ref)
    return items
