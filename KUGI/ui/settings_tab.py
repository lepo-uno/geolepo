# -*- coding: utf-8 -*-
"""Tab pengaturan."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QFormLayout,
                                 QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QMessageBox, QPushButton, QSpinBox,
                                 QVBoxLayout, QWidget)
from qgis.core import QgsSettings

from .. import kugi_api


class SettingsTab(QWidget):

    catalog_updated = pyqtSignal()

    def __init__(self, parent=None):
        super(SettingsTab, self).__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        catalog_box = QGroupBox("Katalog KUGI")
        catalog_layout = QVBoxLayout(catalog_box)
        self.catalog_info = QLabel("")
        self.catalog_info.setWordWrap(True)
        catalog_layout.addWidget(self.catalog_info)

        catalog_row = QHBoxLayout()
        self.update_button = QPushButton("Perbarui katalog dari API")
        self.update_button.clicked.connect(self._on_update_catalog)
        catalog_row.addWidget(self.update_button)
        self.clear_button = QPushButton("Bersihkan simpanan")
        self.clear_button.clicked.connect(self._on_clear_cache)
        catalog_row.addWidget(self.clear_button)
        catalog_row.addStretch(1)
        catalog_layout.addLayout(catalog_row)

        note = QLabel(
            "Perbarui mengambil ulang daftar kategori dan unsur, lalu "
            "menandai skema atribut yang tersimpan sebagai basi supaya "
            "diambil lagi saat dipakai. Bersihkan membuang semuanya.")
        note.setWordWrap(True)
        catalog_layout.addWidget(note)
        layout.addWidget(catalog_box)

        api_box = QGroupBox("Koneksi API")
        api_form = QFormLayout(api_box)
        self.url_edit = QLineEdit(kugi_api.base_url())
        api_form.addRow("Alamat", self.url_edit)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(kugi_api.timeout_seconds())
        self.timeout_spin.setSuffix(" detik")
        api_form.addRow("Batas waktu", self.timeout_spin)
        self.offline_check = QCheckBox(
            "Pakai snapshot bawaan bila API tidak bisa diakses")
        self.offline_check.setChecked(kugi_api.use_offline_fallback())
        api_form.addRow("", self.offline_check)

        api_row = QHBoxLayout()
        self.test_button = QPushButton("Uji koneksi")
        self.test_button.clicked.connect(self._on_test)
        api_row.addWidget(self.test_button)
        self.save_button = QPushButton("Simpan pengaturan")
        self.save_button.clicked.connect(self._on_save)
        api_row.addWidget(self.save_button)
        api_row.addStretch(1)
        api_form.addRow("", self._wrap(api_row))

        self.api_status = QLabel("")
        self.api_status.setWordWrap(True)
        api_form.addRow("", self.api_status)
        layout.addWidget(api_box)

        layout.addStretch(1)
        self._refresh_catalog_info()

    @staticmethod
    def _wrap(inner):
        holder = QWidget()
        holder.setLayout(inner)
        return holder

    def _refresh_catalog_info(self):
        summary = kugi_api.index_summary()
        if not summary:
            self.catalog_info.setText(
                "Katalog belum diunduh. Tekan Perbarui untuk mengambil "
                "seluruh kategori sekali, sekitar 3 MB.")
            return
        self.catalog_info.setText(
            "Versi tersimpan: %s\n"
            "Diambil: %s\n"
            "Isi: %d kategori, %s unsur\n"
            "Skema atribut tersimpan: %d unsur\n"
            "Ukuran indeks: %s KB"
            % (summary["version"], summary["fetched"] or "tidak diketahui",
               summary["categories"], "{:,}".format(summary["features"]),
               summary["schemas"], "{:,}".format(summary["bytes"] // 1024)))

    def _on_update_catalog(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            kugi_api.build_index()
        except kugi_api.KugiApiError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(
                self, "KUGI",
                "Gagal memperbarui. Katalog lama tetap dipakai.\n\n%s" % exc)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_catalog_info()
        self.catalog_updated.emit()
        QMessageBox.information(self, "KUGI", "Katalog diperbarui.")

    def _on_clear_cache(self):
        answer = QMessageBox.question(
            self, "KUGI",
            "Buang seluruh simpanan katalog dan skema?\n\n"
            "Katalog perlu diunduh ulang setelah ini.",
            QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        removed = kugi_api.clear_cache()
        self._refresh_catalog_info()
        self.catalog_updated.emit()
        QMessageBox.information(
            self, "KUGI", "%d berkas simpanan dibuang." % removed)

    def _on_test(self):
        kugi_api.set_base_url(self.url_edit.text().strip())
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, message = kugi_api.test_connection()
        finally:
            QApplication.restoreOverrideCursor()
        self.api_status.setText(message)

    def _on_save(self):
        kugi_api.set_base_url(self.url_edit.text().strip())
        settings = QgsSettings()
        settings.setValue(kugi_api.SETTINGS_PREFIX + "timeout",
                          self.timeout_spin.value())
        settings.setValue(kugi_api.SETTINGS_PREFIX + "offline_fallback",
                          self.offline_check.isChecked())
        self.api_status.setText("Pengaturan disimpan.")
