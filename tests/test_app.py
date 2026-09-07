from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from app import TodoStore, make_server


class TodoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "todos.sqlite3"
        self.store = TodoStore(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_crud_and_persistence(self) -> None:
        created = self.store.create("Write tests")
        self.assertFalse(created.completed)

        updated = self.store.update(created.id, completed=True)
        self.assertIsNotNone(updated)
        self.assertTrue(updated.completed)

        renamed = self.store.update(created.id, title="Write better tests")
        self.assertEqual("Write better tests", renamed.title)

        reopened_store = TodoStore(self.db)
        persisted = reopened_store.get(created.id)
        self.assertIsNotNone(persisted)
        self.assertEqual("Write better tests", persisted.title)
        self.assertTrue(persisted.completed)

        self.assertTrue(reopened_store.delete(created.id))
        self.assertIsNone(reopened_store.get(created.id))

    def test_invalid_title_is_rejected_without_mutation(self) -> None:
        self.store.create("Keep me")
        with self.assertRaises(ValueError):
            self.store.create("   ")
        self.assertEqual(["Keep me"], [todo.title for todo in self.store.list()])


class TodoHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server("127.0.0.1", 0, Path(self.tmp.name) / "api.sqlite3")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload)
        headers = {} if body is None else {"Content-Type": "application/json"}
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        raw = response.read()
        data = None if not raw else json.loads(raw)
        return response.status, data

    def test_browser_entry_point_is_served(self) -> None:
        self.connection.request("GET", "/")
        response = self.connection.getresponse()
        body = response.read().decode("utf-8")
        self.assertEqual(200, response.status)
        self.assertIn("Todo List", body)
        self.assertIn("/app.js", body)

    def test_user_flow(self) -> None:
        status, created = self.request("POST", "/api/todos", {"title": "Buy milk"})
        self.assertEqual(201, status)

        todo_id = created["id"]
        status, listed = self.request("GET", "/api/todos")
        self.assertEqual(200, status)
        self.assertEqual("Buy milk", listed[0]["title"])

        status, updated = self.request("PATCH", f"/api/todos/{todo_id}", {"completed": True})
        self.assertEqual(200, status)
        self.assertTrue(updated["completed"])

        status, renamed = self.request("PATCH", f"/api/todos/{todo_id}", {"title": "Buy oat milk"})
        self.assertEqual(200, status)
        self.assertEqual("Buy oat milk", renamed["title"])

        status, _ = self.request("DELETE", f"/api/todos/{todo_id}")
        self.assertEqual(204, status)

        status, listed = self.request("GET", "/api/todos")
        self.assertEqual(200, status)
        self.assertEqual([], listed)

    def test_bad_input_and_missing_todo_fail_cleanly(self) -> None:
        status, error = self.request("POST", "/api/todos", {"title": ""})
        self.assertEqual(400, status)
        self.assertIn("empty", error["error"])

        status, error = self.request("PATCH", "/api/todos/999", {"completed": True})
        self.assertEqual(404, status)
        self.assertEqual("todo not found", error["error"])


if __name__ == "__main__":
    unittest.main()
