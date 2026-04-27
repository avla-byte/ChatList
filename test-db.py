#!/usr/bin/env python3
"""
Тестовый просмотр SQLite: список таблиц, пагинация данных, CRUD (добавить / изменить / удалить).
Запуск: python test-db.py [путь_к_файлу.db]
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE_DEFAULT = 50


def _safe_table_name(name: str, allowed: set[str]) -> Optional[str]:
    if name in allowed:
        return name
    return None


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


class RowEditDialog(QDialog):
    """Диалог ввода значений колонок (для INSERT и UPDATE)."""

    def __init__(
        self,
        parent: QWidget,
        columns: list[tuple[str, int]],
        initial: Optional[dict[str, Any]] = None,
        title: str = "Строка",
        read_only_cols: Optional[set[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._columns = columns
        self._edits: dict[str, QLineEdit] = {}
        ro = read_only_cols or set()
        form = QFormLayout(self)
        initial = initial or {}
        for col_name, _notnull in columns:
            le = QLineEdit()
            if col_name in initial and initial[col_name] is not None:
                le.setText(str(initial[col_name]))
            if col_name in ro:
                le.setReadOnly(True)
            self._edits[col_name] = le
            form.addRow(col_name + ("" if col_name not in ro else " (только чтение)"), le)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {name: w.text() for name, w in self._edits.items()}


class TableBrowser(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._conn: Optional[sqlite3.Connection] = None
        self._table: str = ""
        self._allowed_tables: set[str] = set()
        self._page = 0
        self._page_size = PAGE_SIZE_DEFAULT
        self._total_rows = 0
        self._columns_meta: list[tuple[str, int]] = []

        self._title = QLabel("Таблица: —")
        self._table_widget = QTableWidget()
        self._table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table_widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self._btn_prev = QPushButton("← Назад")
        self._btn_next = QPushButton("Вперёд →")
        self._btn_refresh = QPushButton("Обновить")
        self._btn_add = QPushButton("Добавить")
        self._btn_edit = QPushButton("Изменить")
        self._btn_delete = QPushButton("Удалить")
        self._spin_page_size = QSpinBox()
        self._spin_page_size.setRange(5, 500)
        self._spin_page_size.setValue(PAGE_SIZE_DEFAULT)
        self._label_pages = QLabel()

        for b in (
            self._btn_prev,
            self._btn_next,
            self._btn_refresh,
            self._btn_add,
            self._btn_edit,
            self._btn_delete,
        ):
            b.clicked.connect(self._on_button)  # type: ignore[arg-type]

        self._spin_page_size.valueChanged.connect(self._on_page_size_changed)

        nav = QHBoxLayout()
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._label_pages)
        nav.addWidget(self._btn_next)
        nav.addStretch()
        nav.addWidget(QLabel("Строк на странице:"))
        nav.addWidget(self._spin_page_size)

        crud = QHBoxLayout()
        crud.addWidget(self._btn_refresh)
        crud.addWidget(self._btn_add)
        crud.addWidget(self._btn_edit)
        crud.addWidget(self._btn_delete)
        crud.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._table_widget)
        layout.addLayout(nav)
        layout.addLayout(crud)

    def _on_button(self) -> None:
        sender = self.sender()
        if sender is self._btn_prev:
            self._page = max(0, self._page - 1)
            self._load_page()
        elif sender is self._btn_next:
            max_page = max(0, (self._total_rows - 1) // self._page_size)
            self._page = min(max_page, self._page + 1)
            self._load_page()
        elif sender is self._btn_refresh:
            self._load_page()
        elif sender is self._btn_add:
            self._do_insert()
        elif sender is self._btn_edit:
            self._do_update()
        elif sender is self._btn_delete:
            self._do_delete()

    def _on_page_size_changed(self, value: int) -> None:
        self._page_size = int(value)
        self._page = 0
        self._load_page()

    def set_context(
        self,
        conn: sqlite3.Connection,
        table: str,
        allowed_tables: set[str],
    ) -> None:
        self._conn = conn
        self._table = _safe_table_name(table, allowed_tables) or ""
        self._allowed_tables = allowed_tables
        self._page = 0
        if not self._table:
            self._title.setText("Таблица: —")
            self._table_widget.clear()
            self._table_widget.setRowCount(0)
            self._table_widget.setColumnCount(0)
            self._label_pages.setText("")
            return
        self._title.setText(f"Таблица: {self._table}")
        self._load_column_meta()
        self._load_page()

    def _load_column_meta(self) -> None:
        assert self._conn is not None
        assert self._table
        tq = _quote_ident(self._table)
        try:
            cur = self._conn.execute(f"PRAGMA table_info({tq})")
            rows = cur.fetchall()
        except sqlite3.Error as e:
            logger.exception("PRAGMA table_info")
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать схему таблицы:\n{e}")
            self._columns_meta = []
            return
        # cid, name, type, notnull, dflt_value, pk
        self._columns_meta = [(str(r[1]), int(r[3] or 0)) for r in rows]

    def _count_rows(self) -> int:
        assert self._conn is not None
        tq = _quote_ident(self._table)
        try:
            cur = self._conn.execute(f"SELECT COUNT(*) FROM {tq}")
            return int(cur.fetchone()[0])
        except sqlite3.Error as e:
            logger.exception("COUNT")
            QMessageBox.critical(self, "Ошибка", f"Не удалось посчитать строки:\n{e}")
            return 0

    def _load_page(self) -> None:
        if not self._conn or not self._table:
            return
        self._total_rows = self._count_rows()
        offset = self._page * self._page_size
        tq = _quote_ident(self._table)
        sql = f"SELECT rowid AS __rowid__, * FROM {tq} LIMIT ? OFFSET ?"
        try:
            cur = self._conn.execute(sql, (self._page_size, offset))
            fetched = cur.fetchall()
            colnames = [d[0] for d in cur.description] if cur.description else []
        except sqlite3.Error as e:
            logger.exception("SELECT page")
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось загрузить страницу (возможно, WITHOUT ROWID):\n{e}",
            )
            self._table_widget.clear()
            return

        self._table_widget.clear()
        self._table_widget.setColumnCount(len(colnames))
        self._table_widget.setHorizontalHeaderLabels(colnames)
        self._table_widget.setRowCount(len(fetched))
        for r, row in enumerate(fetched):
            for c, val in enumerate(row):
                item = QTableWidgetItem("" if val is None else str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row[0])
                self._table_widget.setItem(r, c, item)

        max_page = max(0, (self._total_rows - 1) // self._page_size) if self._total_rows else 0
        self._label_pages.setText(
            f"Стр. {self._page + 1} из {max_page + 1} · всего строк: {self._total_rows}",
        )
        self._btn_prev.setEnabled(self._page > 0)
        self._btn_next.setEnabled(self._page < max_page)

    def clear_view(self) -> None:
        self._conn = None
        self._table = ""
        self._allowed_tables = set()
        self._title.setText("Таблица: —")
        self._table_widget.clear()
        self._table_widget.setRowCount(0)
        self._table_widget.setColumnCount(0)
        self._label_pages.setText("")
        self._columns_meta = []

    def _selected_rowid(self) -> Optional[int]:
        items = self._table_widget.selectedItems()
        if not items:
            return None
        row = items[0].row()
        it = self._table_widget.item(row, 0)
        if it is None:
            return None
        rid = it.data(Qt.ItemDataRole.UserRole)
        return int(rid) if rid is not None else None

    def _row_dict(self, row: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for c in range(self._table_widget.columnCount()):
            h = self._table_widget.horizontalHeaderItem(c)
            name = h.text() if h else str(c)
            it = self._table_widget.item(row, c)
            out[name] = it.text() if it else ""
        return out

    def _do_insert(self) -> None:
        if not self._conn or not self._table or not self._columns_meta:
            return
        cols = [c for c, _ in self._columns_meta if c != "__rowid__"]
        if not cols:
            QMessageBox.warning(self, "Внимание", "Нет колонок для вставки.")
            return
        meta_for_dialog = [(c, nn) for c, nn in self._columns_meta if c != "__rowid__"]
        dlg = RowEditDialog(self, meta_for_dialog, title="Новая строка")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        raw = dlg.values()
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(_quote_ident(c) for c in cols)
        vals = [raw.get(c, "") for c in cols]
        tq = _quote_ident(self._table)
        try:
            self._conn.execute(f"INSERT INTO {tq} ({col_sql}) VALUES ({placeholders})", vals)
            self._conn.commit()
            logger.info("INSERT в %s", self._table)
        except sqlite3.Error as e:
            logger.exception("INSERT")
            self._conn.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось добавить строку:\n{e}")
            return
        self._page = max(0, (self._count_rows() - 1) // self._page_size)
        self._load_page()

    def _do_update(self) -> None:
        if not self._conn or not self._table:
            return
        row = self._table_widget.currentRow()
        if row < 0:
            QMessageBox.information(self, "Изменить", "Выберите строку.")
            return
        rid = self._selected_rowid()
        if rid is None:
            QMessageBox.warning(self, "Изменить", "Не удалось определить rowid.")
            return
        data = self._row_dict(row)
        data.pop("__rowid__", None)
        meta_for_dialog = [(c, nn) for c, nn in self._columns_meta if c != "__rowid__"]
        dlg = RowEditDialog(
            self,
            meta_for_dialog,
            initial=data,
            title="Изменить строку",
            read_only_cols=set(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        raw = dlg.values()
        set_parts = [_quote_ident(c) + " = ?" for c, _ in meta_for_dialog]
        vals = [raw.get(c, "") for c, _ in meta_for_dialog]
        vals.append(rid)
        tq = _quote_ident(self._table)
        sql = f"UPDATE {tq} SET {', '.join(set_parts)} WHERE rowid = ?"
        try:
            self._conn.execute(sql, vals)
            self._conn.commit()
            logger.info("UPDATE rowid=%s в %s", rid, self._table)
        except sqlite3.Error as e:
            logger.exception("UPDATE")
            self._conn.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return
        self._load_page()

    def _do_delete(self) -> None:
        if not self._conn or not self._table:
            return
        rid = self._selected_rowid()
        if rid is None:
            QMessageBox.information(self, "Удалить", "Выберите строку.")
            return
        if (
            QMessageBox.question(
                self,
                "Удалить",
                f"Удалить строку rowid={rid}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        tq = _quote_ident(self._table)
        try:
            self._conn.execute(f"DELETE FROM {tq} WHERE rowid = ?", (rid,))
            self._conn.commit()
            logger.info("DELETE rowid=%s из %s", rid, self._table)
        except sqlite3.Error as e:
            logger.exception("DELETE")
            self._conn.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n{e}")
            return
        self._load_page()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Тест SQLite — таблицы и CRUD")
        self.resize(1000, 600)
        self._conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._allowed_tables: set[str] = set()

        self._path_label = QLabel("Файл БД: не выбран")
        self._btn_open_file = QPushButton("Выбрать файл…")
        self._btn_open_file.clicked.connect(self._pick_file)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._open_selected_table)
        self._btn_open_table = QPushButton("Открыть")
        self._btn_open_table.clicked.connect(self._open_selected_table)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self._path_label)
        lv.addWidget(self._btn_open_file)
        lv.addWidget(QLabel("Таблицы:"))
        lv.addWidget(self._list)
        lv.addWidget(self._btn_open_table)

        self._browser = TableBrowser(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self._browser)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def _close_db(self) -> None:
        self._browser.clear_view()
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                logger.exception("close connection")
            self._conn = None
        self._allowed_tables.clear()
        self._list.clear()
        self._db_path = None

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "SQLite файл",
            str(Path.home()),
            "SQLite (*.db *.sqlite *.sqlite3);;Все файлы (*.*)",
        )
        if not path:
            return
        self._open_database(Path(path))

    def _open_database(self, path: Path) -> None:
        self._close_db()
        path = path.expanduser().resolve()
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.exception("open db")
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n{e}")
            return
        self._conn = conn
        self._db_path = path
        self._path_label.setText(f"Файл БД: {path}")
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name",
            )
            names = [str(r[0]) for r in cur.fetchall()]
        except sqlite3.Error as e:
            logger.exception("list tables")
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать список таблиц:\n{e}")
            conn.close()
            self._conn = None
            return
        self._allowed_tables = set(names)
        self._list.clear()
        for n in names:
            self._list.addItem(n)
        logger.info("Открыта БД %s, таблиц: %d", path, len(names))

    def _open_selected_table(self, *_args: Any) -> None:
        if self._conn is None:
            QMessageBox.information(self, "Открыть", "Сначала выберите файл SQLite.")
            return
        item = self._list.currentItem()
        if item is None:
            QMessageBox.information(self, "Открыть", "Выберите таблицу в списке.")
            return
        name = item.text()
        if name not in self._allowed_tables:
            QMessageBox.warning(self, "Открыть", "Некорректное имя таблицы.")
            return
        self._browser.set_context(self._conn, name, self._allowed_tables)

    def closeEvent(self, event: Any) -> None:
        self._close_db()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.is_file():
            win._open_database(p)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
