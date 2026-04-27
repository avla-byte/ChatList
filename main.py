"""
ChatList: GUI, точка входа. Настройка путей, цикл PyQt, привязка сессии.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import dotenv

import db
import export_data
import models
from session_state import ResultRow, ResultSession

from PyQt6.QtCore import QThread, Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)
_FILE_LOG_STARTED = False


def _setup_file_log() -> None:
    global _FILE_LOG_STARTED
    if _FILE_LOG_STARTED:
        return
    _FILE_LOG_STARTED = True
    p = Path(__file__).resolve().parent / "chatlist.log"
    h = RotatingFileHandler(
        p,
        maxBytes=2_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    h.setLevel(logging.INFO)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
    )
    logging.getLogger().addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_dotenv = Path(__file__).resolve().parent / ".env"
if _dotenv.is_file():
    dotenv.load_dotenv(_dotenv)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger.info("load_dotenv: %s (exists=%s)", _dotenv, _dotenv.is_file())

DEFAULT_DB_KEY = "db_path"


def default_db_path() -> Path:
    return Path(__file__).resolve().parent / "chatlist.sqlite"


def get_db_path() -> Path:
    p = default_db_path()
    try:
        c = db.get_connection(p)
        db.init_db(c)
        custom = db.get_setting(c, DEFAULT_DB_KEY)
        c.close()
        if custom and str(custom).strip():
            return Path(custom).expanduser()
    except Exception as e:
        logger.warning("get_db_path: %s", e)
    return p


class _FetchThread(QThread):
    step = pyqtSignal(int, int)
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, db_path: Path, user_prompt: str) -> None:
        super().__init__()
        self._db_path = db_path
        self._user_prompt = user_prompt

    def run(self) -> None:
        c: sqlite3.Connection | None = None
        try:
            c = db.get_connection(self._db_path)
            db.init_db(c)

            def on_prog(d: int, t: int) -> None:
                self.step.emit(d, t)

            rows = models.run_prompt_parallel(c, self._user_prompt, progress=on_prog)
            self.done.emit(rows)
        except Exception as e:
            logger.exception("FetchThread")
            self.failed.emit(str(e))
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass


def _item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


def _err_cell(text: str) -> QTableWidgetItem:
    it = _item(text)
    it.setBackground(QColor(255, 245, 238))
    return it


class _ModelDialog(QDialog):
    def __init__(self, parent: QWidget, row: db.ModelRow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новая модель" if row is None else f"Модель: {row.name}")
        self._name = QLineEdit()
        self._url = QLineEdit()
        self._api_id = QLineEdit()
        self._api_model = QLineEdit()
        self._active = QCheckBox("Активна (участвует в рассылке)")
        self._active.setChecked(True)
        if row:
            self._name.setText(row.name)
            self._url.setText(row.api_url)
            self._api_id.setText(row.api_id)
            self._api_model.setText(row.api_model)
            self._active.setChecked(row.is_active == 1)
        form = QFormLayout()
        form.addRow("Название (в таблице):", self._name)
        form.addRow("URL API (полный, …/v1/chat/completions):", self._url)
        form.addRow("Переменная ключа в .env (api_id):", self._api_id)
        form.addRow("ID модели у провайдера (api_model):", self._api_model)
        form.addRow(self._active)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _try_accept(self) -> None:
        n, u, k = self._name.text().strip(), self._url.text().strip(), self._api_id.text().strip()
        if not n or not u or not k:
            QMessageBox.warning(self, "Проверка", "Заполните название, URL и имя переменной ключа.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str, str, int]:
        return (
            self._name.text().strip(),
            self._url.text().strip(),
            self._api_id.text().strip(),
            self._api_model.text().strip(),
            1 if self._active.isChecked() else 0,
        )


class _ModelsListDialog(QDialog):
    def __init__(self, parent: QWidget, conn: sqlite3.Connection) -> None:
        super().__init__(parent)
        self.setWindowTitle("Нейросети (модели)")
        self.setMinimumSize(QSize(720, 360))
        self._conn = conn
        self._by_row_index: list[db.ModelRow] = []
        self._table = QTableWidget(0, 4)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setHorizontalHeaderLabels(
            ["Название", "URL (кратко)", "Перем. ключа", "API-модель"],
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        v = QVBoxLayout(self)
        v.addWidget(self._table)
        h = QHBoxLayout()
        h.addWidget(b_add := QPushButton("Добавить"))
        h.addWidget(b_ed := QPushButton("Изменить"))
        h.addWidget(b_del := QPushButton("Удалить"))
        h.addStretch()
        h.addWidget(b_close := QPushButton("Закрыть"))
        v.addLayout(h)
        b_add.clicked.connect(self._on_add)
        b_ed.clicked.connect(self._on_edit)
        b_del.clicked.connect(self._on_del)
        b_close.clicked.connect(self.accept)
        self._refresh()

    def _refresh(self) -> None:
        r = db.list_models(self._conn, active_only=False)
        self._by_row_index = list(r)
        self._table.setRowCount(0)
        for m in r:
            ridx = self._table.rowCount()
            self._table.insertRow(ridx)
            u = m.api_url
            u_short = u if len(u) < 64 else u[:60] + "…"
            self._table.setItem(ridx, 0, _item(m.name))
            self._table.setItem(ridx, 1, _item(u_short))
            self._table.setItem(ridx, 2, _item(m.api_id))
            self._table.setItem(ridx, 3, _item(m.api_model or "—"))

    def _row_model(self) -> db.ModelRow | None:
        cur = self._table.currentRow()
        if cur < 0 or cur >= len(self._by_row_index):
            return None
        return self._by_row_index[cur]

    def _on_add(self) -> None:
        d = _ModelDialog(self, None)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        name, u, kid, mname, act = d.values()
        try:
            db.insert_model(self._conn, name, u, kid, mname, act)
            self._refresh()
        except Exception as e:
            logger.exception("insert_model")
            QMessageBox.critical(self, "Ошибка БД", str(e))

    def _on_edit(self) -> None:
        m = self._row_model()
        if not m:
            QMessageBox.information(self, "Нейросети", "Выберите строку.")
            return
        d = _ModelDialog(self, m)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        name, u, kid, mname, act = d.values()
        try:
            db.update_model(self._conn, m.id, name, u, kid, mname, act)
            self._refresh()
        except Exception as e:
            logger.exception("update_model")
            QMessageBox.critical(self, "Ошибка БД", str(e))

    def _on_del(self) -> None:
        m = self._row_model()
        if not m:
            QMessageBox.information(self, "Нейросети", "Выберите строку.")
            return
        r = QMessageBox.question(
            self, "Удаление", f"Удалить «{m.name}» из списка моделей?"
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            db.delete_model(self._conn, m.id)
            self._refresh()
        except Exception as e:
            logger.exception("delete_model")
            QMessageBox.critical(
                self,
                "Нельзя удалить",
                f"{e}\n(Возможно, есть сохранённые результаты, ссылающиеся на эту модель.)",
            )


class _PromptDialog(QDialog):
    def __init__(self, parent: QWidget, row: db.PromptRow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый промт" if row is None else f"Промт #{row.id}")
        self.setMinimumSize(QSize(560, 420))
        self._body = QPlainTextEdit()
        self._tags = QLineEdit()
        self._tags.setPlaceholderText("Необязательно, через запятую…")
        if row:
            self._body.setPlainText(row.body)
            self._tags.setText(row.tags or "")
        form = QFormLayout()
        if row:
            form.addRow("Создано:", QLabel(row.created_at))
        form.addRow("Текст промта:", self._body)
        form.addRow("Теги:", self._tags)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _try_accept(self) -> None:
        if not self._body.toPlainText().strip():
            QMessageBox.warning(self, "Проверка", "Введите непустой текст промта.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return (self._body.toPlainText().strip(), self._tags.text().strip())


class _PromptsListDialog(QDialog):
    """Таблица библиотеки промтов с CRUD."""

    def __init__(self, parent: QWidget, conn: sqlite3.Connection) -> None:
        super().__init__(parent)
        self.setWindowTitle("Промты")
        self.setMinimumSize(QSize(800, 400))
        self._conn = conn
        self._by_row_index: list[db.PromptRow] = []
        self._table = QTableWidget(0, 4)
        self._table.setWordWrap(True)
        self._table.setHorizontalHeaderLabels(
            ["id", "Создано", "Текст (фрагмент)", "Теги"],
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        v = QVBoxLayout(self)
        v.addWidget(self._table, 1)
        row_bt = QHBoxLayout()
        row_bt.addWidget(b_add := QPushButton("Добавить"))
        row_bt.addWidget(b_ed := QPushButton("Изменить"))
        row_bt.addWidget(b_del := QPushButton("Удалить"))
        row_bt.addWidget(b_ref := QPushButton("Обновить"))
        row_bt.addStretch()
        row_bt.addWidget(b_close := QPushButton("Закрыть"))
        v.addLayout(row_bt)
        b_add.clicked.connect(self._on_add)
        b_ed.clicked.connect(self._on_edit)
        b_del.clicked.connect(self._on_del)
        b_ref.clicked.connect(self._refresh)
        b_close.clicked.connect(self.accept)
        self._table.itemDoubleClicked.connect(self._on_double)
        self._refresh()

    def _refresh(self) -> None:
        try:
            rows = db.list_prompts(self._conn)
        except Exception as e:
            logger.exception("list_prompts")
            QMessageBox.critical(self, "БД", str(e))
            rows = []
        self._by_row_index = list(rows)
        self._table.setRowCount(0)
        for p in rows:
            ridx = self._table.rowCount()
            self._table.insertRow(ridx)
            frag = (p.body[:120] + "…") if len(p.body) > 120 else p.body
            frag = frag.replace("\n", " ").strip() or "—"
            self._table.setItem(ridx, 0, _item(str(p.id)))
            self._table.setItem(ridx, 1, _item(p.created_at))
            self._table.setItem(ridx, 2, _item(frag))
            self._table.setItem(ridx, 3, _item(p.tags or "—"))

    def _row_prompt(self) -> db.PromptRow | None:
        cur = self._table.currentRow()
        if cur < 0 or cur >= len(self._by_row_index):
            return None
        return self._by_row_index[cur]

    def _on_add(self) -> None:
        d = _PromptDialog(self, None)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        body, tags = d.values()
        try:
            db.insert_prompt(self._conn, body, tags or None)
            self._refresh()
        except Exception as e:
            logger.exception("insert_prompt")
            QMessageBox.critical(self, "Ошибка БД", str(e))

    def _on_edit(self) -> None:
        p = self._row_prompt()
        if not p:
            QMessageBox.information(self, "Промты", "Выберите строку.")
            return
        d = _PromptDialog(self, p)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        body, tags = d.values()
        try:
            db.update_prompt(self._conn, p.id, body, tags or None)
            self._refresh()
        except Exception as e:
            logger.exception("update_prompt")
            QMessageBox.critical(self, "Ошибка БД", str(e))

    def _on_del(self) -> None:
        p = self._row_prompt()
        if not p:
            QMessageBox.information(self, "Промты", "Выберите строку.")
            return
        r = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить промт #{p.id} из библиотеки?\n"
            "Ссылки в сохранённых ответах будут обнулены (prompt_id).",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            db.delete_prompt(self._conn, p.id)
            self._refresh()
        except Exception as e:
            logger.exception("delete_prompt")
            QMessageBox.critical(self, "Ошибка БД", str(e))

    def _on_double(self, it: QTableWidgetItem) -> None:
        self._table.setCurrentItem(it)
        self._on_edit()


def _short_cell_text(s: str, n: int) -> str:
    t = (s or "").replace("\n", " ")
    t = t.strip() or "—"
    if len(t) <= n:
        return t
    return t[: max(1, n - 1)] + "…"


class _SavedResultsDialog(QDialog):
    """Просмотр записей из таблицы `results` (после «Сохранить выбранные ответы»)."""

    def __init__(self, parent: QWidget, conn: sqlite3.Connection) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сохранённые в БД ответы")
        self.setMinimumSize(QSize(920, 480))
        self._conn = conn
        self._rows: list[db.ResultSavedRow] = []
        v = QVBoxLayout(self)
        v.addWidget(
            QLabel(
                "Здесь — ответы, которые вы нажимали «Сохранить выбранные ответы». "
                "Промт из библиотеки (выпадающий список) — это отдельно, про сохранённый текст запроса.",
            ),
        )
        self._table = QTableWidget(0, 4)
        self._table.setWordWrap(True)
        self._table.setHorizontalHeaderLabels(
            ["Создано", "Модель", "Промт (фрагмент)", "Ответ (фрагмент)"],
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setToolTip("Двойной щелчок — открыть полный промт и ответ")
        v.addWidget(self._table, 1)
        hbt = QHBoxLayout()
        hbt.addWidget(ref := QPushButton("Обновить"))
        hbt.addStretch()
        hbt.addWidget(cl := QPushButton("Закрыть"))
        v.addLayout(hbt)
        ref.clicked.connect(self._load)
        cl.clicked.connect(self.accept)
        self._table.itemDoubleClicked.connect(self._on_double)
        self._load()

    def _load(self) -> None:
        try:
            self._rows = db.list_saved_results(self._conn, limit=2000)
        except Exception as e:
            logger.exception("list_saved_results")
            QMessageBox.critical(self, "БД", str(e))
            self._rows = []
        self._table.setRowCount(0)
        for r in self._rows:
            i = self._table.rowCount()
            self._table.insertRow(i)
            self._table.setItem(i, 0, _item(r.created_at))
            self._table.setItem(i, 1, _item(r.model_name))
            self._table.setItem(i, 2, _item(_short_cell_text(r.prompt_snapshot, 100)))
            self._table.setItem(i, 3, _item(_short_cell_text(r.response_text, 150)))

    def _on_double(self, it: QTableWidgetItem) -> None:
        ridx = it.row()
        if ridx < 0 or ridx >= len(self._rows):
            return
        r = self._rows[ridx]
        d2 = QDialog(self)
        d2.setWindowTitle(f"Сохранённый ответ #{r.id} — {r.model_name}")
        d2.setMinimumSize(QSize(700, 560))
        lay = QVBoxLayout(d2)
        lay.addWidget(QLabel("Промт (как в момент сохранения):"))
        e1 = QPlainTextEdit()
        e1.setReadOnly(True)
        e1.setPlainText(r.prompt_snapshot)
        lay.addWidget(e1, 1)
        lay.addWidget(QLabel("Сохранённый ответ:"))
        e2 = QPlainTextEdit()
        e2.setReadOnly(True)
        e2.setPlainText(r.response_text)
        lay.addWidget(e2, 2)
        extra = f"id в БД: {r.id}   ·   создано: {r.created_at}   ·   model_id: {r.model_id}"
        if r.prompt_id is not None:
            extra += f"   ·   связь с библиотекой промтов: prompt_id={r.prompt_id}"
        lay.addWidget(QLabel(extra))
        bbb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bbb.rejected.connect(d2.close)
        lay.addWidget(bbb)
        d2.exec()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ChatList")
        self.setMinimumSize(QSize(900, 560))
        self._db_path = get_db_path()
        self._conn = db.get_connection(self._db_path)
        db.init_db(self._conn)
        self._session = ResultSession()
        self._order_model_ids: list[int] = []
        self._fetch: Optional[_FetchThread] = None
        self._source_prompt_id: Optional[int] = None
        w = QWidget(self)
        self.setCentralWidget(w)
        v = QVBoxLayout(w)
        h_pr = QHBoxLayout()
        h_pr.addWidget(QLabel("Промт из библиотеки:"), 0)
        self._cb_prompts = QComboBox()
        self._cb_prompts.setMinimumWidth(360)
        h_pr.addWidget(self._cb_prompts, 1)
        v.addLayout(h_pr)
        self._ed_prompt = QPlainTextEdit()
        f = self._ed_prompt.font()
        f.setPointSize(max(10, f.pointSize()))
        self._ed_prompt.setFont(f)
        self._ed_prompt.setPlaceholderText("Введите текст запроса…")
        v.addWidget(self._ed_prompt, 1)
        row_btn = QHBoxLayout()
        self._btn_send = QPushButton("Отправить")
        self._btn_save = QPushButton("Сохранить выбранные ответы")
        self._btn_lib = QPushButton("Сохранить промт в библиотеку")
        self._btn_saved_db = QPushButton("Сохранённые ответы…")
        self._btn_prompts = QPushButton("Промты…")
        self._btn_models = QPushButton("Нейросети…")
        row_btn.addWidget(self._btn_send)
        row_btn.addWidget(self._btn_save)
        row_btn.addWidget(self._btn_lib)
        row_btn.addWidget(self._btn_saved_db)
        row_btn.addWidget(self._btn_prompts)
        self._btn_exp_md = QPushButton("В Markdown…")
        self._btn_exp_json = QPushButton("В JSON…")
        row_btn.addWidget(self._btn_exp_md)
        row_btn.addWidget(self._btn_exp_json)
        row_btn.addWidget(self._btn_models)
        row_btn.addStretch()
        v.addLayout(row_btn)
        v.addWidget(QLabel("Результаты (временно, в памяти до сохранения):"))
        h_f = QHBoxLayout()
        h_f.addWidget(QLabel("Фильтр:"), 0)
        self._ed_filter = QLineEdit()
        self._ed_filter.setPlaceholderText("Подстрока в модели или в ответе…")
        h_f.addWidget(self._ed_filter, 1)
        h_f.addWidget(QLabel("Сортировка:"), 0)
        self._cb_sort = QComboBox()
        self._cb_sort.addItems(
            ("Исходный порядок", "Модель (А→Я)", "Модель (Я→А)"),
        )
        h_f.addWidget(self._cb_sort)
        v.addLayout(h_f)
        self._table = QTableWidget(0, 3)
        self._table.setWordWrap(True)
        self._table.setHorizontalHeaderLabels(["Модель", "Ответ / ошибка", "Сохранить?"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setToolTip(
            "Двойной щелчок по строке — открыть полный ответ или текст ошибки в отдельном окне.",
        )
        v.addWidget(self._table, 2)
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status = sb
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.itemDoubleClicked.connect(self._on_result_double_click)
        self._cb_prompts.currentIndexChanged.connect(self._on_prompt_pick)
        self._btn_send.clicked.connect(self._on_send)
        self._btn_save.clicked.connect(self._on_save_results)
        self._btn_lib.clicked.connect(self._on_save_prompt)
        self._btn_saved_db.clicked.connect(self._on_open_saved_results)
        self._btn_prompts.clicked.connect(self._on_open_prompts)
        self._btn_models.clicked.connect(self._on_open_models)
        self._ed_filter.textChanged.connect(self._apply_result_filter)
        self._cb_sort.currentIndexChanged.connect(self._apply_result_sort)
        self._btn_exp_md.clicked.connect(self._on_export_md)
        self._btn_exp_json.clicked.connect(self._on_export_json)
        self._refresh_prompts_combo()
        self._rebuild_table()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Дожидаемся воркера, чтобы не закрыть БД в середине запроса
        if self._fetch and self._fetch.isRunning():
            self._status.showMessage("Ожидание завершения запроса…", 0)
            self._fetch.wait(60_000)
        try:
            self._conn.close()
        except Exception:
            pass
        event.accept()

    def _refresh_prompts_combo(self) -> None:
        self._cb_prompts.blockSignals(True)
        self._cb_prompts.clear()
        self._cb_prompts.addItem("— вручную / новый —", None)
        try:
            for p in db.list_prompts(self._conn):
                label = (p.body[:50] + "…") if len(p.body) > 50 else p.body
                self._cb_prompts.addItem(f"{p.id} · {label}", p.id)
        except Exception as e:
            logger.exception("list_prompts")
            self._status.showMessage(f"Ошибка библиотеки промтов: {e}", 10000)
        self._cb_prompts.blockSignals(False)

    def _on_prompt_pick(self) -> None:
        pid = self._cb_prompts.currentData()
        if pid is None:
            self._source_prompt_id = None
            return
        if not isinstance(pid, int):
            return
        r = db.get_prompt(self._conn, pid)
        self._source_prompt_id = int(pid) if r else None
        if r:
            self._ed_prompt.setPlainText(r.body)

    def _rebuild_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for i, r in enumerate(self._session.rows):
            self._table.insertRow(i)
            self._table.setItem(i, 0, _item(r.model_name))
            if r.is_ok:
                c1 = _item((r.response_text or "") or "(пусто)")
            else:
                c1 = _err_cell("Ошибка: " + (r.error or ""))
            self._table.setItem(i, 1, c1)
            c2 = QTableWidgetItem()
            c2.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | (c2.flags() & ~Qt.ItemFlag.ItemIsEditable)
            )
            c2.setCheckState(
                Qt.CheckState.Checked if r.selected else Qt.CheckState.Unchecked
            )
            c2.setData(Qt.ItemDataRole.UserRole, i)
            self._table.setItem(i, 2, c2)
        self._table.blockSignals(False)
        self._apply_result_filter()

    def _on_result_double_click(self, it: QTableWidgetItem) -> None:
        row = it.row()
        if row < 0 or row >= len(self._session.rows):
            return
        self._show_result_detail(row)

    def _show_result_detail(self, row: int) -> None:
        r = self._session.rows[row]
        d = QDialog(self)
        if r.is_ok:
            d.setWindowTitle(f"Полный ответ — {r.model_name}")
            body = (r.response_text or "") or "(пусто)"
        else:
            d.setWindowTitle(f"Ошибка — {r.model_name}")
            body = (r.error or "")
        d.setMinimumSize(QSize(640, 480))
        lay = QVBoxLayout(d)
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setPlainText(body)
        f = ed.font()
        f.setPointSize(max(10, f.pointSize()))
        ed.setFont(f)
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(d.close)
        close_btn = bb.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("Закрыть")
        lay.addWidget(bb)
        d.exec()

    def _apply_result_filter(self) -> None:
        q = (self._ed_filter.text() or "").strip().lower()
        for i in range(self._table.rowCount()):
            if not q:
                self._table.setRowHidden(i, False)
                continue
            c0 = self._table.item(i, 0)
            c1 = self._table.item(i, 1)
            t0 = (c0.text() or "").lower() if c0 else ""
            t1 = (c1.text() or "").lower() if c1 else ""
            self._table.setRowHidden(i, (q not in t0) and (q not in t1))

    def _apply_result_sort(self, _index: int = 0) -> None:
        if not self._session.rows:
            return
        m = self._cb_sort.currentIndex()
        rows = self._session.rows
        if m == 0:
            if self._order_model_ids:
                pos = {mid: j for j, mid in enumerate(self._order_model_ids)}
                self._session.rows = sorted(
                    rows,
                    key=lambda r: pos.get(r.model_id, 1_000_000),
                )
        elif m == 1:
            self._session.rows = sorted(
                rows,
                key=lambda r: (r.model_name or "").casefold(),
            )
        elif m == 2:
            self._session.rows = sorted(
                rows,
                key=lambda r: (r.model_name or "").casefold(),
                reverse=True,
            )
        self._rebuild_table()

    def _rows_for_export(self) -> list[ResultRow] | None:
        if not self._session.rows:
            QMessageBox.information(self, "Экспорт", "Нет строк в таблице результатов.")
            return None
        sel = [r for r in self._session.rows if r.selected]
        if sel:
            return sel
        r = QMessageBox.question(
            self,
            "Экспорт",
            "Ни одна строка не отмечена. Экспортировать все ответы в таблице?",
        )
        if r == QMessageBox.StandardButton.Yes:
            return list(self._session.rows)
        return None

    def _on_export_md(self) -> None:
        rows = self._rows_for_export()
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить Markdown",
            str(Path.home() / "ChatList.md"),
            "Markdown (*.md);;Все (*.*)",
        )
        if not path:
            return
        t = (self._session.prompt_text or self._ed_prompt.toPlainText() or "").strip()
        try:
            out = export_data.export_markdown(t, rows)
            Path(path).write_text(out, encoding="utf-8")
        except OSError as e:
            logger.exception("export md")
            QMessageBox.critical(self, "Файл", str(e))
            return
        self._status.showMessage("Markdown сохранён", 5000)

    def _on_export_json(self) -> None:
        rows = self._rows_for_export()
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить JSON",
            str(Path.home() / "ChatList.json"),
            "JSON (*.json);;Все (*.*)",
        )
        if not path:
            return
        t = (self._session.prompt_text or self._ed_prompt.toPlainText() or "").strip()
        try:
            out = export_data.export_json(t, rows)
            Path(path).write_text(out, encoding="utf-8")
        except OSError as e:
            logger.exception("export json")
            QMessageBox.critical(self, "Файл", str(e))
            return
        self._status.showMessage("JSON сохранён", 5000)

    def _on_table_item_changed(self, it: QTableWidgetItem) -> None:
        if it.column() != 2 or not self._session.rows:
            return
        ridx = it.data(Qt.ItemDataRole.UserRole)
        if ridx is None or not isinstance(ridx, int):
            return
        if 0 <= ridx < len(self._session.rows):
            self._session.rows[ridx].selected = it.checkState() == Qt.CheckState.Checked

    def _on_send(self) -> None:
        t = self._ed_prompt.toPlainText().strip()
        if not t:
            QMessageBox.information(self, "Запрос", "Введите непустой текст промта.")
            return
        if self._fetch and self._fetch.isRunning():
            return
        self._ed_filter.setText("")
        self._session.clear()
        self._order_model_ids = []
        self._rebuild_table()
        if self._cb_prompts.currentData() is None:
            self._source_prompt_id = None
        self._status.showMessage("Отправка…")
        self._btn_send.setEnabled(False)
        th = _FetchThread(self._db_path, t)
        self._fetch = th
        th.step.connect(self._on_fetch_step)
        th.done.connect(self._on_fetch_done)
        th.failed.connect(self._on_fetch_fail)
        th.finished.connect(self._on_fetch_thread_finished)
        th.start()

    def _on_fetch_thread_finished(self) -> None:
        self._btn_send.setEnabled(True)
        if self._fetch and self._fetch is not None:
            self._fetch = None
        self._status.showMessage("Готово", 5000)

    def _on_fetch_step(self, done: int, total: int) -> None:
        self._status.showMessage(f"Получено {done} из {total}…")

    def _on_fetch_done(self, rows: list[ResultRow]) -> None:
        t = self._ed_prompt.toPlainText().strip()
        sp = self._source_prompt_id
        self._session.replace(t, sp, rows)
        self._order_model_ids = [r.model_id for r in self._session.rows]
        self._cb_sort.blockSignals(True)
        self._cb_sort.setCurrentIndex(0)
        self._cb_sort.blockSignals(False)
        self._rebuild_table()
        self._status.showMessage("Ответы получены", 8000)

    def _on_fetch_fail(self, msg: str) -> None:
        QMessageBox.critical(self, "Ошибка запроса", msg)
        self._status.showMessage("Ошибка", 8000)

    def _on_save_results(self) -> None:
        t = (self._session.prompt_text or self._ed_prompt.toPlainText()).strip()
        to_save: list[db.ResultInsert] = []
        for r in self._session.rows:
            if r.selected and r.is_ok and (r.response_text or "").strip():
                to_save.append(
                    db.ResultInsert(
                        model_id=r.model_id,
                        prompt_id=self._session.source_prompt_id
                        if self._session.source_prompt_id is not None
                        else None,
                        prompt_snapshot=t,
                        response_text=r.response_text,
                    )
                )
        if not to_save:
            QMessageBox.information(
                self, "Сохранение", "Нет отмеченных строк с успешными ответами."
            )
            return
        try:
            db.insert_results(self._conn, to_save)
            self._session.clear()
            self._rebuild_table()
            self._status.showMessage(f"Сохранено записей: {len(to_save)}", 8000)
        except Exception as e:
            logger.exception("insert_results")
            QMessageBox.critical(self, "БД", str(e))

    def _on_save_prompt(self) -> None:
        t = self._ed_prompt.toPlainText().strip()
        if not t:
            QMessageBox.information(self, "Библиотека", "Пустой текст сохранить нельзя.")
            return
        try:
            new_id = db.insert_prompt(self._conn, t, None)
            self._status.showMessage(f"Промт #{new_id} добавлен в библиотеку", 8000)
            self._refresh_prompts_combo()
        except Exception as e:
            logger.exception("insert_prompt")
            QMessageBox.critical(self, "БД", str(e))

    def _on_open_saved_results(self) -> None:
        d = _SavedResultsDialog(self, self._conn)
        d.exec()

    def _on_open_prompts(self) -> None:
        saved_sid = self._source_prompt_id
        d = _PromptsListDialog(self, self._conn)
        d.exec()
        self._refresh_prompts_combo()
        if saved_sid is not None and db.get_prompt(self._conn, saved_sid) is not None:
            for i in range(self._cb_prompts.count()):
                if self._cb_prompts.itemData(i) == saved_sid:
                    self._cb_prompts.setCurrentIndex(i)
                    break
            self._source_prompt_id = saved_sid
        else:
            self._cb_prompts.setCurrentIndex(0)
            self._source_prompt_id = None
        self._status.showMessage("Библиотека промтов обновлена", 3000)

    def _on_open_models(self) -> None:
        d = _ModelsListDialog(self, self._conn)
        d.exec()
        if self._fetch and self._fetch.isRunning():
            return
        self._status.showMessage("Список моделей обновлён", 3000)


def main() -> int:
    try:
        _setup_file_log()
        app = QApplication(sys.argv)
        w = MainWindow()
        w.show()
        return int(app.exec())
    except Exception:
        logger.exception("main")
        return 1


if __name__ == "__main__":
    sys.exit(main())
