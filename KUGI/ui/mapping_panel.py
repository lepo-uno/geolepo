# -*- coding: utf-8 -*-
"""Panel pemetaan berbasis dropdown.

Menggantikan panel dual list dengan bentuk yang dikenal pengguna ArcGIS:
satu baris per field KUGI, kolom sumbernya dipilih lewat dropdown. Dua
alasan penggantian. Bentuk ini sudah dikenal karena separuh pedoman BIG
memakai ArcGIS, dan drag and drop sudah dua kali menghasilkan bug jenis
sama, yaitu tampilan tidak sinkron dengan model.

Kolom yang sudah dipakai hilang dari dropdown baris lain dan dari daftar
kolom tambahan, sehingga aturan satu kolom satu tujuan terlihat langsung.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, 
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, 
                                 QWidget)

from ..compat import (ITEM_EDITABLE, NO_EDIT_TRIGGERS, RESIZE_STRETCH,
                      RESIZE_TO_CONTENTS, ROLE_USER, SCROLLBAR_OFF,
                      SELECT_NONE, qvariant_for, type_display_name)
from ..mapping import (AUTO_FIELDS, MODE_CONSTANT, MODE_CRS, MODE_SEQUENCE, 
                       MODE_SOURCE)

COL_FIELD = 0
COL_TYPE = 1
COL_SOURCE = 2
COL_FILLED = 3
COL_NOTE = 4

NONE_TOKEN = "\x00none"
AUTO_TOKEN = "\x00auto"

ROW_HEIGHT = 28


class MappingPanel(QWidget):
    """Tabel pemetaan plus daftar centang kolom tambahan."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super(MappingPanel, self).__init__(parent)
        self.state = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Cari field KUGI")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        tools.addWidget(self.search, 1)

        self.clear_button = QPushButton("Kosongkan pemetaan")
        self.clear_button.clicked.connect(self._on_clear_all)
        tools.addWidget(self.clear_button)
        layout.addLayout(tools)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Field KUGI", "Tipe", "Kolom sumber", "Terisi", "Catatan"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(SELECT_NONE)
        self.table.setEditTriggers(NO_EDIT_TRIGGERS)
        self.table.setAlternatingRowColors(True)
        # Scrollbar internal dimatikan: tabel tumbuh mengikuti isinya dan
        # yang bergulir hanya jendela luar, supaya tidak ada gulir bersarang.
        self.table.setVerticalScrollBarPolicy(SCROLLBAR_OFF)
        self.table.setHorizontalScrollBarPolicy(SCROLLBAR_OFF)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_FIELD, RESIZE_TO_CONTENTS)
        header.setSectionResizeMode(COL_TYPE, RESIZE_TO_CONTENTS)
        header.setSectionResizeMode(COL_SOURCE, RESIZE_STRETCH)
        header.setSectionResizeMode(COL_FILLED, RESIZE_TO_CONTENTS)
        header.setSectionResizeMode(COL_NOTE, RESIZE_TO_CONTENTS)
        layout.addWidget(self.table)

        self.extras_label = QLabel(
            "Kolom tambahan — pilih/check untuk digabung dengan struktur "
            "tabel standar KUGI")
        self.extras_label.setWordWrap(True)
        layout.addWidget(self.extras_label)

        self.extras_table = QTableWidget(0, 4)
        self.extras_table.setHorizontalHeaderLabels(
            ["Bawa", "Kolom eksisting", "Nama di keluaran", "Terisi"])
        self.extras_table.verticalHeader().setVisible(False)
        self.extras_table.setSelectionMode(SELECT_NONE)
        self.extras_table.setAlternatingRowColors(True)
        self.extras_table.setVerticalScrollBarPolicy(SCROLLBAR_OFF)
        self.extras_table.setHorizontalScrollBarPolicy(SCROLLBAR_OFF)
        eh = self.extras_table.horizontalHeader()
        eh.setSectionResizeMode(0, RESIZE_TO_CONTENTS)
        eh.setSectionResizeMode(1, RESIZE_STRETCH)
        eh.setSectionResizeMode(2, RESIZE_STRETCH)
        eh.setSectionResizeMode(3, RESIZE_TO_CONTENTS)
        self.extras_table.cellChanged.connect(self._on_extra_renamed)
        layout.addWidget(self.extras_table)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    # ----------------------------------------------------------- pemuatan

    def set_state(self, state):
        self.state = state
        self.search.clear()
        if state is None:
            self.table.setRowCount(0)
            self.extras_table.setRowCount(0)
            self.summary.setText("")
            self._resize_tables()
            return
        self.refresh()

    def refresh(self):
        self._loading = True
        try:
            self._fill_table()
            self._fill_extras()
            self._update_summary()
        finally:
            self._loading = False
        self._apply_filter()
        self._resize_tables()

    def _resize_tables(self):
        """Tinggi tabel mengikuti jumlah baris, tanpa gulir sendiri."""
        for table in (self.table, self.extras_table):
            rows = table.rowCount()
            height = table.horizontalHeader().height() + rows * ROW_HEIGHT + 4
            table.setMinimumHeight(height)
            table.setMaximumHeight(height)

    # -------------------------------------------------------------- tabel

    def _available_sources(self, current=""):
        """Kolom yang boleh dipilih baris ini.

        Berisi kolom yang belum dipakai baris lain, ditambah pilihan baris
        ini sendiri supaya tidak hilang dari dropdownnya.
        """
        names = list(self.state.unmapped_sources())
        if current and current not in names:
            names.append(current)
        return names

    def _build_combo(self, field_name, spec):
        combo = QComboBox()

        # FCODE dan OBJECTID dikunci. Keduanya penanda identitas: yang satu
        # menyatakan unsur apa ini, yang lain menyatakan baris mana ini.
        # Membiarkannya bisa ditimpa kolom lain membuka jalan ke keluaran
        # yang mengaku sebagai sesuatu yang bukan dirinya.
        if self.state.is_locked(field_name):
            combo.addItem(self._auto_label(field_name), AUTO_TOKEN)
            combo.setEnabled(False)
            combo.setToolTip(
                "Diisi otomatis oleh plugin dan tidak bisa diubah")
            return combo

        combo.addItem("<None>", NONE_TOKEN)
        auto_mode = AUTO_FIELDS.get(field_name)
        if auto_mode is not None:
            combo.addItem("<otomatis: %s>" % self._auto_label(field_name),
                          AUTO_TOKEN)

        current = spec.source if spec.mode == MODE_SOURCE else ""
        for name in self._available_sources(current):
            filled = self.state.fill_text(self.state.source_filled(name))
            label = "%s  [%s]" % (name, self.state.source_type_name(name))
            if filled:
                label = "%s  ·  %s terisi" % (label, filled)
            combo.addItem(label, name)

        if spec.mode == MODE_SOURCE and current:
            combo.setCurrentIndex(combo.findData(current))
        elif spec.mode in (MODE_CONSTANT, MODE_SEQUENCE, MODE_CRS):
            combo.setCurrentIndex(combo.findData(AUTO_TOKEN))
        else:
            combo.setCurrentIndex(combo.findData(NONE_TOKEN))

        combo.currentIndexChanged.connect(
            lambda _, f=field_name, c=combo: self._on_source_picked(f, c))
        return combo

    def _auto_label(self, field_name):
        mode = AUTO_FIELDS.get(field_name)
        if mode == MODE_CONSTANT:
            return self.state.schema.code
        if mode == MODE_SEQUENCE:
            return "nomor urut otomatis"
        if mode == MODE_CRS:
            return self.state.crs_identifier or "CRS layer"
        return "otomatis"

    def _fill_table(self):
        self.table.setRowCount(0)
        if self.state is None or self.state.schema is None:
            return
        for att in self.state.schema.attributes:
            if qvariant_for(att.value_type) is None:
                continue
            spec = self.state.mapping.get(att.name)
            if spec is None:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)

            item = QTableWidgetItem(att.name)
            item.setToolTip(att.definition or att.name)
            self.table.setItem(row, COL_FIELD, item)

            qtype = qvariant_for(att.value_type)
            self.table.setItem(row, COL_TYPE,
                               QTableWidgetItem(type_display_name(qtype)))

            self.table.setCellWidget(row, COL_SOURCE,
                                     self._build_combo(att.name, spec))

            filled = self.state.fill_text(self.state.target_filled(att.name))
            self.table.setItem(row, COL_FILLED, QTableWidgetItem(filled))

            note = "%d domain" % len(att.domain) if att.has_domain else ""
            self.table.setItem(row, COL_NOTE, QTableWidgetItem(note))
            self.table.setRowHeight(row, ROW_HEIGHT)

    # ------------------------------------------------------------- ekstra

    def _fill_extras(self):
        self.extras_table.setRowCount(0)
        if self.state is None:
            return
        checked = {e.source: e.output for e in self.state.extras}
        candidates = list(checked.keys()) + list(self.state.unmapped_sources())
        seen = set()
        ordered = [n for n in self.state.source_field_names()
                   if n in candidates and not (n in seen or seen.add(n))]

        for name in ordered:
            row = self.extras_table.rowCount()
            self.extras_table.insertRow(row)

            box = QCheckBox()
            box.setChecked(name in checked)
            box.stateChanged.connect(
                lambda _, n=name: self._on_extra_toggled(n))
            holder = QWidget()
            wrap = QHBoxLayout(holder)
            wrap.setContentsMargins(6, 0, 0, 0)
            wrap.addWidget(box)
            wrap.addStretch(1)
            self.extras_table.setCellWidget(row, 0, holder)

            label = "%s  [%s]" % (name, self.state.source_type_name(name))
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~ITEM_EDITABLE)
            self.extras_table.setItem(row, 1, item)

            output = QTableWidgetItem(checked.get(name, ""))
            if name in checked:
                output.setFlags(output.flags() | ITEM_EDITABLE)
            else:
                output.setFlags(output.flags() & ~ITEM_EDITABLE)
            output.setData(ROLE_USER, name)
            self.extras_table.setItem(row, 2, output)

            filled = self.state.fill_text(self.state.source_filled(name))
            cell = QTableWidgetItem(filled)
            cell.setFlags(cell.flags() & ~ITEM_EDITABLE)
            self.extras_table.setItem(row, 3, cell)
            self.extras_table.setRowHeight(row, ROW_HEIGHT)

    # ------------------------------------------------------------ reaksi

    def _on_source_picked(self, field_name, combo):
        if self._loading or self.state is None:
            return
        token = combo.currentData()
        if token == NONE_TOKEN or token == AUTO_TOKEN:
            self.state.clear(field_name)
        else:
            self.state.assign(field_name, token)
        self.refresh()
        self.changed.emit()

    def _on_extra_toggled(self, source_name):
        if self._loading or self.state is None:
            return
        if source_name in [e.source for e in self.state.extras]:
            self.state.remove_extra(source_name)
        else:
            self.state.add_extra(source_name)
        self.refresh()
        self.changed.emit()

    def _on_extra_renamed(self, row, column):
        if self._loading or column != 2 or self.state is None:
            return
        item = self.extras_table.item(row, column)
        if item is None:
            return
        source = item.data(ROLE_USER)
        if not source:
            return
        if source in [e.source for e in self.state.extras]:
            self.state.rename_extra(source, item.text())
            self.refresh()
            self.changed.emit()

    def _on_clear_all(self):
        if self.state is None:
            return
        self.state.clear_all()
        self.refresh()
        self.changed.emit()

    def _apply_filter(self, *args):
        needle = self.search.text().strip().lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_FIELD)
            hidden = bool(needle) and needle not in (item.text().lower()
                                                     if item else "")
            self.table.setRowHidden(row, hidden)

    def _update_summary(self):
        if self.state is None:
            self.summary.setText("")
            return
        text = "%d dari %d field terisi · %d kolom tambahan" % (
            self.state.filled_count(), len(self.state.mapping),
            len(self.state.extras))
        hidden = self.state.hidden_source_count()
        if hidden:
            text += " · %d kolom OBJECTID sumber disembunyikan" % hidden
        self.summary.setText(text)
