"""Checks for workflows/02-email-to-attio/push_github_secrets.py. Run:  python tests\\test_push_github_secrets.py

No real gh, no real .env and no real secret is used.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).resolve().parents[1] / "workflows" / "02-email-to-attio" / "push_github_secrets.py"
spec = importlib.util.spec_from_file_location("push_github_secrets", HELPER_PATH)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

FAKE_VALUES = {name: f"FAKE-VALUE-{i}-SECRET" for i, name in enumerate(helper.SECRET_NAMES)}


def write_env(folder, values):
    path = Path(folder) / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


class PushSecretsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.lines = []

    def run_helper(self, values, dry_run, push=None):
        env = write_env(self.folder.name, values)
        with mock.patch.object(helper, "push_secret", push or mock.Mock(return_value=True)) as fake_push:
            result = helper.run(dry_run, env_path=env, gh="fake-gh", out=self.lines.append)
        return result, fake_push

    def test_dry_run_lists_names_and_sends_nothing(self):
        (copied, skipped, failed), fake_push = self.run_helper(FAKE_VALUES, dry_run=True)
        fake_push.assert_not_called()
        self.assertEqual(copied, list(helper.SECRET_NAMES))
        self.assertEqual((skipped, failed), ([], []))

    def test_no_value_is_ever_printed(self):
        self.run_helper(FAKE_VALUES, dry_run=False)
        self.run_helper(FAKE_VALUES, dry_run=True)
        self.assertNotIn("FAKE-VALUE", "\n".join(self.lines))

    def test_missing_and_empty_are_skipped(self):
        values = dict(FAKE_VALUES)
        del values["ATTIO_API_KEY"]
        values["SLACK_LOG_WEBHOOK_URL"] = ""
        (copied, skipped, failed), fake_push = self.run_helper(values, dry_run=False)
        self.assertEqual(skipped, ["SLACK_LOG_WEBHOOK_URL", "ATTIO_API_KEY"])
        self.assertEqual(fake_push.call_count, len(helper.SECRET_NAMES) - 2)
        self.assertEqual(failed, [])

    def test_a_failed_copy_is_reported_by_name_only(self):
        (copied, skipped, failed), _ = self.run_helper(FAKE_VALUES, dry_run=False, push=mock.Mock(return_value=False))
        self.assertEqual(failed, list(helper.SECRET_NAMES))
        self.assertNotIn("FAKE-VALUE", "\n".join(self.lines))

    def test_nothing_outside_the_list_is_copied(self):
        values = dict(FAKE_VALUES, SOMETHING_ELSE="x", SLACK_TEST_WEBHOOK_URL="y")
        _, fake_push = self.run_helper(values, dry_run=False)
        sent = {call.args[2] for call in fake_push.call_args_list}
        self.assertEqual(sent, set(helper.SECRET_NAMES))

    def test_value_goes_through_stdin_not_the_command_line(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(helper.subprocess, "run", return_value=completed) as fake_run:
            self.assertTrue(helper.push_secret("gh", "o/r", "NAME", "FAKE-VALUE-SECRET"))
        command = fake_run.call_args.args[0]
        self.assertNotIn("FAKE-VALUE-SECRET", " ".join(command))
        self.assertEqual(fake_run.call_args.kwargs["input"], "FAKE-VALUE-SECRET")
        self.assertTrue(fake_run.call_args.kwargs["capture_output"])

    def test_missing_env_file_skips_everything(self):
        missing = Path(self.folder.name) / "nope.env"
        copied, skipped, failed = helper.run(True, env_path=missing, out=self.lines.append)
        self.assertEqual((copied, failed), ([], []))
        self.assertEqual(skipped, list(helper.SECRET_NAMES))


if __name__ == "__main__":
    unittest.main()
