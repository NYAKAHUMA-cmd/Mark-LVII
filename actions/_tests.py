"""Focused tests for computer-control safety rules.

    python -m actions._tests
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import computer_control
from core import appidentity, confirm


class BackgroundAppSafetyTests(unittest.TestCase):
    def test_candidate_filter_keeps_only_other_user_apps(self):
        eligible = computer_control._is_background_app_candidate
        protected = {101}
        self.assertTrue(eligible(1, "notepad.exe", "user", "user", 2, protected))
        self.assertFalse(eligible(2, "notepad.exe", "user", "user", 2, protected))
        self.assertFalse(eligible(3, "chrome.exe", "user", "user", 2, protected))
        self.assertFalse(eligible(4, "notepad.exe", "other", "user", 2, protected))
        self.assertFalse(eligible(101, "notepad.exe", "user", "user", 2, protected))
        self.assertFalse(eligible(5, "explorer.exe", "user", "user", 2, protected))

    def test_bulk_close_never_terminates_without_a_visible_window(self):
        process = MagicMock()
        process.exe.return_value = r"C:\Apps\sample.exe"
        match = appidentity.Match(
            pid=123, name="sample.exe", score=0, is_self=False,
        )
        with (
            patch.object(appidentity, "_PSUTIL", True),
            patch.object(appidentity, "own_pids", return_value=set()),
            patch.object(appidentity, "own_exes", return_value=set()),
            patch.object(appidentity, "_post_close_windows", return_value=0),
            patch.object(appidentity.psutil, "Process", return_value=process),
        ):
            gone, detail = appidentity.request_close(match, require_window=True)

        self.assertFalse(gone)
        self.assertIn("left running", detail)
        process.terminate.assert_not_called()

    def test_bulk_close_rejects_reused_process_id(self):
        process = MagicMock()
        process.create_time.return_value = 200.0
        process.exe.return_value = r"C:\Apps\sample.exe"
        match = appidentity.Match(
            pid=123, name="sample.exe", score=0, is_self=False,
        )
        with (
            patch.object(appidentity, "_PSUTIL", True),
            patch.object(appidentity, "own_pids", return_value=set()),
            patch.object(appidentity, "own_exes", return_value=set()),
            patch.object(appidentity, "_post_close_windows") as post_close,
            patch.object(appidentity.psutil, "Process", return_value=process),
        ):
            gone, detail = appidentity.request_close(
                match, require_window=True, expected_create_time=100.0,
            )

        self.assertFalse(gone)
        self.assertIn("changed since it was listed", detail)
        post_close.assert_not_called()
        process.terminate.assert_not_called()

    def test_confirmation_does_not_replace_an_existing_request(self):
        shown = []
        confirm.bind(lambda title, detail: shown.append(title), lambda: None)
        try:
            first = confirm.request("first", "First action", "Details", lambda: "done")
            second = confirm.request("second", "Second action", "Details", lambda: "done")

            self.assertIn("[CONFIRMATION_PENDING]", first)
            self.assertIn("already waiting", second)
            self.assertEqual(confirm.pending_title(), "First action")
            self.assertEqual(shown, ["First action"])
        finally:
            confirm.resolve(False)
            confirm.bind(None, None)


if __name__ == "__main__":
    unittest.main()
