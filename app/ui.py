from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from .config import default_config_path, load_config
from .label_pdf import LabelRow, generate_labels_pdf
from .sapo_client import ReceiveInventoryItem, SapoClient


@dataclass
class RowState:
    item: ReceiveInventoryItem
    print_qty: int


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("In tem 72x22 - Receive Inventories")
        self.resize(980, 600)

        cfg_path = default_config_path()
        if not cfg_path.exists():
            # help user create config.json quickly
            raise FileNotFoundError(
                f"Không thấy config.json tại: {cfg_path}\n"
                "Hãy copy config.example.json -> config.json và điền token."
            )

        self._cfg = load_config(cfg_path)
        self._client = SapoClient(self._cfg)
        self._rows: list[RowState] = []
        self._last_strategy = ""

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.setStyleSheet(
            """
            QMainWindow { background: #0b1220; }
            QLabel { color: #e5e7eb; font-size: 12px; }
            QLineEdit {
              background: #0f172a;
              color: #e5e7eb;
              border: 1px solid #22304a;
              border-radius: 10px;
              padding: 10px 12px;
              font-size: 13px;
            }
            QLineEdit:focus { border-color: #4f46e5; }
            QPushButton {
              background: #4f46e5;
              color: white;
              border: none;
              border-radius: 10px;
              padding: 10px 14px;
              font-weight: 600;
            }
            QPushButton:hover { background: #4338ca; }
            QPushButton:disabled { background: #334155; color: #cbd5e1; }
            QTableWidget {
              background: #0f172a;
              color: #e5e7eb;
              gridline-color: #22304a;
              border: 1px solid #22304a;
              border-radius: 12px;
              selection-background-color: #1d4ed8;
            }
            QHeaderView::section {
              background: #111c33;
              color: #e5e7eb;
              padding: 8px 10px;
              border: none;
              border-bottom: 1px solid #22304a;
              font-weight: 700;
            }
            QSpinBox {
              background: #0b1220;
              color: #e5e7eb;
              border: 1px solid #22304a;
              border-radius: 10px;
              padding: 6px 10px;
            }
            """
        )

        top = QHBoxLayout()
        layout.addLayout(top)
        top.setSpacing(10)

        top.addWidget(QLabel("Mã receive_inventories:"))
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("Ví dụ: RI000123 hoặc mã bạn nhập")
        self.code_input.returnPressed.connect(self.on_fetch)
        top.addWidget(self.code_input, 1)

        self.fetch_btn = QPushButton("Tải đơn")
        self.fetch_btn.clicked.connect(self.on_fetch)
        top.addWidget(self.fetch_btn)

        self.status_lbl = QLabel("")
        self.status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_lbl.setStyleSheet("color: #93c5fd;")
        layout.addWidget(self.status_lbl)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["SKU", "Tên", "Số lượng", "Số lượng in"])
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 520)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 120)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        bottom.setSpacing(10)

        self.export_btn = QPushButton("Xuất PDF tem")
        self.export_btn.clicked.connect(self.on_export_pdf)
        self.export_btn.setEnabled(False)
        bottom.addWidget(self.export_btn)

        self.open_btn = QPushButton("Mở PDF")
        self.open_btn.clicked.connect(self.on_open_pdf)
        self.open_btn.setEnabled(False)
        bottom.addWidget(self.open_btn)

        bottom.addStretch(1)

        bottom.addWidget(QLabel("2 QR/tem:"))
        self.two_up = QSpinBox()
        self.two_up.setMinimum(0)
        self.two_up.setMaximum(1)
        self.two_up.setValue(1)
        self.two_up.setToolTip("1 = chia đôi tem, in 2 QR giống nhau trên 1 tem")
        bottom.addWidget(self.two_up)

        self._last_pdf: Path | None = None

    def _set_rows(self, items: list[ReceiveInventoryItem]) -> None:
        self._rows = [RowState(item=i, print_qty=max(0, int(i.quantity))) for i in items]
        self.table.setRowCount(len(self._rows))

        for r, st in enumerate(self._rows):
            self.table.setItem(r, 0, QTableWidgetItem(st.item.sku))
            self.table.setItem(r, 1, QTableWidgetItem(st.item.name))
            qty_item = QTableWidgetItem(str(st.item.quantity))
            qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            qty_item.setFlags(qty_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, qty_item)

            spin = QSpinBox()
            spin.setMinimum(0)
            spin.setMaximum(100000)
            spin.setValue(st.print_qty)
            spin.valueChanged.connect(lambda v, row=r: self._on_print_qty_changed(row, v))
            self.table.setCellWidget(r, 3, spin)

        self.export_btn.setEnabled(len(self._rows) > 0)

    def _on_print_qty_changed(self, row: int, v: int) -> None:
        if 0 <= row < len(self._rows):
            self._rows[row].print_qty = int(v)

    def on_fetch(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            QMessageBox.warning(self, "Thiếu mã", "Bạn chưa nhập mã receive_inventories.")
            return

        self.fetch_btn.setEnabled(False)
        self.code_input.setEnabled(False)
        self.status_lbl.setText("Đang gọi API…")
        QApplication.processEvents()
        try:
            items, strategy = self._client.get_receive_inventory(code)
            self._last_strategy = strategy
            self._set_rows(items)
            self.status_lbl.setText(f"Tải OK: {len(items)} dòng. Auth strategy: {strategy}")
        except Exception as e:
            self.status_lbl.setText("Lỗi.")
            QMessageBox.critical(self, "Lỗi gọi API", str(e))
            self.export_btn.setEnabled(False)
        finally:
            self.fetch_btn.setEnabled(True)
            self.code_input.setEnabled(True)
            self.code_input.setFocus()

    def on_export_pdf(self) -> None:
        if not self._rows:
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu PDF tem",
            str(Path.cwd() / "labels_72x22.pdf"),
            "PDF Files (*.pdf)",
        )
        if not out_path:
            return

        rows_to_print: list[LabelRow] = []
        for st in self._rows:
            if st.print_qty <= 0:
                continue
            # mỗi quantity = 1 tem (1 page)
            for _ in range(st.print_qty):
                rows_to_print.append(LabelRow(name=st.item.name, sku=st.item.sku))

        if not rows_to_print:
            QMessageBox.information(self, "Không có tem", "Tất cả dòng đều có số lượng in = 0.")
            return

        try:
            generate_labels_pdf(
                rows_to_print,
                out_path,
                page_w_mm=72.0,
                page_h_mm=22.0,
                two_up=(self.two_up.value() == 1),
            )
            self._last_pdf = Path(out_path)
            self.open_btn.setEnabled(True)
            QMessageBox.information(
                self,
                "Xuất PDF xong",
                f"Đã tạo: {out_path}\nSố tem: {len(rows_to_print)}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Lỗi xuất PDF", str(e))

    def on_open_pdf(self) -> None:
        if not self._last_pdf or not self._last_pdf.exists():
            return
        os.startfile(str(self._last_pdf))  # type: ignore[attr-defined]


def run_app() -> None:
    app = QApplication([])
    # App icon (window/taskbar). In exe build, icon is also embedded by PyInstaller.
    icon_path = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        w = MainWindow()
    except Exception as e:
        QMessageBox.critical(None, "Không khởi động được", str(e))
        return
    w.show()
    app.exec()

