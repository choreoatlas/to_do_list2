from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from todo.http import make_server


class BrowserFunctionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        static = Path(__file__).resolve().parents[1] / "static"
        self.server = make_server("127.0.0.1", 0, root / "browser.sqlite3", static)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def test_FL_browser_real_user_flow(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(self.base_url)

            page.get_by_placeholder("What needs doing?").fill("Browser todo")
            page.get_by_role("button", name="Add").click()
            expect(page.get_by_text("Browser todo")).to_be_visible()

            checkbox = page.get_by_role("checkbox")
            checkbox.check()
            expect(checkbox).to_be_checked()
            checkbox.uncheck()
            expect(checkbox).not_to_be_checked()

            page.once("dialog", lambda dialog: dialog.accept("Edited in browser"))
            page.get_by_role("button", name="Edit").click()
            expect(page.get_by_text("Edited in browser")).to_be_visible()

            page.get_by_role("button", name="Delete").click()
            expect(page.get_by_text("Edited in browser")).to_have_count(0)

            browser.close()


if __name__ == "__main__":
    unittest.main()
