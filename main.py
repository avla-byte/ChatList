"""
Минимальное GUI-приложение на PyQt6.
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_PHRASE = "Минимальная программа на Python"


class MainWindow(QWidget):
    """Простое окно: кнопка выводит заданную фразу."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ChatList — минимальный PyQt")
        self.setMinimumWidth(320)

        self._output = QLabel("", self)
        self._output.setWordWrap(True)

        self._button = QPushButton("Нажми меня", self)
        self._button.clicked.connect(self._on_click)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Минимальный интерфейс на PyQt6:", self))
        layout.addWidget(self._button)
        layout.addWidget(self._output)

        logger.info("Окно создано")

    def _on_click(self) -> None:
        self._output.setText(OUTPUT_PHRASE)
        logger.info("Нажата кнопка — выведена фраза")


def main() -> int:
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        logger.info("Приложение запущено, цикл событий")
        return app.exec()
    except Exception:
        logger.exception("Критическая ошибка при запуске GUI")
        return 1


if __name__ == "__main__":
    sys.exit(main())
