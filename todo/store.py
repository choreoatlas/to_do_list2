from __future__ import annotations

import sqlite3
from pathlib import Path

from .model import Todo, normalize_completed, normalize_title


class TodoStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1))
                )
                """
            )

    @staticmethod
    def _todo(row: sqlite3.Row | None) -> Todo | None:
        if row is None:
            return None
        return Todo(id=int(row["id"]), title=str(row["title"]), completed=bool(row["completed"]))

    def create(self, title: object) -> Todo:
        normalized = normalize_title(title)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO todos (title, completed) VALUES (?, 0)",
                (normalized,),
            )
            row = connection.execute("SELECT * FROM todos WHERE id = ?", (cursor.lastrowid,)).fetchone()
        todo = self._todo(row)
        assert todo is not None
        return todo

    def list(self) -> list[Todo]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM todos ORDER BY id ASC").fetchall()
        return [self._todo(row) for row in rows if row is not None]

    def get(self, todo_id: int) -> Todo | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return self._todo(row)

    def update(self, todo_id: int, *, title: object | None = None, completed: object | None = None) -> Todo | None:
        current = self.get(todo_id)
        if current is None:
            return None

        next_title = current.title if title is None else normalize_title(title)
        next_completed = current.completed if completed is None else normalize_completed(completed)

        with self._connect() as connection:
            connection.execute(
                "UPDATE todos SET title = ?, completed = ? WHERE id = ?",
                (next_title, int(next_completed), todo_id),
            )
            row = connection.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return self._todo(row)

    def delete(self, todo_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        return cursor.rowcount == 1
