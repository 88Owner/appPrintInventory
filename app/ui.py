from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import default_config_path, load_config
from .label_pdf import LabelRow, generate_labels_pdf
from .sapo_client import ReceiveInventoryItem, ReceiveInventorySummary, SapoApiError, SapoClient


def _rid_from_detail_url(url: str) -> int | None:
    m = re.search(r"/receive_inventories/(\d+)\.json", url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("In tem 72x22 - Receive Inventories (pending)")
        self.resize(1100, 720)

        cfg_path = default_config_path()
        if not cfg_path.exists():
            raise FileNotFoundError(
                f"Không thấy config.json tại: {cfg_path}\n"
                "Hãy copy config.example.json -> config.json và điền token."
            )

        self._cfg = load_config(cfg_path)
        self._client = SapoClient(self._cfg)

        self._pending_summaries: list[ReceiveInventorySummary] = []
        self._detail_cache: dict[int, list[ReceiveInventoryItem]] = {}
        self._line_prefs: dict[int, list[dict[str, Any]]] = {}
        self._recv_print: dict[int, bool] = {}
        self._last_pdf: Path | None = None
        self._loading_detail_rid: int | None = None

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
            QGroupBox {
              color: #e5e7eb;
              border: 1px solid #22304a;
              border-radius: 12px;
              margin-top: 10px;
              padding-top: 8px;
              font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QCheckBox { color: #e5e7eb; spacing: 8px; }
            QSplitter::handle { background: #22304a; height: 4px; }
            """
        )

        top = QHBoxLayout()
        layout.addLayout(top)
        top.setSpacing(10)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip(
            "Tải lại danh sách receive_inventories (pending) từ API — dữ liệu mới nhất. Phím tắt: F5"
        )
        self.refresh_btn.clicked.connect(self.refresh_pending_list)
        top.addWidget(self.refresh_btn)

        self.refresh_in_group_btn = QPushButton("Refresh")
        self.refresh_in_group_btn.setToolTip("Tải lại danh sách phiếu pending từ API (giữ nút gần bảng)")
        self.refresh_in_group_btn.clicked.connect(self.refresh_pending_list)

        top.addWidget(QLabel("Mã / ID nhanh:"))
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("Mở chi tiết 1 phiếu (ID hoặc REI…)")
        self.code_input.returnPressed.connect(self.on_fetch_one)
        top.addWidget(self.code_input, 1)

        self.fetch_btn = QPushButton("Mở phiếu")
        self.fetch_btn.clicked.connect(self.on_fetch_one)
        top.addWidget(self.fetch_btn)

        self.status_lbl = QLabel("")
        self.status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_lbl.setStyleSheet("color: #93c5fd;")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter, 1)

        grp_pending = QGroupBox("Phiếu nhập chờ xử lý")
        gl = QVBoxLayout(grp_pending)
        pending_hdr = QHBoxLayout()
        pending_hdr.addWidget(QLabel("Danh sách từ API (có thể bấm Refresh để cập nhật)"))
        pending_hdr.addStretch(1)
        pending_hdr.addWidget(self.refresh_in_group_btn)
        gl.addLayout(pending_hdr)
        self.pending_table = QTableWidget(0, 5)
        self.pending_table.setHorizontalHeaderLabels(["In phiếu", "ID", "Mã", "Trạng thái", "Ngày tạo"])
        self.pending_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pending_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.pending_table.setAlternatingRowColors(True)
        self.pending_table.verticalHeader().setVisible(False)
        self.pending_table.setColumnWidth(0, 72)
        self.pending_table.setColumnWidth(1, 80)
        self.pending_table.setColumnWidth(2, 140)
        self.pending_table.setColumnWidth(3, 120)
        self.pending_table.horizontalHeader().setStretchLastSection(True)
        self.pending_table.itemSelectionChanged.connect(self._on_pending_selection_changed)
        gl.addWidget(self.pending_table)
        splitter.addWidget(grp_pending)

        grp_detail = QGroupBox("Chi tiết hàng trong phiếu (chọn dòng + số lượng in)")
        gd = QVBoxLayout(grp_detail)
        self.detail_title = QLabel("Chọn một phiếu ở bảng trên.")
        gd.addWidget(self.detail_title)
        self.detail_table = QTableWidget(0, 5)
        self.detail_table.setHorizontalHeaderLabels(["In dòng", "SKU", "Tên", "SL phiếu", "SL in"])
        self.detail_table.setAlternatingRowColors(True)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setColumnWidth(0, 72)
        self.detail_table.setColumnWidth(1, 160)
        self.detail_table.setColumnWidth(3, 90)
        self.detail_table.setColumnWidth(4, 100)
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        gd.addWidget(self.detail_table)
        splitter.addWidget(grp_detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        bottom.setSpacing(10)

        self.export_btn = QPushButton("Xuất PDF tem (phiếu + dòng đã chọn)")
        self.export_btn.clicked.connect(self.on_export_pdf)
        bottom.addWidget(self.export_btn)

        self.open_btn = QPushButton("Mở PDF")
        self.open_btn.clicked.connect(self.on_open_pdf)
        self.open_btn.setEnabled(False)
        bottom.addWidget(self.open_btn)

        bottom.addStretch(1)

        bottom.addWidget(QLabel("2 tem/trang:"))
        self.two_up = QSpinBox()
        self.two_up.setMinimum(0)
        self.two_up.setMaximum(1)
        self.two_up.setValue(1)
        self.two_up.setToolTip("1 = một khổ 72×22mm in 2 tem (trái/phải)")
        bottom.addWidget(self.two_up)

        sc = QShortcut(QKeySequence(Qt.Key.Key_F5), self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(self.refresh_pending_list)

        self.refresh_pending_list()

    def _pending_rid_for_row(self, row: int) -> int | None:
        it = self.pending_table.item(row, 1)
        if not it:
            return None
        v = it.data(Qt.UserRole)
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    def _ensure_line_prefs(self, rid: int, items: list[ReceiveInventoryItem]) -> None:
        if rid not in self._line_prefs or len(self._line_prefs[rid]) != len(items):
            self._line_prefs[rid] = [
                {"selected": True, "print_qty": max(0, int(it.quantity))} for it in items
            ]

    def _set_recv_print(self, rid: int, on: bool) -> None:
        self._recv_print[rid] = on

    def _set_line_selected(self, rid: int, idx: int, on: bool) -> None:
        prefs = self._line_prefs.get(rid)
        if prefs and 0 <= idx < len(prefs):
            prefs[idx]["selected"] = on

    def _set_line_qty(self, rid: int, idx: int, qty: int) -> None:
        prefs = self._line_prefs.get(rid)
        if prefs and 0 <= idx < len(prefs):
            prefs[idx]["print_qty"] = max(0, int(qty))

    def _set_refresh_enabled(self, enabled: bool) -> None:
        self.refresh_btn.setEnabled(enabled)
        self.refresh_in_group_btn.setEnabled(enabled)

    def refresh_pending_list(self) -> None:
        self._set_refresh_enabled(False)
        self.status_lbl.setText("Đang tải danh sách phiếu pending…")
        QApplication.processEvents()
        try:
            rows, strategy, base_url = self._client.list_pending_receive_inventories()
            # Dữ liệu mới: bỏ cache chi tiết, tick in, và bảng chi tiết cũ
            self._detail_cache.clear()
            self._line_prefs.clear()
            self._recv_print.clear()
            self.pending_table.clearSelection()
            self.detail_table.setRowCount(0)
            self.detail_title.setText("Chọn một phiếu ở bảng trên.")

            self._pending_summaries = rows
            self._fill_pending_table()
            self.status_lbl.setText(
                f"Đã tải {len(rows)} phiếu pending."
            )
        except Exception as e:
            self.status_lbl.setText("Lỗi tải danh sách.")
            QMessageBox.critical(self, "Lỗi API danh sách", str(e))
        finally:
            self._set_refresh_enabled(True)

    def _fill_pending_table(self) -> None:
        self.pending_table.setRowCount(0)
        for summ in self._pending_summaries:
            r = self.pending_table.rowCount()
            self.pending_table.insertRow(r)

            cb = QCheckBox("In")
            cb.setChecked(self._recv_print.get(summ.id, False))
            cb.stateChanged.connect(
                lambda st, rid=summ.id: self._set_recv_print(rid, Qt.CheckState(st) == Qt.CheckState.Checked)
            )
            self.pending_table.setCellWidget(r, 0, cb)

            id_item = QTableWidgetItem(str(summ.id))
            id_item.setData(Qt.UserRole, summ.id)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            self.pending_table.setItem(r, 1, id_item)

            c2 = QTableWidgetItem(summ.code)
            c2.setFlags(c2.flags() & ~Qt.ItemIsEditable)
            self.pending_table.setItem(r, 2, c2)

            c3 = QTableWidgetItem(summ.receipt_status)
            c3.setFlags(c3.flags() & ~Qt.ItemIsEditable)
            self.pending_table.setItem(r, 3, c3)

            c4 = QTableWidgetItem(summ.created_on)
            c4.setFlags(c4.flags() & ~Qt.ItemIsEditable)
            self.pending_table.setItem(r, 4, c4)

    def _on_pending_selection_changed(self) -> None:
        sel = self.pending_table.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        rid = self._pending_rid_for_row(row)
        if rid is None:
            return
        summ = next((s for s in self._pending_summaries if s.id == rid), None)
        title = f"Phiếu #{rid} — {summ.code if summ else ''}"
        self.detail_title.setText(title)
        self._load_detail(rid)

    def _load_detail(self, rid: int) -> None:
        if self._loading_detail_rid == rid:
            return
        self._loading_detail_rid = rid
        self.detail_table.setEnabled(False)
        QApplication.processEvents()
        try:
            if rid not in self._detail_cache:
                items, strategy, url = self._client.get_receive_inventory(str(rid))
                self._detail_cache[rid] = items
                self.status_lbl.setText(f"Chi tiết phiếu {rid}: {len(items)} dòng. {url} — {strategy}")
            items = self._detail_cache[rid]
            self._ensure_line_prefs(rid, items)
            self._fill_detail_table(rid)
        except SapoApiError as e:
            QMessageBox.warning(self, "Không tải được chi tiết", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", str(e))
        finally:
            self.detail_table.setEnabled(True)
            self._loading_detail_rid = None

    def _fill_detail_table(self, rid: int) -> None:
        items = self._detail_cache.get(rid, [])
        prefs = self._line_prefs.get(rid, [])
        self.detail_table.blockSignals(True)
        self.detail_table.setRowCount(0)
        for i, it in enumerate(items):
            pref = prefs[i] if i < len(prefs) else {"selected": True, "print_qty": max(0, it.quantity)}
            r = self.detail_table.rowCount()
            self.detail_table.insertRow(r)

            cb = QCheckBox("In")
            cb.setChecked(bool(pref.get("selected", True)))
            cb.stateChanged.connect(
                lambda st, rr=rid, idx=i: self._set_line_selected(rr, idx, Qt.CheckState(st) == Qt.CheckState.Checked)
            )
            self.detail_table.setCellWidget(r, 0, cb)

            c1 = QTableWidgetItem(it.sku)
            c1.setFlags(c1.flags() & ~Qt.ItemIsEditable)
            self.detail_table.setItem(r, 1, c1)

            c2 = QTableWidgetItem(it.name)
            c2.setFlags(c2.flags() & ~Qt.ItemIsEditable)
            self.detail_table.setItem(r, 2, c2)

            c3 = QTableWidgetItem(str(it.quantity))
            c3.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            c3.setFlags(c3.flags() & ~Qt.ItemIsEditable)
            self.detail_table.setItem(r, 3, c3)

            spin = QSpinBox()
            spin.setRange(0, 100000)
            spin.setValue(int(pref.get("print_qty", max(0, it.quantity))))
            spin.valueChanged.connect(lambda v, rr=rid, idx=i: self._set_line_qty(rr, idx, v))
            self.detail_table.setCellWidget(r, 4, spin)

        self.detail_table.blockSignals(False)

    def on_fetch_one(self) -> None:
        code = self.code_input.text().strip()
        if not code:
            QMessageBox.warning(self, "Thiếu mã", "Nhập ID hoặc mã phiếu (REI…).")
            return
        self.fetch_btn.setEnabled(False)
        self.code_input.setEnabled(False)
        self.status_lbl.setText("Đang tải chi tiết phiếu…")
        QApplication.processEvents()
        try:
            items, strategy, url = self._client.get_receive_inventory(code)
            rid = _rid_from_detail_url(url)
            if rid is None:
                QMessageBox.warning(self, "Không xác định ID", f"Đã tải nhưng không parse được ID từ URL:\n{url}")
                return
            self._detail_cache[rid] = items
            self._ensure_line_prefs(rid, items)
            found = False
            for r in range(self.pending_table.rowCount()):
                if self._pending_rid_for_row(r) == rid:
                    self.pending_table.selectRow(r)
                    found = True
                    break
            summ = next((s for s in self._pending_summaries if s.id == rid), None)
            self.detail_title.setText(f"Phiếu #{rid} — {summ.code if summ else code}")
            self._fill_detail_table(rid)
            self.status_lbl.setText(f"Mở phiếu {rid}: {len(items)} dòng. {url} — {strategy}")
            if not found:
                QMessageBox.information(
                    self,
                    "Phiếu không nằm trong danh sách pending",
                    "Chi tiết vẫn hiển thị bên dưới. Phiếu này có thể không còn trạng thái pending.",
                )
        except Exception as e:
            self.status_lbl.setText("Lỗi.")
            QMessageBox.critical(self, "Lỗi gọi API", str(e))
        finally:
            self.fetch_btn.setEnabled(True)
            self.code_input.setEnabled(True)

    def on_export_pdf(self) -> None:
        selected_rids = [rid for rid, on in self._recv_print.items() if on]
        if not selected_rids:
            QMessageBox.information(self, "Chưa chọn phiếu", 'Tick cột "In phiếu" cho ít nhất một phiếu cần in tem.')
            return

        rows_to_print: list[LabelRow] = []
        errors: list[str] = []

        for rid in sorted(set(selected_rids)):
            if rid not in self._detail_cache:
                try:
                    items, _, _ = self._client.get_receive_inventory(str(rid))
                    self._detail_cache[rid] = items
                except Exception as e:
                    errors.append(f"#{rid}: {e}")
                    continue
            items = self._detail_cache[rid]
            self._ensure_line_prefs(rid, items)
            prefs = self._line_prefs[rid]
            for it, pref in zip(items, prefs):
                if not pref.get("selected", False):
                    continue
                n = max(0, int(pref.get("print_qty", 0)))
                for _ in range(n):
                    rows_to_print.append(LabelRow(name=it.name, sku=it.sku))

        if errors:
            QMessageBox.warning(self, "Một số phiếu bỏ qua", "\n".join(errors[:8]))

        if not rows_to_print:
            QMessageBox.information(
                self,
                "Không có tem",
                "Không có dòng hàng nào được chọn (In dòng) với SL in > 0 trong các phiếu đã tick.",
            )
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu PDF tem",
            str(Path.cwd() / "labels_72x22.pdf"),
            "PDF Files (*.pdf)",
        )
        if not out_path:
            return

        two_up = self.two_up.value() == 1
        try:
            generate_labels_pdf(
                rows_to_print,
                out_path,
                page_w_mm=72.0,
                page_h_mm=22.0,
                two_up=two_up,
            )
            self._last_pdf = Path(out_path)
            self.open_btn.setEnabled(True)
            n_tem = len(rows_to_print)
            n_trang = (n_tem + 1) // 2 if two_up else n_tem
            QMessageBox.information(
                self,
                "Xuất PDF xong",
                f"Đã tạo: {out_path}\nSố tem: {n_tem}\nSố trang PDF: {n_trang}"
                + (" (2 tem/trang)" if two_up else ""),
            )
        except Exception as e:
            QMessageBox.critical(self, "Lỗi xuất PDF", str(e))

    def on_open_pdf(self) -> None:
        if not self._last_pdf or not self._last_pdf.exists():
            return
        os.startfile(str(self._last_pdf))  # type: ignore[attr-defined]


def run_app() -> None:
    app = QApplication([])
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
