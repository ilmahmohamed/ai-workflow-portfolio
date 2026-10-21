"""Pretend-data checks for workflows/01-outlook-to-slack/write_check.py.

These follow tests/cases/03-write-check.md (which is not changed).
Everything here is fake and self-contained:
  - a pretend Outlook answers in place of the real one,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened).

Run:  python tests\\test_write_check.py
"""

import copy
import io
import json
import os
import re
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
import write_check  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_ENV = {"MS_CLIENT_ID": "fake-client-id", "MS_TOKEN_CACHE": FAKE_CACHE}
SECRETS = (FAKE_TOKEN, FAKE_CACHE)

SUBJECT = "WRITE-CHECK test"
CATEGORY = "Needs review"


def message(message_id, subject=SUBJECT, categories=()):
    return {"id": message_id, "subject": subject, "categories": list(categories)}


def http_error(code):
    return urllib.error.HTTPError("https://graph.test/x", code, "pretend", {}, io.BytesIO(b""))


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class PretendOutlook:
    """A pretend Outlook Inbox. Records every request made to it."""

    def __init__(self, messages=(), patch_errors=None, confirm="ok", unreachable=False):
        self.messages = {m["id"]: copy.deepcopy(m) for m in messages}
        self.patch_errors = patch_errors or {}   # {1: error} fails the first change request
        self.confirm = confirm                   # ok, error500, hidden or unreachable
        self.unreachable = unreachable
        self.requests = []
        self.patch_bodies = []
        self.unexpected = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        if self.unreachable:
            raise urllib.error.URLError("pretend: no internet")
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path

        if method == "GET" and path.endswith("/me/mailFolders/inbox/messages"):
            # Like a sloppy search: hand back every message, the script must pick the exact ones.
            return FakeResponse(json.dumps({"value": list(copy.deepcopy(list(self.messages.values())))}).encode())

        found = re.search(r"/me/messages/([^/]+)$", path)
        if found:
            message_id = urllib.parse.unquote(found.group(1))
            current = self.messages.get(message_id)
            if current is not None and method == "GET":
                if self.confirm == "error500":
                    raise http_error(500)
                if self.confirm == "unreachable":
                    raise urllib.error.URLError("pretend: no internet")
                shown = copy.deepcopy(current)
                if self.confirm == "hidden":
                    shown["categories"] = [c for c in shown["categories"] if c != CATEGORY]
                return FakeResponse(json.dumps(shown).encode())
            if current is not None and method == "PATCH":
                body = json.loads(request.data.decode())
                self.patch_bodies.append(body)
                error = self.patch_errors.get(len(self.patch_bodies))
                if error is not None:
                    raise error
                current["categories"] = list(body["categories"])
                return FakeResponse(json.dumps(current).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]

    def categories(self, message_id="msg-1"):
        return self.messages[message_id]["categories"]


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


class WriteCheckTests(unittest.TestCase):
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

    def run_main(self, world, argv=(), remove=(), token_error=None):
        """Run the script's main() against a pretend Outlook. Returns (code, output)."""
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        if token_error is not None:
            token_patch = mock.patch.object(write_check, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(write_check, "get_access_token", return_value=FAKE_TOKEN)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["write_check.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen), \
                token_patch:
            for name in remove:
                os.environ.pop(name, None)
            code = write_check.main()
        output = buffer.getvalue().decode("utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a token was printed on screen")
        self.assertEqual(world.unexpected, [], "the script sent an unexpected request")
        return code, output

    def one(self, categories=()):
        return PretendOutlook([message("msg-1", categories=categories)])

    # ---- Case 1: exactly one match works ---------------------------------

    def test_case1_live_adds_confirms_and_removes_the_category(self):
        world = self.one()
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.patch_bodies, [{"categories": [CATEGORY]}, {"categories": []}])
        self.assertEqual(world.categories(), [])
        self.assertEqual(world.methods(), ["GET", "PATCH", "GET", "PATCH"])
        self.assertIn("Write check passed", output)
        self.assertIn("back to what they were", output)

    # ---- Case 2: no match ---------------------------------------------------

    def test_case2_no_match_says_so_and_changes_nothing(self):
        for argv in ([], ["--live"]):
            with self.subTest(argv=argv):
                world = PretendOutlook([message("msg-9", subject="Something else")])
                code, output = self.run_main(world, argv)
                self.assertEqual(code, 1)
                self.assertIn("couldn't find an email", output)
                self.assertIn(SUBJECT, output)
                self.assertEqual(world.changes(), [])

    def test_case2_an_empty_inbox_is_the_same(self):
        world = PretendOutlook([])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't find an email", output)
        self.assertEqual(world.changes(), [])

    # ---- Case 3: more than one match ------------------------------------------

    def test_case3_two_matches_are_refused(self):
        world = PretendOutlook([message("msg-1"), message("msg-2")])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("found 2 emails", output)
        self.assertIn("exactly one", output)
        self.assertEqual(world.changes(), [])
        self.assertEqual(world.categories("msg-1"), [])
        self.assertEqual(world.categories("msg-2"), [])

    def test_case3_dry_run_also_refuses(self):
        world = PretendOutlook([message("msg-1"), message("msg-2"), message("msg-3")])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 1)
        self.assertIn("found 3 emails", output)
        self.assertEqual(world.changes(), [])

    # ---- Case 4: only the exact subject counts ----------------------------------

    def test_case4_near_misses_do_not_count(self):
        world = PretendOutlook([
            message("a", subject="write-check TEST"),
            message("b", subject="WRITE-CHECK test 2"),
            message("c", subject="Re: WRITE-CHECK test"),
        ])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't find an email", output)
        self.assertEqual(world.changes(), [])

    def test_case4_extra_spaces_around_the_subject_still_match(self):
        world = PretendOutlook([message("msg-1", subject="  WRITE-CHECK test  ")])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("Write check passed", output)

    def test_case4_a_near_miss_next_to_the_real_one_is_left_alone(self):
        world = PretendOutlook([message("msg-1"), message("other", subject="WRITE-CHECK test 2")])
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.categories("other"), [])
        self.assertFalse([r for r in world.requests if "other" in r.full_url])

    # ---- Case 5: Outlook refuses the write -----------------------------------------

    def test_case5_refused_write_says_the_sign_in_may_need_redoing(self):
        for code_number in (401, 403):
            with self.subTest(code=code_number):
                world = PretendOutlook([message("msg-1")], patch_errors={1: http_error(code_number)})
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn("Outlook refused the change", output)
                self.assertIn("sign-in may need redoing", output)
                self.assertIn("outlook_signin.py", output)
                self.assertNotIn(str(code_number), output)
                self.assertNotIn("HTTPError", output)
                self.assertNotIn("Traceback", output)
                self.assertEqual(world.categories(), [])
                self.assertEqual(len(world.patch_bodies), 1, "it should not try again or restore")

    def test_case5_a_lost_connection_when_adding_is_explained(self):
        world = PretendOutlook([message("msg-1")], patch_errors={1: urllib.error.URLError("down")})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Outlook", output)
        self.assertEqual(world.categories(), [])

    # ---- Case 6: removed again even if the confirm step fails ---------------------

    def assert_restored_after_failed_confirm(self, world):
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("confirm step failed", output)
        self.assertIn("did not pass", output)
        self.assertIn("put the email back", output)
        self.assertEqual(world.patch_bodies, [{"categories": [CATEGORY]}, {"categories": []}])
        self.assertEqual(world.categories(), [])
        self.assertNotIn("Write check passed", output)

    def test_case6_confirm_error_still_removes_the_category(self):
        self.assert_restored_after_failed_confirm(PretendOutlook([message("msg-1")], confirm="error500"))

    def test_case6_category_missing_when_read_back_still_removes_it(self):
        self.assert_restored_after_failed_confirm(PretendOutlook([message("msg-1")], confirm="hidden"))

    def test_case6_lost_connection_at_confirm_still_removes_it(self):
        self.assert_restored_after_failed_confirm(PretendOutlook([message("msg-1")], confirm="unreachable"))

    # ---- Case 7: the category cannot be removed ----------------------------------------

    def test_case7_failed_removal_is_reported_and_is_not_a_pass(self):
        world = PretendOutlook([message("msg-1")], patch_errors={2: http_error(403)})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't remove the category", output)
        self.assertIn("may still be on the email", output)
        self.assertIn("by hand", output)
        self.assertNotIn("Write check passed", output)
        self.assertNotIn("403", output)
        self.assertEqual(world.categories(), [CATEGORY])

    # ---- Case 8: other categories are kept -----------------------------------------------

    def test_case8_other_categories_are_kept_in_order(self):
        world = self.one(["Blue", "Leads"])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(
            world.patch_bodies,
            [{"categories": ["Blue", "Leads", CATEGORY]}, {"categories": ["Blue", "Leads"]}],
        )
        self.assertEqual(world.categories(), ["Blue", "Leads"])

    # ---- Case 9: the email already has "Needs review" ---------------------------------------

    def test_case9_existing_category_stops_the_check(self):
        for existing in (CATEGORY, "needs review"):
            with self.subTest(existing=existing):
                world = self.one(["Blue", existing])
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn("already has the category", output)
                self.assertEqual(world.changes(), [])
                self.assertEqual(world.categories(), ["Blue", existing])

    # ---- Case 10: dry run changes nothing ----------------------------------------------------

    def test_case10_dry_run_says_what_it_would_do_and_sends_no_changes(self):
        world = self.one(["Blue"])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertIn(SUBJECT, output)
        self.assertIn("would add the category Needs review to 1 email", output)
        self.assertIn("confirm it, then remove it again", output)
        self.assertEqual(world.changes(), [])
        self.assertEqual(set(world.methods()), {"GET"})
        self.assertEqual(world.categories(), ["Blue"])

    def test_case10_dry_run_needs_no_slack_link(self):
        world = self.one()
        code, _ = self.run_main(world, [], remove=["SLACK_WEBHOOK_URL"])
        self.assertEqual(code, 0)

    def test_default_is_a_dry_run(self):
        world = self.one()
        code, output = self.run_main(world, [])
        self.assertIn("Dry run", output)
        self.assertNotIn("Write check passed", output)
        self.assertEqual(world.patch_bodies, [])

    # ---- Case 11: nothing is moved, deleted, sent or replied to -------------------------------

    def test_case11_only_two_category_updates_on_one_email(self):
        world = PretendOutlook([message("msg-1"), message("decoy", subject="Hello", categories=["Red"])])
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        changes = world.changes()
        self.assertEqual(len(changes), 2)
        for request in changes:
            self.assertEqual(request.get_method(), "PATCH")
            self.assertTrue(request.full_url.split("?")[0].endswith("/me/messages/msg-1"))
        for body in world.patch_bodies:
            self.assertEqual(set(body), {"categories"})
        self.assertEqual(world.categories("decoy"), ["Red"])
        for request in world.requests:
            url = request.full_url.lower()
            for word in ("/move", "/copy", "/send", "/reply", "/forward", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, url)
            self.assertNotIn("decoy", url)
            self.assertNotEqual(request.get_method(), "DELETE")

    # ---- Case 12: the token is never shown ----------------------------------------------------------

    def test_case12_token_is_sent_to_outlook_but_never_printed(self):
        world = self.one()
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.requests[0].get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertNotIn(FAKE_TOKEN, output)

    def test_case12_token_is_not_printed_when_things_fail(self):
        scenarios = [
            PretendOutlook([message("msg-1")], patch_errors={1: http_error(403)}),
            PretendOutlook([message("msg-1")], confirm="error500"),
            PretendOutlook([message("msg-1")], patch_errors={2: http_error(403)}),
        ]
        for world in scenarios:
            code, output = self.run_main(world, ["--live"])
            self.assertEqual(code, 1)

    def test_case12_env_file_is_left_alone(self):
        before = self.env_path.read_bytes()
        self.run_main(self.one(), ["--live"])
        self.assertEqual(self.env_path.read_bytes(), before)

    # ---- Case 13: sign-in missing or Outlook unreachable -------------------------------------------

    def test_case13_missing_client_id_stops_before_any_request(self):
        self.env_path.write_text("OTHER=1\n", encoding="utf-8")
        world = self.one()
        code, output = self.run_main(world, ["--live"], remove=["MS_CLIENT_ID"])
        self.assertEqual(code, 1)
        self.assertIn("MS_CLIENT_ID", output)
        self.assertEqual(world.requests, [])

    def test_case13_missing_saved_sign_in_says_to_run_outlook_signin(self):
        self.env_path.write_text("MS_CLIENT_ID=fake-from-file\n", encoding="utf-8")
        world = self.one()
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        env = {"MS_CLIENT_ID": "fake-client-id"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["write_check.py", "--live"]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen):
            os.environ.pop("MS_TOKEN_CACHE", None)
            code = write_check.main()
        output = buffer.getvalue().decode("utf-8")
        self.assertEqual(code, 1)
        self.assertIn("MS_TOKEN_CACHE", output)
        self.assertIn("outlook_signin.py", output)
        self.assertEqual(world.requests, [])

    def test_case13_saved_sign_in_that_no_longer_works(self):
        error = outlook_signin.SetupProblem("Your saved sign-in no longer works (reason: x). Please run outlook_signin.py again.")
        world = self.one()
        code, output = self.run_main(world, ["--live"], token_error=error)
        self.assertEqual(code, 1)
        self.assertIn("no longer works", output)
        self.assertEqual(world.requests, [])

    def test_case13_no_internet_is_explained_and_nothing_changes(self):
        world = PretendOutlook([message("msg-1")], unreachable=True)
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Outlook", output)
        self.assertNotIn("URLError", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual(world.categories(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
