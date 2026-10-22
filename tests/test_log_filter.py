"""Pretend-data checks for workflows/02-email-to-attio/log_filter.py (not written yet).

These follow tests/cases/17-actions-readiness.md, Case S5. They say what the new filter must offer:

  log_filter.clean(text)    the text with anything shaped like an email address replaced by "[removed]"
  log_filter.MARK           "[removed]"
  log_filter.main()         reads standard input line by line, writes the cleaned lines to standard output as they
                            come, and always returns 0 (so it can never hide the real result of a run)

Everything here is fake. Run:  python tests\\test_log_filter.py
"""

import io
import subprocess
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILTER = PROJECT_ROOT / "workflows" / "02-email-to-attio" / "log_filter.py"
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "02-email-to-attio"))

import log_filter  # noqa: E402

ADDRESSES = [
    "sam.fake@example.com",
    "First.Last+tag@Sub.Domain.example",
    "UPPER@EXAMPLE.ORG",
    "x@y.example",
    "<x@y.example>",
    "(x@y.example)",
    "x@y.example,",
    "x@y.example.",
    "mailto:x@y.example",
    "name=x@y.example",
    "ñandú@ejemplo.example",
    "user@localhost",
    "x@y@z",
    "foo@",
    "a.b-c_d@e-f.g.example",
]

UNCHANGED = [
    "e-1a2b3c: Lead - Demo, moved to Leads",
    "Posted to Slack (live): 3",
    "  created person",
    "  created deal (stage New, owner set)",
    "System or security emails skipped: 22",
    "Dry run finished: 2 email(s) would be handled (27 skipped, 0 still waiting).",
    "The email being handled was e-1a2b3c.",
    "Reply drafts made: 1",
    "See @channel for details",
    "a lone @ sign and an @handle",
    "50% off @ noon",
    "",
    "   ",
    "no at sign here, just text. Version 1.2.3",
]


class LogFilterTests(unittest.TestCase):
    def test_every_kind_of_address_is_removed_and_the_words_around_it_stay(self):
        for address in ADDRESSES:
            with self.subTest(address=address):
                cleaned = log_filter.clean(f"before {address} after")
                self.assertNotIn("@", cleaned)
                self.assertIn(log_filter.MARK, cleaned)
                self.assertTrue(cleaned.startswith("before "))
                self.assertTrue(cleaned.endswith(" after"))

    def test_the_mark_is_removed_text_in_square_brackets(self):
        self.assertEqual(log_filter.MARK, "[removed]")
        self.assertEqual(log_filter.clean("from sam.fake@example.com today"), "from [removed] today")

    def test_several_addresses_on_one_line_are_all_removed(self):
        cleaned = log_filter.clean("a@b.example c@d.example, e@f.example")
        self.assertNotIn("@", cleaned)
        self.assertEqual(cleaned.count(log_filter.MARK), 3)

    def test_everything_else_passes_through_unchanged(self):
        for line in UNCHANGED:
            with self.subTest(line=line):
                self.assertEqual(log_filter.clean(line), line)

    def test_cleaning_twice_changes_nothing_more(self):
        for address in ADDRESSES:
            once = log_filter.clean(f"x {address} y")
            self.assertEqual(log_filter.clean(once), once)

    def test_line_endings_are_kept(self):
        text = "one a@b.example" + chr(13) + chr(10) + "two" + chr(10)
        self.assertEqual(log_filter.clean(text), "one [removed]" + chr(13) + chr(10) + "two" + chr(10))

    def test_a_very_long_line_is_fine(self):
        line = ("word " * 200000) + "x@y.example " + ("word " * 1000)
        cleaned = log_filter.clean(line)
        self.assertNotIn("@", cleaned)
        self.assertEqual(len(cleaned), len(line) - len("x@y.example") + len(log_filter.MARK))

    # ---- the command line ------------------------------------------------------------------------------

    def run_filter(self, data):
        done = subprocess.run([sys.executable, "-I", str(FILTER)], input=data, capture_output=True, timeout=30)
        return done.returncode, done.stdout, done.stderr

    def test_it_filters_what_it_is_given_and_ends_successfully(self):
        code, out, err = self.run_filter(
            b"e-1a2b3c: Lead - Demo, moved to Leads\nfrom sam.fake@example.com\nDone: 1 email(s) handled.\n")
        self.assertEqual(code, 0)
        self.assertEqual(out.decode("utf-8").splitlines(),
                         ["e-1a2b3c: Lead - Demo, moved to Leads", "from [removed]", "Done: 1 email(s) handled."])
        self.assertEqual(err, b"")

    def test_it_never_fails_whatever_it_is_given(self):
        for name, data in {
            "nothing": b"",
            "only a newline": b"\n",
            "damaged text": b"\xff\xfe\xfd broken \x80 a@b.example\n",
            "no final newline": b"last line a@b.example",
            "a null byte": b"abc\x00def a@b.example\n",
            "a very long line": b"x " * 500000 + b"a@b.example\n",
        }.items():
            with self.subTest(case=name):
                code, out, err = self.run_filter(data)
                self.assertEqual(code, 0)
                self.assertNotIn(b"@", out)
                self.assertEqual(err, b"")

    def test_lines_appear_as_they_come_not_all_at_the_end(self):
        process = subprocess.Popen([sys.executable, "-I", str(FILTER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        first = []

        def read_first_line():
            first.append(process.stdout.readline())

        try:
            process.stdin.write(b"first line from a@b.example\n")
            process.stdin.flush()
            reader = threading.Thread(target=read_first_line, daemon=True)
            reader.start()
            reader.join(10)
            self.assertEqual(first, [b"first line from [removed]\n"], "the first line must come out before the input ends")
        finally:
            process.stdin.close()
            process.wait(timeout=10)
            process.stdout.close()

    def test_main_returns_zero_and_uses_the_streams_it_is_given(self):
        stdin = io.BytesIO(b"hello a@b.example\n")
        stdout = io.BytesIO()
        self.assertEqual(log_filter.main(stdin=stdin, stdout=stdout), 0)
        self.assertEqual(stdout.getvalue(), b"hello [removed]\n")

    def test_the_filter_reads_and_writes_no_file(self):
        text = FILTER.read_text(encoding="utf-8")
        for word in ("open(", "write_text", "write_bytes", "read_text", "Path("):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
