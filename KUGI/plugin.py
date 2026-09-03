# -*- coding: utf-8 -*-
"""Kelas utama plugin."""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .ui.main_dialog import KugiDialog

ICON_PATH = os.path.join(os.path.dirname(__file__), "icon.png")
MENU_TITLE = "KUGI"


class KugiStandardizerPlugin(object):

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon = QIcon(ICON_PATH) if os.path.isfile(ICON_PATH) else QIcon()
        self.action = QAction(icon, MENU_TITLE, self.iface.mainWindow())
        self.action.setToolTip(
            "Standardisasi atribut data spasial sesuai KUGI dan validasi QC")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(MENU_TITLE, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginVectorMenu(MENU_TITLE, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None

    def run(self):
        if self.dialog is None:
            self.dialog = KugiDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
