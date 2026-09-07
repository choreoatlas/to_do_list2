from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .store import TodoStore


class TodoHandler(BaseHTTPRequestHandler):
    store: TodoStore
    static_root: Path

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def _todo_id(self) -> int | None:
        path = urlparse(self.path).path
        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["api", "todos"]:
            try:
                return int(parts[2])
            except ValueError:
                return None
        return None

    def _serve_static(self, relative: str, content_type: str) -> None:
        path = (self.static_root / relative).resolve()
        if self.static_root.resolve() not in path.parents and path != self.static_root.resolve():
            self.send_error(404)
            return
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/todos":
            self._json(200, [asdict(todo) for todo in self.store.list()])
            return
        todo_id = self._todo_id()
        if todo_id is not None:
            todo = self.store.get(todo_id)
            if todo is None:
                self._json(404, {"error": "todo not found"})
            else:
                self._json(200, asdict(todo))
            return
        if path == "/":
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._serve_static("app.js", "text/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self._serve_static("styles.css", "text/css; charset=utf-8")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/todos":
            self.send_error(404)
            return
        try:
            payload = self._read_json()
            todo = self.store.create(payload.get("title"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(201, asdict(todo))

    def do_PATCH(self) -> None:
        todo_id = self._todo_id()
        if todo_id is None:
            self.send_error(404)
            return
        try:
            payload = self._read_json()
            if not any(key in payload for key in ("title", "completed")):
                raise ValueError("title or completed is required")
            todo = self.store.update(
                todo_id,
                title=payload["title"] if "title" in payload else None,
                completed=payload["completed"] if "completed" in payload else None,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        if todo is None:
            self._json(404, {"error": "todo not found"})
            return
        self._json(200, asdict(todo))

    def do_DELETE(self) -> None:
        todo_id = self._todo_id()
        if todo_id is None:
            self.send_error(404)
            return
        if not self.store.delete(todo_id):
            self._json(404, {"error": "todo not found"})
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def make_server(host: str, port: int, db_path: Path | str, static_root: Path | str | None = None) -> ThreadingHTTPServer:
    store = TodoStore(db_path)
    root = Path(static_root) if static_root is not None else Path(__file__).resolve().parents[1] / "static"

    class BoundHandler(TodoHandler):
        pass

    BoundHandler.store = store
    BoundHandler.static_root = root
    return ThreadingHTTPServer((host, port), BoundHandler)
