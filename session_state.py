"""
Временные результаты в памяти (не в SQLite). Очищаются при новом запросе/после «Сохранить».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class ResultRow:
    model_id: int
    model_name: str
    response_text: str
    error: Optional[str] = None
    selected: bool = False

    @property
    def is_ok(self) -> bool:
        return self.error is None


@dataclass
class ResultSession:
    """Текущая пачка ответов в RAM."""

    prompt_text: str = ""
    source_prompt_id: Optional[int] = None
    rows: List[ResultRow] = field(default_factory=list)

    def clear(self) -> None:
        self.prompt_text = ""
        self.source_prompt_id = None
        self.rows.clear()

    def replace(
        self,
        prompt: str,
        source_prompt_id: Optional[int],
        new_rows: List[ResultRow],
    ) -> None:
        self.prompt_text = (prompt or "").strip()
        self.source_prompt_id = source_prompt_id
        self.rows = list(new_rows)

    def selected_for_persist(self) -> List[ResultRow]:
        return [r for r in self.rows if r.selected and r.is_ok and r.response_text]
