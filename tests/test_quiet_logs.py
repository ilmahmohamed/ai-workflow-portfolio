"""Checks for workflows/02-email-to-attio/trim_log.py and the two scheduler batch files.

These follow tests/cases/06-quiet-logs.md (which is not changed).
The helper tests only use throwaway files. The batch-file tests only read the batch files
as text, so nothing is run and no real mailbox, key or service is touched.

Run:  python tests\\test_quiet_logs.py
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FOLDER = PROJECT_ROOT / "workflows" / "02-email-to-attio"
sys.path.insert(0, str(FOLDER))

import trim_log  # noqa: E402


def numbered(count):
    return b"".join(f"line {i}\r\n".encode() for i in range(1, count + 1))


class TrimLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "log.txt"

    def lines(self):
        return self.path.read_bytes().splitlines(keepends=True)

    # ---- Case 1 -----------------------------------------------------------

    def test_case1_a_short_log_is_left_alone(self):
        self.path.write_bytes(numbered(300))
        self.assertFalse(trim_log.trim_log(self.path))
        self.assertEqual(self.path.read_bytes(), numbered(300))

    # ---- Case 2 -----------------------------------------------------------

    def test_case2_a_long_log_is_cut_to_its_latest_500_lines(self):
        self.path.write_bytes(numbered(800))
        self.assertTrue(trim_log.trim_log(self.path))
        lines = self.lines()
        self.assertEqual(len(lines), 500)
        self.assertEqual(lines[0], b"line 301\r\n")
        self.assertEqual(lines[-1], b"line 800\r\n")

    def test_case2_no_temporary_copy_is_left_behind(self):
        self.path.write_bytes(numbered(800))
        trim_log.trim_log(self.path)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["log.txt"])

    # ---- Case 3 -----------------------------------------------------------

    def test_case3_the_file_is_only_rewritten_once_it_is_well_past_the_limit(self):
        self.path.write_bytes(numbered(600))
        self.assertFalse(trim_log.trim_log(self.path))
        self.assertEqual(len(self.lines()), 600)
        self.path.write_bytes(numbered(601))
        self.assertTrue(trim_log.trim_log(self.path))
        self.assertEqual(len(self.lines()), 500)

    def test_case3_the_limit_and_the_slack_can_be_set(self):
        self.path.write_bytes(numbered(7))
        self.assertFalse(trim_log.trim_log(self.path, keep=5, slack=2))
        self.path.write_bytes(numbered(8))
        self.assertTrue(trim_log.trim_log(self.path, keep=5, slack=2))
        self.assertEqual(self.lines(), [f"line {i}\r\n".encode() for i in range(4, 9)])

    # ---- Case 4 -----------------------------------------------------------

    def test_case4_a_missing_file_is_not_an_error_and_is_not_created(self):
        self.assertFalse(trim_log.trim_log(self.dir / "nope.txt"))
        self.assertFalse((self.dir / "nope.txt").exists())

    def test_case4_an_empty_file_stays_empty(self):
        self.path.write_bytes(b"")
        self.assertFalse(trim_log.trim_log(self.path))
        self.assertEqual(self.path.read_bytes(), b"")

    def test_case4_a_last_line_without_a_line_ending_is_kept_as_it_is(self):
        self.path.write_bytes(b"a\nb\nc")
        self.assertTrue(trim_log.trim_log(self.path, keep=2, slack=0))
        self.assertEqual(self.path.read_bytes(), b"b\nc")

    def test_case4_a_file_that_cannot_be_rewritten_is_left_alone_quietly(self):
        self.path.write_bytes(numbered(800))
        with mock.patch.object(os, "replace", side_effect=PermissionError("locked")):
            self.assertFalse(trim_log.trim_log(self.path))
        self.assertEqual(self.path.read_bytes(), numbered(800))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["log.txt"])

    # ---- Case 5 -----------------------------------------------------------

    def test_case5_the_kept_text_is_byte_for_byte_the_same(self):
        odd = b"caf\xe9 \xff\xfe odd bytes \x00\r\n"
        original = b"".join(odd + f"{i}\r\n".encode() for i in range(10))
        self.path.write_bytes(original)
        trim_log.trim_log(self.path, keep=4, slack=0)
        self.assertEqual(self.path.read_bytes(), b"".join(original.splitlines(keepends=True)[-4:]))

    # ---- Case 6 -----------------------------------------------------------

    def run_cli(self, argv):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            code = trim_log.main(argv)
        self.assertEqual(out.getvalue(), "", "the helper must print nothing")
        return code

    def test_case6_the_helper_keeps_500_lines_by_default(self):
        self.path.write_bytes(numbered(800))
        self.assertEqual(self.run_cli([str(self.path)]), 0)
        self.assertEqual(len(self.lines()), 500)

    def test_case6_a_number_after_the_file_name_sets_the_limit(self):
        self.path.write_bytes(numbered(800))
        self.assertEqual(self.run_cli([str(self.path), "200"]), 0)
        self.assertEqual(len(self.lines()), 200)

    def test_case6_it_always_ends_successfully(self):
        self.path.write_bytes(numbered(800))
        self.assertEqual(self.run_cli([]), 0)
        self.assertEqual(self.run_cli([str(self.dir / "nope.txt")]), 0)
        self.assertEqual(self.run_cli([str(self.path), "not-a-number"]), 0)
        self.assertEqual(len(self.lines()), 500, "a bad number falls back to the default")


class BatchFileTests(unittest.TestCase):
    """Case 7 and 8: read the batch files and .gitignore as text. Nothing is run."""

    def text(self, name):
        return (FOLDER / name).read_text(encoding="utf-8")

    def check_batch_file(self, name, script, log, other_log):
        text = self.text(name)
        # The name of whoever runs the test is looked up, so no real name is written in this file.
        for personal in ("Users", Path.home().name, "AppData", "C:\\"):
            self.assertNotIn(personal, text, f"{name} must not contain a personal path or name")
        self.assertIn('cd /d "%~dp0..\\.."', text)
        self.assertIn(f"%~dp0{log}", text)
        self.assertNotIn(other_log, text, f"{name} must only use its own log file")
        self.assertIn("set PYTHONIOENCODING=utf-8", text)
        self.assertIn(f"{script} --live", text)
        self.assertIn("exit code: %ERRORLEVEL%", text)
        self.assertIn('trim_log.py "%LOG%"', text)
        trimmed, heading, ran = text.index("trim_log.py"), text.index("echo ====="), text.index(f"{script} --live")
        self.assertLess(trimmed, heading, "the log is trimmed before anything is written to it")
        self.assertLess(heading, ran)

    def test_case7_the_triage_batch_file(self):
        self.check_batch_file("run_triage.bat", "outlook_triage.py", "triage-log.txt", "leads-to-attio-log.txt")

    def test_case7_the_attio_batch_file(self):
        self.check_batch_file("run_leads_to_attio.bat", "leads_to_attio.py", "leads-to-attio-log.txt",
                              "triage-log.txt")

    def test_case8_the_logs_and_their_temporary_copies_are_ignored_by_git(self):
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("workflows/02-email-to-attio/triage-log.txt*", ignore)
        self.assertIn("workflows/02-email-to-attio/leads-to-attio-log.txt*", ignore)


if __name__ == "__main__":
    unittest.main(verbosity=2)
