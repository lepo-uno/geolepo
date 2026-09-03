# -*- coding: utf-8 -*-
"""Dialog utama.

Hanya dua tab. Tab QC terpisah dihapus karena memaksa user memilih ulang
kategori, geometri, skala, dan unsur padahal semuanya baru saja dipilih di
tab standardisasi. Validasinya tetap berjalan, hasilnya muncul sebagai
panel di bawah tombol proses.

Setiap tab dibungkus QScrollArea supaya jendela bisa dikecilkan dan isinya
tetap terjangkau lewat gulir.
"""

import os

from qgis.PyQt.QtGui import QIcon, QGuiApplication
from qgis.PyQt.QtWidgets import (QDialog, QFrame, QHBoxLayout,
                                 QPushButton, QScrollArea,
                                 QTabWidget, QVBoxLayout)

from .settings_tab import SettingsTab
from .standardize_tab import StandardizeTab

ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "icon.png")

PREFERRED_WIDTH = 1080
PREFERRED_HEIGHT = 820
MARGIN = 80


def scrollable(widget):
    """Bungkus widget dalam area gulir yang ikut melebar."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


class KugiDialog(QDialog):

    def __init__(self, iface, parent=None):
        super(KugiDialog, self).__init__(parent)
        self.iface = iface
        self.setWindowTitle("KUGI")
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.setSizeGripEnabled(True)
        self.setMinimumSize(720, 400)
        self.resize(*self._initial_size())

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        self.standardize_tab = StandardizeTab(iface)
        self.settings_tab = SettingsTab()

        self.tabs.addTab(scrollable(self.standardize_tab), "Standardisasi")
        self.tabs.addTab(scrollable(self.settings_tab), "Pengaturan")
        layout.addWidget(self.tabs)

        # Mulai baru dan Keluar berada di baris tombol milik dialog, bukan
        # di dalam form. Keduanya dipisahkan jarak lebar karena akibatnya
        # berlawanan dan tidak boleh tertekan karena salah sasaran.
        footer = QHBoxLayout()
        self.reset_button = QPushButton("Mulai baru")
        self.reset_button.clicked.connect(self._on_reset)
        footer.addWidget(self.reset_button)
        footer.addStretch(1)
        self.close_button = QPushButton("Keluar")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        # Katalog diperbarui dari tab Pengaturan harus terlihat oleh tab
        # Standardisasi. Tanpa ini user mengunduh di sana lalu kembali dan
        # menemukan kotak carinya masih mati, seperti unduhannya gagal.
        self.settings_tab.catalog_updated.connect(
            self.standardize_tab.reload_index)
        self.standardize_tab.open_settings.connect(self._show_settings)

        # Diperiksa setelah seluruh widget terpasang dan sinyal
        # tersambung, supaya kotak pesan tidak muncul sebelum
        # dialognya sendiri terlihat.
        self.standardize_tab.check_catalog()

    def _show_settings(self):
        self.tabs.setCurrentIndex(1)

    def _on_reset(self):
        self.standardize_tab.full_reset()

    @staticmethod
    def _initial_size():
        width, height = PREFERRED_WIDTH, PREFERRED_HEIGHT
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(720, available.width() - MARGIN))
            height = min(height, max(400, available.height() - MARGIN))
        return width, height
