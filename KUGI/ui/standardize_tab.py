# -*- coding: utf-8 -*-
"""Tab standardisasi."""

import os

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QDesktopServices
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                                 QComboBox, QFileDialog, QFormLayout,
                                 QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QMessageBox, QProgressBar, QPushButton,
                                 QTreeWidget,
                                 QTreeWidgetItem, QVBoxLayout, QWidget)
from qgis.core import (QgsMapLayerProxyModel, QgsProject, QgsVectorLayer,
                       QgsWkbTypes)
from qgis.gui import QgsMapLayerComboBox

from .. import kugi_api
from ..builder import build_memory_layer, write_outputs
from ..kugi_model import (GEOMETRY_LABEL, QGIS_GEOMETRY_TO_TOKEN, SCALE_MAP)
from ..mapping import MappingState
from ..validator import (LEVEL_ERROR, LEVEL_INFO, LEVEL_LABEL,
                         LEVEL_WARNING, validate)
from .mapping_panel import MappingPanel


class _Note(object):
    """Catatan dari builder atau penulis, dibentuk seperti temuan validator."""

    def __init__(self, level, field, message):
        self.level = level
        self.field = field
        self.message = message
        self.feature_ids = []


MAX_RESULTS = 200
RESULT_NAME_WIDTH = 200
DEFINITION_LIMIT = 180


class StandardizeTab(QWidget):

    open_settings = pyqtSignal()
    """Seluruh alur plugin dalam satu form yang bergulir.

    Tab QC terpisah dihapus karena memaksa user memilih ulang kategori,
    geometri, skala, dan unsur padahal semuanya baru saja dipilih di sini.
    Validasi tetap berjalan, hasilnya muncul sebagai panel di bawah tombol.
    """

    def __init__(self, iface, parent=None):
        super(StandardizeTab, self).__init__(parent)
        self.iface = iface
        self.schema = None
        self.state = None
        self.memory_layer = None
        self.last_paths = []
        self._loading = False
        self._all_refs = []
        self._index_attempted = False
        self._build_ui()
        # Indeks dimuat dari cache kalau ada. Unduhan hanya ditawarkan saat
        # user benar-benar mulai mencari, bukan saat dialog dibuka.
        self._update_geometry_label()
        self._all_refs = kugi_api.index_features()
        if self._all_refs:
            self._fill_categories()
            self._apply_search()


    # ------------------------------------------------------------------ ui

    def _build_ui(self):
        layout = QVBoxLayout(self)

        source_box = QGroupBox("1  Data masukan")
        source_form = QFormLayout(source_box)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        source_form.addRow("Layer", self.layer_combo)
        self.layer_info = QLabel("-")
        self.layer_info.setWordWrap(True)
        source_form.addRow("", self.layer_info)
        layout.addWidget(source_box)

        target_box = QGroupBox("2  Cari unsur KUGI")
        target_layout = QVBoxLayout(target_box)

        # Pencarian di depan, penyaring di belakang. Alur lama memaksa user
        # tahu lebih dulu bahwa JALAN_LN ada di kategori TRANSPORTASI,
        # padahal yang orang ingat adalah nama unsurnya.
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Ketik nama, alias, deskripsi, atau kode unsur")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_search)
        target_layout.addWidget(self.search_edit)

        options = QHBoxLayout()
        self.geometry_lock = QCheckBox("Hanya unsur yang sesuai data masukan")
        self.geometry_lock.setChecked(True)
        self.geometry_lock.stateChanged.connect(self._apply_search)
        options.addWidget(self.geometry_lock)
        options.addStretch(1)
        self.advanced_button = QPushButton("Pencarian lanjutan")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._on_advanced_toggled)
        options.addWidget(self.advanced_button)
        target_layout.addLayout(options)

        self.advanced_box = QWidget()
        filters = QHBoxLayout(self.advanced_box)
        filters.setContentsMargins(0, 0, 0, 0)
        self.category_combo = QComboBox()
        self.category_combo.addItem("Semua kategori", "")
        self.category_combo.currentIndexChanged.connect(self._apply_search)
        filters.addWidget(self.category_combo, 3)

        self.geometry_combo = QComboBox()
        self.geometry_combo.addItem("Semua geometri", "")
        for token in ("AR", "LN", "PT"):
            self.geometry_combo.addItem(
                "%s  (%s)" % (token, GEOMETRY_LABEL[token]), token)
        self.geometry_combo.currentIndexChanged.connect(self._apply_search)
        filters.addWidget(self.geometry_combo, 2)

        self.scale_combo = QComboBox()
        self.scale_combo.addItem("Semua skala", "")
        for code in sorted(SCALE_MAP):
            self.scale_combo.addItem(SCALE_MAP[code], code)
        self.scale_combo.currentIndexChanged.connect(self._apply_search)
        filters.addWidget(self.scale_combo, 2)
        self.advanced_box.setVisible(False)
        target_layout.addWidget(self.advanced_box)

        # Jaring untuk user yang menolak unduhan saat dialog dibuka.
        # Tombolnya ada di tempat kejadian supaya tidak perlu pindah tab.
        self.catalog_prompt = QWidget()
        prompt_layout = QVBoxLayout(self.catalog_prompt)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        self.prompt_label = QLabel(
            "Katalog KUGI belum diunduh. Sekali unduh. Setelah tersimpan, "
            "pencarian berjalan tanpa internet.")
        self.prompt_label.setWordWrap(True)
        prompt_layout.addWidget(self.prompt_label)
        prompt_row = QHBoxLayout()
        self.download_button = QPushButton("Unduh katalog")
        self.download_button.clicked.connect(self._on_download_clicked)
        prompt_row.addWidget(self.download_button)
        self.settings_link = QPushButton("Buka pengaturan")
        self.settings_link.clicked.connect(self.open_settings.emit)
        prompt_row.addWidget(self.settings_link)
        prompt_row.addStretch(1)
        prompt_layout.addLayout(prompt_row)
        self.catalog_prompt.setVisible(False)
        target_layout.addWidget(self.catalog_prompt)

        self.result_count = QLabel("")
        target_layout.addWidget(self.result_count)

        # Dua kolom. Deskripsi KUGI bisa 400 karakter, dan menampilkannya
        # sebagai tooltip justru menutupi baris hasil di bawahnya.
        self.result_list = QTreeWidget()
        self.result_list.setColumnCount(2)
        self.result_list.setHeaderHidden(True)
        self.result_list.setRootIsDecorated(False)
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.result_list.setWordWrap(True)
        self.result_list.setColumnWidth(0, RESULT_NAME_WIDTH)
        self.result_list.header().setStretchLastSection(True)
        self.result_list.itemSelectionChanged.connect(self._on_result_selected)
        self.result_list.itemDoubleClicked.connect(self._on_load_schema)
        target_layout.addWidget(self.result_list)

        action_row = QHBoxLayout()
        self.schema_info = QLabel("Katalog belum diunduh.")
        self.schema_info.setWordWrap(True)
        action_row.addWidget(self.schema_info, 1)
        self.load_schema_button = QPushButton("Muat skema")
        self.load_schema_button.clicked.connect(self._on_load_schema)
        action_row.addWidget(self.load_schema_button)
        target_layout.addLayout(action_row)
        layout.addWidget(target_box)

        mapping_box = QGroupBox("3  Pengaturan kolom/atribut tabel")
        mapping_layout = QVBoxLayout(mapping_box)
        self.mapping_panel = MappingPanel()
        self.mapping_panel.changed.connect(self._on_mapping_changed)
        mapping_layout.addWidget(self.mapping_panel)

        layout.addWidget(mapping_box, 1)

        output_box = QGroupBox("4  Pengaturan hasil")
        output_layout = QVBoxLayout(output_box)

        formats = QHBoxLayout()
        self.shp_check = QCheckBox("Shapefile (.shp)")
        self.shp_check.setChecked(True)
        self.shp_check.stateChanged.connect(self._on_format_changed)
        formats.addWidget(self.shp_check)
        self.gpkg_check = QCheckBox("GeoPackage (.gpkg)")
        self.gpkg_check.stateChanged.connect(self._on_format_changed)
        formats.addWidget(self.gpkg_check)
        self.qml_check = QCheckBox("Tulis .qml (domain nilai)")
        self.qml_check.setChecked(True)
        formats.addWidget(self.qml_check)
        formats.addStretch(1)
        output_layout.addLayout(formats)

        output_layout.addWidget(QLabel("Folder keluaran"))
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Pilih folder tempat berkas ditulis")
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Telusuri")
        browse.clicked.connect(self._on_browse)
        folder_row.addWidget(browse)
        output_layout.addLayout(folder_row)

        output_layout.addWidget(QLabel("Nama berkas"))
        self.name_edit = QLineEdit()
        output_layout.addWidget(self.name_edit)

        # "peta" dipakai, bukan "project", mengikuti frasa QGIS sendiri di
        # dialog Save Vector Layer As ("Add saved file to map").
        self.add_check = QCheckBox("Tambahkan hasil ke peta")
        self.add_check.setChecked(True)
        output_layout.addWidget(self.add_check)

        self.output_info = QLabel("")
        self.output_info.setWordWrap(True)
        output_layout.addWidget(self.output_info)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        output_layout.addWidget(self.progress)

        actions = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        actions.addWidget(self.status_label, 1)
        # Satu tombol untuk seluruh alur: bangun, tulis berkas, periksa,
        # tampilkan hasil. Tidak pernah dikunci, karena temuan validasi
        # adalah laporan, bukan penghalang. Ditaruh di dalam panel ini
        # karena yang dihasilkannya persis apa yang diatur di sini.
        self.process_button = QPushButton("Proses standardisasi KUGI")
        self.process_button.clicked.connect(self._on_process)
        actions.addWidget(self.process_button)
        output_layout.addLayout(actions)
        layout.addWidget(output_box)

        self.result_box = QGroupBox("5  Hasil")
        self.result_box.setVisible(False)
        result_layout = QVBoxLayout(self.result_box)
        self.result_summary = QLabel("")
        self.result_summary.setWordWrap(True)
        result_layout.addWidget(self.result_summary)

        self.result_tree = QTreeWidget()
        self.result_tree.setColumnCount(4)
        self.result_tree.setHeaderLabels(["", "Field", "Catatan", "Fitur"])
        self.result_tree.setRootIsDecorated(False)
        self.result_tree.setUniformRowHeights(True)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.result_tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_tree.itemDoubleClicked.connect(self._on_result_activated)
        result_layout.addWidget(self.result_tree)

        result_actions = QHBoxLayout()
        self.result_files = QLabel("")
        self.result_files.setWordWrap(True)
        result_actions.addWidget(self.result_files, 1)
        self.select_button = QPushButton("Pilih di peta")
        self.select_button.setToolTip(
            "Pilih fitur bermasalah pada baris yang sedang disorot")
        self.select_button.clicked.connect(self._on_select_features)
        result_actions.addWidget(self.select_button)
        self.open_folder_button = QPushButton("Buka folder")
        self.open_folder_button.clicked.connect(self._on_open_folder)
        result_actions.addWidget(self.open_folder_button)
        result_layout.addLayout(result_actions)

        layout.addWidget(self.result_box)

        self._on_layer_changed(self.layer_combo.currentLayer())

    # --------------------------------------------------------------- katalog

    def _on_download_clicked(self):
        if self.download_index():
            self._set_catalog_missing(False)

    def _set_catalog_missing(self, missing):
        """Nyalakan atau matikan kotak cari beserta ajakan unduhnya."""
        self.catalog_prompt.setVisible(missing)
        self.search_edit.setEnabled(not missing)
        self.result_list.setEnabled(not missing)
        self.load_schema_button.setEnabled(not missing)
        if missing:
            self.schema_info.setText("")

    def check_catalog(self):
        """Dipanggil sekali saat dialog dibuka.

        Pemicunya di sini, bukan saat user mengetik. Dialog yang muncul
        tiba-tiba di tengah pengetikan terasa seperti gangguan, padahal
        yang dilakukan user cuma mengetik.
        """
        if self._all_refs:
            self._set_catalog_missing(False)
            return
        box = QMessageBox(self)
        box.setWindowTitle("KUGI")
        box.setText("Katalog KUGI belum tersimpan.")
        box.setInformativeText(
            "Sekali unduh. Setelah tersimpan, pencarian berjalan tanpa "
            "internet.")
        download = box.addButton("Unduh sekarang", QMessageBox.AcceptRole)
        settings = box.addButton("Buka pengaturan", QMessageBox.ActionRole)
        box.addButton("Nanti", QMessageBox.RejectRole)
        box.exec_()

        if box.clickedButton() is download:
            if self.download_index():
                self._set_catalog_missing(False)
                return
        elif box.clickedButton() is settings:
            self.open_settings.emit()
        self._set_catalog_missing(True)

    def _ensure_index(self):
        """Pastikan indeks katalog tersedia, unduh sekali kalau belum.

        API tidak punya endpoint pencarian lintas kategori, jadi 15
        kategori diunduh sekali lalu dicari secara lokal. Setelah itu
        pencarian instan dan tetap jalan tanpa internet.
        """
        self._all_refs = kugi_api.index_features()
        if self._all_refs:
            self._fill_categories()
            self._apply_search()
            return True

        answer = QMessageBox.question(
            self, "KUGI",
            "Katalog KUGI belum tersimpan. Unduh sekarang?\n\n"
            "Sekali unduh. Setelah tersimpan, pencarian berjalan tanpa "
            "internet.",
            QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            self.schema_info.setText(
                "Katalog belum diunduh. Buka tab Pengaturan untuk mengunduh.")
            return False
        return self.download_index()

    def download_index(self):
        """Unduh ulang indeks katalog dengan progres."""
        self._begin_progress(15)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            def report(position, total, name):
                self.progress.setRange(0, max(1, total))
                self.progress.setValue(position)
                self.progress.setFormat("Mengunduh %s  ·  %%p%%" % name)
                QApplication.processEvents()

            kugi_api.build_index(progress=report)
        except kugi_api.KugiApiError as exc:
            QApplication.restoreOverrideCursor()
            self._end_progress()
            QMessageBox.warning(
                self, "KUGI",
                "Gagal mengunduh katalog. Katalog lama tetap dipakai.\n\n%s"
                % exc)
            return False
        finally:
            QApplication.restoreOverrideCursor()
            self._end_progress()

        self._all_refs = kugi_api.index_features()
        self._fill_categories()
        self._apply_search()
        return True

    def _fill_categories(self):
        current = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("Semua kategori", "")
        for name in sorted({r.category for r in self._all_refs if r.category}):
            self.category_combo.addItem(name, name)
        if current:
            index = self.category_combo.findData(current)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
        self.category_combo.blockSignals(False)

    def _on_advanced_toggled(self, checked):
        self.advanced_box.setVisible(checked)

    def _update_geometry_label(self):
        """Sebut tipe datanya, bukan istilah geometri.

        "Hanya unsur bertipe Area, sesuai data masukan" langsung terbaca,
        sedangkan "batasi ke geometri layer" menuntut user paham istilah
        yang tidak mereka pakai sehari-hari.
        """
        token = self._layer_geometry_token()
        label = GEOMETRY_LABEL.get(token, "")
        if label:
            self.geometry_lock.setText(
                "Hanya unsur bertipe %s, sesuai data masukan" % label)
        else:
            self.geometry_lock.setText("Hanya unsur yang sesuai data masukan")

    def _layer_geometry_token(self):
        layer = self.layer_combo.currentLayer()
        if layer is None:
            return ""
        return QGIS_GEOMETRY_TO_TOKEN.get(layer.geometryType(), "")

    def _apply_search(self, *args):
        """Cari di empat field, urutkan berlapis menurut tempat kecocokan."""
        refs = getattr(self, "_all_refs", [])
        self.result_list.clear()
        if not refs:
            self.result_count.setText("")
            return

        needle = self.search_edit.text().strip()
        category = self.category_combo.currentData() or ""
        scale = self.scale_combo.currentData() or ""
        geometry = self.geometry_combo.currentData() or ""
        if self.geometry_lock.isChecked():
            geometry = self._layer_geometry_token() or geometry

        hits = []
        for ref in refs:
            if category and ref.category != category:
                continue
            if scale and ref.scale_code != scale:
                continue
            if geometry and ref.geometry_token != geometry:
                continue
            rank = ref.match_rank(needle)
            if rank is not None:
                hits.append((rank, ref.scale_code, ref))

        hits.sort(key=lambda h: (h[0], h[2].type_name, h[1]))

        shown = 0
        marked = False
        for rank, _, ref in hits[:MAX_RESULTS]:
            # Hanya satu pemisah yang dipasang, yaitu sebelum kelompok yang
            # cocok di deskripsi. Untuk kelompok lain kata yang dicari sudah
            # terlihat di kolom kiri, jadi labelnya mubazir. Untuk kelompok
            # ini tidak, dan tanpa penjelasan hasilnya tampak ngawur.
            if rank == ref.MATCH_DEFINITION and not marked and needle:
                header = QTreeWidgetItem(self.result_list)
                header.setText(0, "Sesuai deskripsi")
                header.setFlags(Qt.NoItemFlags)
                header.setForeground(0, QBrush(QColor(130, 130, 130)))
                marked = True

            item = QTreeWidgetItem(self.result_list)
            item.setText(0, self._describe(ref))
            item.setText(1, self._short_definition(ref.definition))
            item.setData(0, Qt.UserRole, ref.code)
            item.setToolTip(1, ref.definition or ref.type_name)
            shown += 1

        extra = ""
        if len(hits) > MAX_RESULTS:
            extra = ", dipotong di %d teratas" % MAX_RESULTS
        self.result_count.setText(
            "Menampilkan %d dari %d unsur%s" % (shown, len(refs), extra))
        if not hits:
            self.schema_info.setText(
                "Tidak ada unsur yang cocok. Longgarkan penyaring atau "
                "kosongkan kotak cari.")

    @staticmethod
    def _describe(ref):
        bits = [ref.type_name,
                "%s · %s" % (ref.scale_label or "skala ?",
                             GEOMETRY_LABEL.get(ref.geometry_token, "?"))]
        if ref.aliases:
            bits.append(ref.aliases)
        bits.append(ref.code)
        return "\n".join(bits)

    @staticmethod
    def _short_definition(text):
        """Potong deskripsi supaya daftar tetap terbaca.

        Dengan 117 hasil, deskripsi utuh sepanjang 400 karakter membuat
        daftarnya sangat tinggi. Teks lengkap tetap tersedia lewat tooltip.
        """
        clean = (text or "").strip()
        if len(clean) <= DEFINITION_LIMIT:
            return clean
        cut = clean[:DEFINITION_LIMIT]
        space = cut.rfind(" ")
        if space > DEFINITION_LIMIT - 40:
            cut = cut[:space]
        return cut.rstrip(" ,.;") + " …"

    def _on_result_selected(self):
        code = self._resolve_unsur_code()
        if code:
            self.schema_info.setText(
                "Unsur terpilih %s. Klik Muat skema untuk melanjutkan." % code)

    def _resolve_unsur_code(self):
        item = self.result_list.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    def _on_load_schema(self, *args):
        # Argumen diserap: slot ini tersambung ke sinyal yang
        # mengirim argumen (itemDoubleClicked, stateChanged)
        # sekaligus dipanggil langsung tanpa argumen.
        code = self._resolve_unsur_code()
        if not code:
            QMessageBox.information(
                self, "KUGI", "Pilih unsur dari daftar lebih dulu.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.schema = kugi_api.fetch_schema(code)
        except kugi_api.KugiApiError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "KUGI", str(exc))
            return
        QApplication.restoreOverrideCursor()

        self.schema_info.setText(
            "%s  ·  %s  ·  %d atribut" % (
                self.schema.type_name, self.schema.scale_label or "-",
                len(self.schema.attributes)))

        if not self.name_edit.text().strip():
            self.name_edit.setText(self.schema.type_name)

        self._rebuild_state()
        self._check_geometry_match()

    def _check_geometry_match(self):
        layer = self.layer_combo.currentLayer()
        if layer is None or self.schema is None:
            return
        token = self.schema.geometry_token
        actual = QGIS_GEOMETRY_TO_TOKEN.get(layer.geometryType())
        if token and actual and token != actual:
            QMessageBox.warning(
                self, "Tipe geometri berbeda",
                "Layer bertipe %s sedangkan unsur %s bertipe %s.\n"
                "Periksa lagi pilihan unsurnya." % (
                    GEOMETRY_LABEL.get(actual, actual), self.schema.type_name,
                    GEOMETRY_LABEL.get(token, token)))

    # ----------------------------------------------------------------- layer

    def _on_layer_changed(self, layer):
        if layer is None:
            self.layer_info.setText("Tidak ada layer vektor di project.")
            return
        token = QGIS_GEOMETRY_TO_TOKEN.get(layer.geometryType(), "")
        self._update_geometry_label()
        self.layer_info.setText(
            "%s  ·  %d fitur  ·  %d kolom  ·  %s" % (
                QgsWkbTypes.displayString(layer.wkbType()),
                layer.featureCount(), layer.fields().count(),
                layer.crs().authid() or "CRS tidak diketahui"))
        if token:
            index = self.geometry_combo.findData(token)
            if index >= 0:
                self.geometry_combo.setCurrentIndex(index)
        if self.schema is not None:
            self._rebuild_state()

    def _rebuild_state(self):
        layer = self.layer_combo.currentLayer()
        if layer is None or self.schema is None:
            return
        self.state = MappingState(self.schema, layer.fields())
        self.state.shapefile_target = self.shp_check.isChecked()
        self.state.auto_match()
        self.mapping_panel.set_state(self.state)
        self.memory_layer = None
        self._clear_results()
        self._on_mapping_changed()

    # ----------------------------------------------------------------- aksi

    def _on_format_changed(self, *args):
        # Argumen diserap: slot ini tersambung ke sinyal yang
        # mengirim argumen (itemDoubleClicked, stateChanged)
        # sekaligus dipanggil langsung tanpa argumen.
        if self.state is not None:
            self.state.shapefile_target = self.shp_check.isChecked()
            # Batas 10 karakter berlaku untuk kedua format bila shapefile
            # termasuk target, supaya skema keduanya identik.
            self.state.refresh_extra_names()
            self.mapping_panel.refresh()
        self._on_mapping_changed()

    def _on_mapping_changed(self):
        self.memory_layer = None
        self._clear_results()
        formats = self._formats()
        parts = []
        base = self.name_edit.text().strip() or "keluaran"
        if "shp" in formats:
            parts.append("%s.shp .shx .dbf .prj .cpg" % base)
        if "gpkg" in formats:
            parts.append("%s.gpkg" % base)
        if self.qml_check.isChecked() and formats:
            parts.append("%s.qml" % base)
        self.output_info.setText("Akan dibuat: " + ("  |  ".join(parts)
                                                    or "belum ada format dipilih"))
        layer = self.layer_combo.currentLayer()
        rows = layer.featureCount() if layer is not None else 0
        self.status_label.setText("Jumlah row data %s" % "{:,}".format(rows))

    def _formats(self):
        formats = []
        if self.shp_check.isChecked():
            formats.append("shp")
        if self.gpkg_check.isChecked():
            formats.append("gpkg")
        return formats

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Pilih folder keluaran")
        if folder:
            self.folder_edit.setText(folder)

    def _on_process(self):
        """Bangun, tulis berkas, periksa, tampilkan hasil. Satu tombol."""
        layer = self.layer_combo.currentLayer()
        if layer is None or self.state is None:
            QMessageBox.information(
                self, "KUGI", "Pilih layer dan muat skema unsur lebih dulu.")
            return

        folder = self.folder_edit.text().strip()
        base = self.name_edit.text().strip()
        formats = self._formats()
        if not folder or not base or not formats:
            QMessageBox.information(
                self, "KUGI",
                "Lengkapi folder, nama berkas, dan minimal satu format.")
            return

        blocking = [i for i in self.state.validate() if i.level == "error"]
        if blocking:
            QMessageBox.warning(
                self, "Pemetaan belum sah",
                "\n".join("%s: %s" % (i.target, i.message)
                          for i in blocking[:8]))
            return

        total = layer.featureCount()
        self.process_button.setEnabled(False)
        self._begin_progress(total)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._step("Menyusun struktur atribut", 0)
            built = build_memory_layer(self.state, layer,
                                       feedback=self._on_build_progress)
            if not built.ok:
                QApplication.restoreOverrideCursor()
                self._end_progress()
                self.process_button.setEnabled(True)
                QMessageBox.warning(self, "KUGI", "\n".join(built.messages)
                                    or "Gagal membangun layer sementara.")
                return
            self.memory_layer = built.layer

            self._step("Menulis berkas keluaran", total)
            written = write_outputs(self.memory_layer, folder, base, formats,
                                    self.qml_check.isChecked())

            self._step("Memeriksa hasil", total)
            issues = validate(self.memory_layer, self.schema,
                              self.shp_check.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
            self._end_progress()
            self.process_button.setEnabled(True)

        self.last_paths = list(written.written)
        self._show_results(built, written, issues)

        if written.ok and self.add_check.isChecked():
            self._add_to_map(base)

    def full_reset(self):
        """Reset penuh, dipanggil dari tombol Mulai baru di dialog.

        Yang dibersihkan: pencarian dan penyaringnya, daftar hasil,
        pengaturan kolom, dan pengaturan hasil termasuk folder dan nama
        berkas.

        Yang TIDAK disentuh: layer hasil yang sudah ditambahkan ke peta,
        berkas yang sudah tertulis di disk, simpanan katalog, dan isi tab
        Pengaturan. Tiga yang pertama hasil kerja user, yang terakhir
        preferensi yang berlaku lintas pekerjaan.
        """
        if self.state is not None and self.state.filled_count():
            answer = QMessageBox.question(
                self, "KUGI",
                "Mulai dari awal?\n\n"
                "Pencarian, pengaturan kolom, dan pengaturan hasil akan "
                "dikosongkan. Layer hasil di peta dan berkas yang sudah "
                "tersimpan tidak terhapus.",
                QMessageBox.Yes | QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        self.schema = None
        self.state = None
        self.memory_layer = None
        self.mapping_panel.set_state(None)

        self.search_edit.clear()
        self.result_list.clear()
        self.result_count.setText("")
        self.category_combo.setCurrentIndex(0)
        self.geometry_combo.setCurrentIndex(0)
        self.scale_combo.setCurrentIndex(0)
        self.geometry_lock.setChecked(True)
        self.advanced_button.setChecked(False)

        self.folder_edit.clear()
        self.name_edit.clear()
        self.shp_check.setChecked(True)
        self.gpkg_check.setChecked(False)
        self.qml_check.setChecked(True)
        self.add_check.setChecked(True)

        self._clear_results()
        self.status_label.setText("")
        self.schema_info.setText("Cari unsur untuk memulai.")
        self._update_geometry_label()
        self._apply_search()

    def reload_index(self):
        """Muat ulang indeks setelah katalog diperbarui di tab Pengaturan."""
        self._all_refs = kugi_api.index_features()
        self._index_attempted = False
        if self._all_refs:
            self._fill_categories()
            self._set_catalog_missing(False)
            self.schema_info.setText("Cari unsur untuk memulai.")
        else:
            self._set_catalog_missing(True)
        self._apply_search()

    def _clear_results(self):
        """Sembunyikan panel hasil ketika layer atau skema berganti.

        Hasil lama merujuk pemetaan lama, jadi menampilkannya terus akan
        menyesatkan.
        """
        self.result_box.setVisible(False)
        self.result_tree.clear()
        self.result_summary.setText("")
        self.result_files.setText("")
        self.last_paths = []

    def _begin_progress(self, total):
        """Tampilkan progres. Rentangnya dua kali jumlah fitur.

        Paruh pertama untuk penyalinan baris, paruh kedua untuk penulisan
        berkas dan pemeriksaan, yang tidak bisa dilaporkan per fitur.
        """
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, total * 2))
        self.progress.setValue(0)

    def _step(self, label, value):
        self.progress.setFormat("%s  ·  %%p%%" % label)
        self.progress.setValue(min(value, self.progress.maximum()))
        self.status_label.setText(label)
        QApplication.processEvents()

    def _on_build_progress(self, done):
        self.progress.setValue(min(done, self.progress.maximum()))
        self.progress.setFormat(
            "Menyalin baris data  ·  %d fitur" % done)
        QApplication.processEvents()

    def _end_progress(self):
        self.progress.setValue(self.progress.maximum())
        self.progress.setVisible(False)
        self.progress.setFormat("")

    def _show_results(self, built, written, issues):
        self.result_box.setVisible(True)
        self.result_tree.clear()

        total = self.memory_layer.featureCount() if self.memory_layer else 0
        source = self.layer_combo.currentLayer()
        expected = source.featureCount() if source is not None else total
        filled = self.state.filled_count()
        if written.ok:
            headline = "Selesai. %d fitur ditulis, %d dari %d field terisi." % (
                total, filled, len(self.state.mapping))
            if expected and total != expected:
                headline += (" Perhatian: layer sumber punya %d fitur."
                             % expected)
        else:
            headline = "Berkas gagal ditulis. %s" % " ".join(written.messages)
        self.result_summary.setText(headline)
        self.status_label.setText(headline)

        rows = list(issues)
        for note in built.messages:
            rows.append(_Note(LEVEL_INFO, "", note))
        for note in written.messages:
            rows.append(_Note(LEVEL_ERROR if not written.ok else LEVEL_INFO,
                              "", note))

        for issue in rows:
            item = QTreeWidgetItem(self.result_tree)
            item.setText(0, LEVEL_LABEL.get(issue.level, ""))
            item.setText(1, issue.field)
            item.setText(2, issue.message)
            ids = getattr(issue, "feature_ids", [])
            item.setText(3, str(len(ids)) if ids else "")
            item.setData(0, Qt.UserRole, ids)
            if issue.level == LEVEL_ERROR:
                item.setForeground(0, QBrush(QColor(190, 60, 60)))
            elif issue.level == LEVEL_WARNING:
                item.setForeground(0, QBrush(QColor(190, 120, 20)))
            else:
                item.setForeground(0, QBrush(QColor(130, 130, 130)))

        if not rows:
            item = QTreeWidgetItem(self.result_tree)
            item.setText(2, "Tidak ada catatan.")

        for column in range(4):
            self.result_tree.resizeColumnToContents(column)
        self.result_tree.setFixedHeight(
            max(2, self.result_tree.topLevelItemCount()) * 22 + 34)

        names = [os.path.basename(path) for path in written.written]
        self.result_files.setText("  ·  ".join(names) if names else "")
        self.open_folder_button.setEnabled(bool(written.written))

    def _add_to_map(self, base):
        """Muat berkas hasil, bukan layer memori.

        Dengan memuat berkas yang baru ditulis, .qml di sebelahnya ikut
        terbaca sehingga dropdown domain langsung teruji di attribute table.
        Layer memori tidak pernah menyentuh .qml, jadi cacat penulisannya
        baru ketahuan belakangan.
        """
        target = None
        for path in self.last_paths:
            if path.lower().endswith((".shp", ".gpkg")):
                target = path
                break
        if target is None:
            return
        loaded = QgsVectorLayer(target, base, "ogr")
        if loaded.isValid():
            QgsProject.instance().addMapLayer(loaded)
        else:
            self.status_label.setText(
                self.status_label.text() + "  Berkas hasil gagal dimuat ke peta.")

    def _on_result_activated(self, item, column):
        self._on_select_features()

    def _on_select_features(self):
        item = self.result_tree.currentItem()
        if item is None:
            return
        ids = item.data(0, Qt.UserRole) or []
        if not ids:
            return
        layer = None
        for candidate in QgsProject.instance().mapLayers().values():
            if candidate.name() == self.name_edit.text().strip():
                layer = candidate
                break
        if layer is None:
            layer = self.memory_layer
        if layer is None:
            return
        layer.selectByIds(list(ids))
        if self.iface is not None:
            self.iface.setActiveLayer(layer)
            self.iface.mapCanvas().zoomToSelected(layer)

    def _on_open_folder(self):
        folder = self.folder_edit.text().strip()
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
