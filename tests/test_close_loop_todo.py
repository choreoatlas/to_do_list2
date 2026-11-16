from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from todo.http import make_server
from todo.store import TodoStore


class StoreEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "todos.sqlite3"
        self.store = TodoStore(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_FL_capture_and_review(self) -> None:
        created = self.store.create("Write report")
        self.assertEqual("Write report", created.title)
        self.assertFalse(created.completed)
        self.assertEqual([created], self.store.list())

    def test_FL_capture_rejects_empty_title_without_mutating_collection(self) -> None:
        self.store.create("Keep me")
        with self.assertRaises(ValueError):
            self.store.create("   ")
        self.assertEqual(["Keep me"], [todo.title for todo in self.store.list()])

    def test_FL_revise(self) -> None:
        created = self.store.create("Old title")
        updated = self.store.update(created.id, title="New title")
        self.assertIsNotNone(updated)
        self.assertEqual("New title", updated.title)

    def test_FL_progress_complete_and_reopen(self) -> None:
        created = self.store.create("Toggle me")
        completed = self.store.update(created.id, completed=True)
        reopened = self.store.update(created.id, completed=False)
        self.assertTrue(completed.completed)
        self.assertFalse(reopened.completed)

    def test_FL_remove(self) -> None:
        created = self.store.create("Delete me")
        self.assertTrue(self.store.delete(created.id))
        self.assertIsNone(self.store.get(created.id))
        self.assertFalse(self.store.delete(created.id))

    def test_FL_durable_survives_store_restart(self) -> None:
        created = self.store.create("Persist me")
        self.store.update(created.id, completed=True)
        reopened = TodoStore(self.db)
        persisted = reopened.get(created.id)
        self.assertIsNotNone(persisted)
        self.assertEqual("Persist me", persisted.title)
        self.assertTrue(persisted.completed)


class HttpEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        static = Path(__file__).resolve().parents[1] / "static"
        self.db = root / "api.sqlite3"
        self.server = make_server("127.0.0.1", 0, self.db, static)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, method: str, path: str, payload: object | None = None):
        body = None if payload is None else json.dumps(payload)
        headers = {} if body is None else {"Content-Type": "application/json"}
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        data = json.loads(raw) if raw and "application/json" in content_type else raw.decode("utf-8")
        return response.status, data

    def independent_request(self, method: str, path: str, payload: object | None = None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            body = None if payload is None else json.dumps(payload)
            headers = {} if body is None else {"Content-Type": "application/json"}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type", "")
            data = json.loads(raw) if raw and "application/json" in content_type else raw.decode("utf-8")
            return response.status, data
        finally:
            connection.close()

    def test_regression_concurrent_partial_patches_preserve_all_changes(self) -> None:
        _, target = self.request("POST", "/api/todos", {"title": "Original"})
        _, unrelated = self.request("POST", "/api/todos", {"title": "Unrelated item"})
        path = f"/api/todos/{target['id']}"
        store = self.server.RequestHandlerClass.store
        original_get = store.get
        reads_complete = threading.Barrier(2)
        requests_ready = threading.Barrier(2)

        def read_before_either_update(todo_id):
            snapshot = original_get(todo_id)
            # Expose the old read/modify/write race deterministically. An atomic
            # update need not call get(), so it is free to avoid this hook.
            reads_complete.wait(timeout=3)
            return snapshot

        def update(payload):
            requests_ready.wait(timeout=3)
            return self.independent_request("PATCH", path, payload)

        with patch.object(store, "get", side_effect=read_before_either_update):
            with ThreadPoolExecutor(max_workers=2) as executor:
                renamed = executor.submit(update, {"title": "Renamed"})
                completed = executor.submit(update, {"completed": True})
                responses = [renamed.result(timeout=8), completed.result(timeout=8)]

        for status, body in responses:
            with self.subTest(proof="successful PATCH", body=body):
                self.assertEqual(200, status)
        expected = [
            {"id": target["id"], "title": "Renamed", "completed": True},
            unrelated,
        ]
        with self.subTest(proof="GET collection"):
            status, collection = self.request("GET", "/api/todos")
            self.assertEqual(200, status)
            self.assertEqual(expected, collection)
        with self.subTest(proof="reopened SQLite collection"):
            self.assertEqual(expected, [asdict(todo) for todo in TodoStore(self.db).list()])

    def test_regression_out_of_range_todo_ids_fail_cleanly_without_mutation(self) -> None:
        self.request("POST", "/api/todos", {"title": "Keep target"})
        self.request("POST", "/api/todos", {"title": "Unrelated item"})
        _, before = self.request("GET", "/api/todos")
        cases = [
            ("above SQLite maximum", str(2**63)),
            ("below SQLite minimum", str(-(2**63) - 1)),
            ("1000-digit ID", "9" * 1000),
            ("5000-digit ID", "9" * 5000),
        ]
        for case, todo_id in cases:
            for method in ("GET", "PATCH", "DELETE"):
                with self.subTest(case=case, method=method):
                    payload = {"completed": True} if method == "PATCH" else None
                    # A failed response must not prevent the independent data
                    # preservation checks from observing the same request.
                    with self.subTest(proof="controlled HTTP response"):
                        status, _ = self.independent_request(method, f"/api/todos/{todo_id}", payload)
                        self.assertEqual(404, status)
                    with self.subTest(proof="GET collection"):
                        status, after = self.request("GET", "/api/todos")
                        self.assertEqual(200, status)
                        self.assertEqual(before, after)
                    with self.subTest(proof="reopened SQLite collection"):
                        self.assertEqual(before, [asdict(todo) for todo in TodoStore(self.db).list()])

    def test_FL_capture_review_revise_progress_remove_over_http(self) -> None:
        status, created = self.request("POST", "/api/todos", {"title": "Buy milk"})
        self.assertEqual(201, status)
        todo_id = created["id"]

        status, listed = self.request("GET", "/api/todos")
        self.assertEqual(200, status)
        self.assertEqual("Buy milk", listed[0]["title"])

        status, revised = self.request("PATCH", f"/api/todos/{todo_id}", {"title": "Buy oat milk"})
        self.assertEqual(200, status)
        self.assertEqual("Buy oat milk", revised["title"])

        status, completed = self.request("PATCH", f"/api/todos/{todo_id}", {"completed": True})
        self.assertEqual(200, status)
        self.assertTrue(completed["completed"])

        status, reopened = self.request("PATCH", f"/api/todos/{todo_id}", {"completed": False})
        self.assertEqual(200, status)
        self.assertFalse(reopened["completed"])

        status, _ = self.request("DELETE", f"/api/todos/{todo_id}")
        self.assertEqual(204, status)
        status, listed = self.request("GET", "/api/todos")
        self.assertEqual([], listed)

    def test_invalid_patch_preserves_entire_persisted_collection(self) -> None:
        _, target = self.request("POST", "/api/todos", {"title": "Keep target"})
        self.request("POST", "/api/todos", {"title": "Unrelated item"})
        path = f"/api/todos/{target['id']}"
        cases = [
            {"title": None},
            {"completed": None},
            {"title": None, "completed": True},
            {"title": "Changed", "completed": None},
            {"title": 42, "completed": True},
            {"title": [], "completed": True},
            {"title": "Changed", "completed": 1},
            {"title": "Changed", "completed": "false"},
            {"title": "", "completed": True},
            {"title": "   ", "completed": True},
            {"title": "x" * 201, "completed": True},
            {},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                _, before = self.request("GET", "/api/todos")
                status, body = self.request("PATCH", path, payload)
                # Observe persistence even when the HTTP status assertion fails.
                with self.subTest(proof="response"):
                    self.assertEqual(400, status)
                    self.assertIn("error", body)
                with self.subTest(proof="GET collection"):
                    status, after = self.request("GET", "/api/todos")
                    self.assertEqual(200, status)
                    self.assertEqual(before, after)
                with self.subTest(proof="reopened SQLite collection"):
                    self.assertEqual(before, [asdict(todo) for todo in TodoStore(self.db).list()])

    def test_valid_partial_and_mixed_patch_preserve_omission_and_false(self) -> None:
        _, target = self.request("POST", "/api/todos", {"title": "Original"})
        _, unrelated = self.request("POST", "/api/todos", {"title": "Unrelated item"})
        path = f"/api/todos/{target['id']}"
        cases = [
            ({"completed": True}, "Original", True),
            ({"title": "  Revised  "}, "Revised", True),
            ({"completed": False}, "Revised", False),
            ({"title": "Both", "completed": True}, "Both", True),
            ({"title": "  Final  ", "completed": False}, "Final", False),
        ]
        for payload, title, completed in cases:
            with self.subTest(payload=payload):
                expected = {"id": target["id"], "title": title, "completed": completed}
                status, updated = self.request("PATCH", path, payload)
                self.assertEqual(200, status)
                self.assertEqual(expected, updated)
                status, collection = self.request("GET", "/api/todos")
                self.assertEqual(200, status)
                self.assertEqual([expected, unrelated], collection)
                self.assertEqual(collection, [asdict(todo) for todo in TodoStore(self.db).list()])

    def test_negative_http_cases_fail_cleanly(self) -> None:
        status, error = self.request("POST", "/api/todos", {"title": ""})
        self.assertEqual(400, status)
        self.assertIn("empty", error["error"])
        status, error = self.request("PATCH", "/api/todos/999", {"completed": True})
        self.assertEqual(404, status)
        self.assertEqual("todo not found", error["error"])

    def test_FL_browser_assets_are_served_and_wired_to_api(self) -> None:
        status, html = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("Todo List", html)
        self.assertIn("/app.js", html)
        status, script = self.request("GET", "/app.js")
        self.assertEqual(200, status)
        self.assertIn("/api/todos", script)


if __name__ == "__main__":
    unittest.main()
