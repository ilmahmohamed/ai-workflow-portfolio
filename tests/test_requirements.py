"""Checks for the pinned list of libraries (requirements.txt at the top of the project, not written yet).

These follow tests/cases/17-actions-readiness.md, Case S6. Run:  python tests\\test_requirements.py
"""

import ast
import importlib.metadata
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
WORKFLOW_FOLDERS = [PROJECT_ROOT / "workflows" / "01-outlook-to-slack", PROJECT_ROOT / "workflows" / "02-email-to-attio"]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([0-9][A-Za-z0-9.\-+]*)$")
NEEDED = {"msal", "requests", "pyjwt", "cryptography", "urllib3", "idna", "certifi", "charset-normalizer"}


def pins():
    found = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            found[line] = PIN.match(line)
    return found


def normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()


class RequirementsTests(unittest.TestCase):
    def test_the_file_is_at_the_top_of_the_project(self):
        self.assertTrue(REQUIREMENTS.is_file())

    def test_every_line_is_pinned_to_one_exact_version(self):
        for line, match in pins().items():
            self.assertIsNotNone(match, f"not an exact pin: {line!r}")

    def test_nothing_but_pinned_libraries_is_in_it(self):
        text = REQUIREMENTS.read_text(encoding="utf-8")
        for word in ("http", "git+", "--", "-e ", "-r ", ">=", "<=", "~=", "*", "@"):
            self.assertNotIn(word, text)

    def test_no_library_is_listed_twice(self):
        names = [normal(m.group(1)) for m in pins().values() if m]
        self.assertEqual(len(names), len(set(names)))

    def test_it_holds_the_two_libraries_the_scripts_use_and_what_they_need(self):
        names = {normal(m.group(1)) for m in pins().values() if m}
        self.assertTrue(NEEDED <= names, f"missing: {sorted(NEEDED - names)}")

    def test_the_pinned_versions_are_the_ones_installed_here(self):
        for line, match in pins().items():
            with self.subTest(line=line):
                self.assertEqual(importlib.metadata.version(match.group(1)), match.group(2))

    def test_the_scripts_use_no_outside_library_that_is_not_listed(self):
        local = {path.stem for folder in WORKFLOW_FOLDERS for path in folder.glob("*.py")}
        used = set()
        for folder in WORKFLOW_FOLDERS:
            for path in folder.glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        used |= {alias.name.split(".")[0] for alias in node.names}
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        used.add(node.module.split(".")[0])
        outside = {name for name in used if name not in sys.stdlib_module_names and name not in local}
        listed = {normal(m.group(1)) for m in pins().values() if m}
        self.assertEqual({normal(name) for name in outside} - listed, set(), "an outside library is not in requirements.txt")
        self.assertEqual({normal(name) for name in outside}, {"msal", "requests"}, "the scripts need exactly these two")


if __name__ == "__main__":
    unittest.main(verbosity=2)
