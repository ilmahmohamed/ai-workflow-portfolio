"""Pretend-data checks for workflows/01-outlook-to-slack/outlook_to_slack.py.

These follow tests/cases/01-outlook-to-slack.md (which is not changed).
Everything here is fake and self-contained:
  - a pretend Outlook and a pretend Slack answer in place of the real ones,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened).

Run:  python tests\\test_outlook_to_slack.py
"""

import base64
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "01-outlook-to-slack"))

import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()

FAKE_SLACK_URL = "https://hooks.slack.test/services/FAKE/FAKE/FAKE"
FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_ENV = {
    "MS_CLIENT_ID": "fake-client-id",
    "MS_TOKEN_CACHE": FAKE_CACHE,
    "SLACK_WEBHOOK_URL": FAKE_SLACK_URL,
}
SECRETS = (FAKE_SLACK_URL, FAKE_TOKEN, FAKE_CACHE)

SAM = ("sam.fake@example.com", "Pricing for your wellness plan")


def email(sender, subject):
    return {"subject": subject, "from": {"emailAddress": {"address": sender, "name": "Fake"}}}


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class PretendWorld:
    """A pretend Outlook and a pretend Slack. Records every request made."""

    def __init__(self, emails=(), slack_error=None, folders=None):
        self.emails = list(emails)
        self.slack_error = slack_error
        self.folders = folders if folders is not None else [
            {"id": "inbox-id", "displayName": "Inbox"},
            {"id": "leads-id", "displayName": "Leads"},
        ]
        self.requests = []
        self.unexpected = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        if url.startswith(FAKE_SLACK_URL):
            if self.slack_error:
                raise self.slack_error
            return FakeResponse(b"ok")
        if url.startswith(outlook_to_slack.GRAPH_URL):
            path = urllib.parse.urlparse(url).path
            if path.endswith("/me/mailFolders"):
                return FakeResponse(json.dumps({"value": self.folders}).encode())
            if path.endswith("/inbox/childFolders"):
                return FakeResponse(json.dumps({"value": []}).encode())
            if path.endswith("/leads-id/messages"):
                # Like the real Outlook, hand back only as many as were asked for.
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                top = int(query["$top"][0])
                return FakeResponse(json.dumps({"value": self.emails[:top]}).encode())
        self.unexpected.append(url)
        raise urllib.error.URLError("pretend world: unexpected address")

    def slack_posts(self):
        return [r for r in self.requests if r.full_url.startswith(FAKE_SLACK_URL)]

    def outlook_requests(self):
        return [r for r in self.requests if r.full_url.startswith(outlook_to_slack.GRAPH_URL)]

    def posted_texts(self):
        return [json.loads(r.data.decode())["text"] for r in self.slack_posts()]


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


class OutlookToSlackTests(unittest.TestCase):
    def setUp(self):
        # Throwaway .env in place of the real one.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env_path = Path(tmp.name) / ".env"
        self.env_path.write_text("MS_CLIENT_ID=fake-from-file\n", encoding="utf-8")
        self.assertFalse(touches_real_env(self.env_path))

        for module in (outlook_signin, outlook_to_slack):
            patcher = mock.patch.object(module, "ENV_PATH", self.env_path)
            patcher.start()
            self.addCleanup(patcher.stop)

        # Tripwires: any attempt to read or change the real .env fails the test.
        def guard(original):
            def wrapper(path, *args, **kwargs):
                if touches_real_env(path):
                    raise AssertionError("TEST SAFETY: the real .env must not be touched")
                return original(path, *args, **kwargs)
            return wrapper

        for name in ("read_bytes", "write_bytes", "read_text", "write_text", "unlink"):
            patcher = mock.patch.object(Path, name, guard(getattr(Path, name)))
            patcher.start()
            self.addCleanup(patcher.stop)

        real_replace = os.replace

        def guarded_replace(src, dst, *args, **kwargs):
            if touches_real_env(src) or touches_real_env(dst):
                raise AssertionError("TEST SAFETY: the real .env must not be touched")
            return real_replace(src, dst, *args, **kwargs)

        patcher = mock.patch.object(os, "replace", guarded_replace)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Block the real internet completely.
        import socket

        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, world, argv=(), remove=()):
        """Run the script's main() against a pretend world. Returns (code, output)."""
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["outlook_to_slack.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen), \
                mock.patch.object(outlook_to_slack, "get_access_token", return_value=FAKE_TOKEN):
            for name in remove:
                os.environ.pop(name, None)
            code = outlook_to_slack.main()
        output = buffer.getvalue().decode("utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a secret was printed on screen")
        self.assertEqual(world.unexpected, [], "the script contacted an unexpected address")
        return code, output

    def assert_only_reads(self, world):
        for request in world.outlook_requests():
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)

    # ---- Case 1: one lead email -------------------------------------------

    def test_case1_dry_run_prints_would_post_and_sends_nothing(self):
        world = PretendWorld([email(*SAM)])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn(f"would post: {SAM[0]}, {SAM[1]}", output)
        self.assertEqual(world.slack_posts(), [])
        self.assert_only_reads(world)

    def test_case1_live_posts_one_message_with_sender_and_subject(self):
        world = PretendWorld([email(*SAM)])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.posted_texts(), [f"{SAM[0]}, {SAM[1]}"])
        self.assertEqual(world.slack_posts()[0].get_method(), "POST")

    def test_case1_email_is_not_changed_or_moved(self):
        world = PretendWorld([email(*SAM)])
        self.run_main(world, ["--live"])
        self.assertTrue(world.outlook_requests())
        self.assert_only_reads(world)

    def test_special_characters_are_made_safe_for_slack(self):
        world = PretendWorld([email("a@example.com", "Q&A <urgent>")])
        self.run_main(world, ["--live"])
        self.assertEqual(world.posted_texts(), ["a@example.com, Q&amp;A &lt;urgent&gt;"])

    # ---- Case 2: the Leads folder is empty --------------------------------

    def test_case2_empty_folder_dry_run(self):
        world = PretendWorld([])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn("No emails found", output)
        self.assertEqual(world.slack_posts(), [])

    def test_case2_empty_folder_live_posts_nothing_and_no_error(self):
        world = PretendWorld([])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("No emails found", output)
        self.assertEqual(world.slack_posts(), [])

    # ---- Case 3: Slack cannot be reached ----------------------------------

    def test_case3_slack_unreachable_is_reported_clearly(self):
        world = PretendWorld([email(*SAM)], slack_error=urllib.error.URLError("down"))
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Slack", output)
        self.assertIn("0 of 1", output)
        self.assertNotIn("Done", output)
        self.assertNotIn("posted:", output)

    def test_case3_email_is_left_alone_so_next_run_picks_it_up(self):
        world = PretendWorld([email(*SAM)], slack_error=urllib.error.URLError("down"))
        _, output = self.run_main(world, ["--live"])
        self.assertIn("next run picks them up again", output)
        self.assert_only_reads(world)

    def test_case3_slack_refusing_the_message_is_reported(self):
        refusal = urllib.error.HTTPError(FAKE_SLACK_URL, 404, "Not Found", {}, io.BytesIO(b""))
        world = PretendWorld([email(*SAM)], slack_error=refusal)
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("code 404", output)
        self.assertNotIn("Done", output)

    def test_case3_stops_at_first_failure(self):
        world = PretendWorld(
            [email("a@example.com", "One"), email("b@example.com", "Two")],
            slack_error=urllib.error.URLError("down"),
        )
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertEqual(len(world.slack_posts()), 1)

    # ---- Default behaviour ------------------------------------------------

    def test_default_is_a_dry_run(self):
        world = PretendWorld([email(*SAM)])
        code, output = self.run_main(world, [])
        self.assertIn("Dry run", output)
        self.assertEqual(world.slack_posts(), [])

    def test_dry_run_does_not_need_the_slack_link(self):
        world = PretendWorld([email(*SAM)])
        code, output = self.run_main(world, [], remove=["SLACK_WEBHOOK_URL"])
        self.assertEqual(code, 0)
        self.assertIn("would post:", output)

    def test_live_without_slack_link_stops_before_doing_anything(self):
        world = PretendWorld([email(*SAM)])
        code, output = self.run_main(world, ["--live"], remove=["SLACK_WEBHOOK_URL"])
        self.assertEqual(code, 1)
        self.assertIn("SLACK_WEBHOOK_URL", output)
        self.assertEqual(world.requests, [])

    # ---- The --limit option -----------------------------------------------

    def three_emails(self):
        return [
            email("one@example.com", "First"),
            email("two@example.com", "Second"),
            email("three@example.com", "Third"),
        ]

    def test_limit_1_dry_run_shows_only_the_newest_email(self):
        world = PretendWorld(self.three_emails())
        code, output = self.run_main(world, ["--limit", "1"])
        self.assertEqual(code, 0)
        self.assertIn("would post: one@example.com, First", output)
        self.assertNotIn("two@example.com", output)
        self.assertNotIn("three@example.com", output)
        self.assertEqual(world.slack_posts(), [])

    def test_limit_1_live_posts_exactly_one_message(self):
        world = PretendWorld(self.three_emails())
        code, _ = self.run_main(world, ["--live", "--limit", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(world.posted_texts(), ["one@example.com, First"])
        self.assert_only_reads(world)

    def test_without_limit_it_still_asks_for_25(self):
        world = PretendWorld(self.three_emails())
        code, output = self.run_main(world, [])
        self.assertIn("would post: three@example.com, Third", output)
        messages = [r for r in world.outlook_requests() if r.full_url.split("?")[0].endswith("/messages")]
        self.assertIn("%24top=25", messages[0].full_url)

    def test_bad_limit_values_are_refused_before_anything_happens(self):
        for bad in ("0", "-1", "101", "abc"):
            with self.subTest(limit=bad):
                world = PretendWorld(self.three_emails())
                errors = io.StringIO()
                with mock.patch.object(sys, "stderr", errors):
                    with self.assertRaises(SystemExit) as stop:
                        self.run_main(world, ["--limit", bad])
                self.assertNotEqual(stop.exception.code, 0)
                self.assertIn("--limit must be a whole number from 1 to 100", errors.getvalue())
                self.assertEqual(world.requests, [])

    # ---- Other safety checks ----------------------------------------------

    def test_missing_leads_folder_is_explained(self):
        world = PretendWorld([email(*SAM)], folders=[{"id": "inbox-id", "displayName": "Inbox"}])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 1)
        self.assertIn("couldn't find a folder called Leads", output)

    def test_at_most_25_emails_are_requested(self):
        world = PretendWorld([email(*SAM)])
        self.run_main(world, [])
        messages = [r for r in world.outlook_requests() if r.full_url.split("?")[0].endswith("/messages")]
        self.assertIn("%24top=25", messages[0].full_url)

    # ---- Saving a refreshed sign-in ---------------------------------------

    def make_cache(self, payload):
        cache = mock.Mock()
        cache.serialize.return_value = json.dumps(payload)
        return cache

    def test_refreshed_token_changes_only_the_cache_line(self):
        self.env_path.write_text(
            "A=1\nMS_CLIENT_ID=abc\nMS_TOKEN_CACHE=old\nSLACK_WEBHOOK_URL=keep-me\nB=2\n",
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            outlook_to_slack.save_refreshed_cache(self.make_cache({"k": "new-secret"}))
        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        expected = base64.urlsafe_b64encode(b'{"k":"new-secret"}').decode()
        self.assertEqual(
            lines,
            ["A=1", "MS_CLIENT_ID=abc", f"MS_TOKEN_CACHE={expected}", "SLACK_WEBHOOK_URL=keep-me", "B=2"],
        )
        self.assertEqual(buffer.getvalue(), "")
        self.assertFalse(self.env_path.with_name(".env.tmp").exists())

    def test_failed_save_is_reported_and_env_is_unchanged(self):
        original = "A=1\nMS_TOKEN_CACHE=old\n"
        self.env_path.write_text(original, encoding="utf-8")
        cache = mock.Mock()
        cache.serialize.side_effect = RuntimeError("boom")
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            outlook_to_slack.save_refreshed_cache(cache)
        self.assertIn("couldn't save the refreshed sign-in", buffer.getvalue())
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), original)

    def test_no_env_file_means_nothing_is_created(self):
        self.env_path.unlink()
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            outlook_to_slack.save_refreshed_cache(self.make_cache({"k": "v"}))
        self.assertIn("no .env file", buffer.getvalue())
        self.assertFalse(self.env_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
