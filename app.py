from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_DB_PATH = ROOT / "data" / "todos.sqlite3"


@dataclass(frozen=True)
class Todo:
    id: int
    title: str
    completed: bool


class TodoStore:
    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def list(self) -> list[Todo]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, title, completed FROM todos ORDER BY id DESC"
            ).fetchall()
        return [Todo(int(row["id"]), str(row["title"]), bool(row["completed"])) for row in rows]

    def get(self, todo_id: int) -> Todo | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id, title, completed FROM todos WHERE id = ?", (todo_id,)
            ).fetchone()
        if row is None:
            return None
        return Todo(int(row["id"]), str(row["title"]), bool(row["completed"]))

    def create(self, title: str) -> Todo:
        title = normalize_title(title)
        with self._lock, self._connect() as connection:
            cursor = connection.execute("INSERT INTO todos(title) VALUES (?)", (title,))
            todo_id = int(cursor.lastrowid)
        return self.get(todo_id)  # type: ignore[return-value]

    def update(self, todo_id: int, *, title: str | None = None, completed: bool | None = None) -> Todo | None:
        current = self.get(todo_id)
        if current is None:
            return None
        next_title = normalize_title(title) if title is not None else current.title
        next_completed = current.completed if completed is None else bool(completed)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE todos
                SET title = ?, completed = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_title, int(next_completed), todo_id),
            )
        return self.get(todo_id)

    def delete(self, todo_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            return cursor.rowcount == 1


def normalize_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("title must be a string")
    value = value.strip()
    if not value:
        raise ValueError("title must not be empty")
    if len(value) > 200:
        raise ValueError("title must be 200 characters or fewer")
    return value


class TodoHandler(BaseHTTPRequestHandler):
    store: TodoStore

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0 or length > 16_384:
            raise ValueError("request body is missing or too large")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    @staticmethod
    def _todo_payload(todo: Todo) -> dict[str, object]:
        return asdict(todo)

    def _todo_id(self) -> int | None:
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if len(parts) != 3 or parts[:2] != ["api", "todos"]:
            return None
        try:
            return int(parts[2])
        except ValueError:
            return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/todos":
            self._json(HTTPStatus.OK, [self._todo_payload(todo) for todo in self.store.list()])
            return
        todo_id = self._todo_id()
        if todo_id is not None:
            todo = self.store.get(todo_id)
            if todo is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "todo not found"})
            else:
                self._json(HTTPStatus.OK, self._todo_payload(todo))
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/todos":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            payload = self._read_json()
            todo = self.store.create(payload.get("title"))
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json(HTTPStatus.CREATED, self._todo_payload(todo))

    def do_PATCH(self) -> None:
        todo_id = self._todo_id()
        if todo_id is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            payload = self._read_json()
            unknown = set(payload) - {"title", "completed"}
            if unknown or not payload:
                raise ValueError("body must contain title and/or completed")
            if "completed" in payload and not isinstance(payload["completed"], bool):
                raise ValueError("completed must be a boolean")
            todo = self.store.update(
                todo_id,
                title=payload.get("title") if "title" in payload else None,
                completed=payload.get("completed") if "completed" in payload else None,
            )
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if todo is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "todo not found"})
            return
        self._json(HTTPStatus.OK, self._todo_payload(todo))

    def do_DELETE(self) -> None:
        todo_id = self._todo_id()
        if todo_id is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self.store.delete(todo_id):
            self._json(HTTPStatus.NOT_FOUND, {"error": "todo not found"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not candidate.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def make_server(host: str, port: int, db_path: str | os.PathLike[str]) -> ThreadingHTTPServer:
    store = TodoStore(db_path)
    handler = type("ConfiguredTodoHandler", (TodoHandler,), {"store": store})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    host = os.environ.get("TODO_HOST", "127.0.0.1")
    port = int(os.environ.get("TODO_PORT", "8000"))
    db_path = os.environ.get("TODO_DB", str(DEFAULT_DB_PATH))
    server = make_server(host, port, db_path)
    print(f"Todo List listening on http://{host}:{port}")
    print(f"SQLite database: {db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
