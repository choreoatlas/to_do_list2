from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path


class ProcessDurabilityEvidenceTests(unittest.TestCase):
    def start_app(self, db: Path) -> tuple[subprocess.Popen, int]:
        root = Path(__file__).resolve().parents[1]
        process = subprocess.Popen(
            [sys.executable, "-u", str(root / "app.py"), "--port", "0", "--db", str(db)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_app, process)
        started = queue.Queue()
        threading.Thread(target=lambda: started.put(process.stdout.readline()), daemon=True).start()
        try:
            announcement = started.get(timeout=10)
        except queue.Empty:
            self.fail("app process did not announce its HTTP address")
        self.assertTrue(announcement.startswith("Todo List running at http://127.0.0.1:"), announcement)
        return process, int(announcement.strip().rsplit(":", 1)[1])

    @staticmethod
    def stop_app(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()

    def request(self, port: int, method: str, path: str, payload=None):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = None if payload is None else json.dumps(payload)
            connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

    def test_committed_user_changes_survive_application_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "restart.sqlite3"
            first, port = self.start_app(db)
            status, todo = self.request(port, "POST", "/api/todos", {"title": "Before restart"})
            self.assertEqual(201, status)
            path = f"/api/todos/{todo['id']}"
            status, expected = self.request(port, "PATCH", path, {"title": "Saved title", "completed": True})
            self.assertEqual(200, status)
            status, removed = self.request(port, "POST", "/api/todos", {"title": "Remove before restart"})
            self.assertEqual(201, status)
            status, _ = self.request(port, "DELETE", f"/api/todos/{removed['id']}")
            self.assertEqual(204, status)
            self.stop_app(first)

            second, port = self.start_app(db)
            try:
                status, persisted = self.request(port, "GET", "/api/todos")
                self.assertEqual(200, status)
                self.assertEqual([expected], persisted)
                status, missing = self.request(port, "GET", f"/api/todos/{removed['id']}")
                self.assertEqual(404, status)
                self.assertEqual("todo not found", missing["error"])
            finally:
                self.stop_app(second)


if __name__ == "__main__":
    unittest.main()
