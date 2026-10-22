"""Pretend-data checks for workflows/01-outlook-to-slack/draft_check.py.

These follow tests/cases/07-draft-check.md.
Everything here is fake and self-contained:
  - a pretend Outlook answers in place of the real one,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened).

Run:  python tests\\test_draft_check.py
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
WORKFLOWS = PROJECT_ROOT / "workflows"
sys.path.insert(0, str(WORKFLOWS / "01-outlook-to-slack"))

import draft_check  # noqa: E402
import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_ENV = {"MS_CLIENT_ID": "fake-client-id", "MS_TOKEN_CACHE": FAKE_CACHE}
SECRETS = (FAKE_TOKEN, FAKE_CACHE)

SUBJECT = "FAKE DRAFT - delete me - not for sending"
DRAFTS_ID = "drafts-folder-id"
DRAFTS_MESSAGES = "/me/mailFolders/drafts/messages"


def draft(message_id, subject=SUBJECT, recipients=()):
    return {
        "id": message_id,
        "subject": subject,
        "isDraft": True,
        "parentFolderId": DRAFTS_ID,
        "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
    }


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
    """A pretend Drafts folder. Records every request made to it."""

    def __init__(self, drafts=(), post_error=None, confirm="ok", unreachable=False):
        self.drafts = {d["id"]: copy.deepcopy(d) for d in drafts}
        self.post_error = post_error     # an error raised when the draft is created
        self.confirm = confirm           # ok, error500, unreachable, no_id, not_draft, wrong_folder, has_recipient
        self.unreachable = unreachable
        self.requests = []
        self.post_bodies = []
        self.unexpected = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        if self.unreachable:
            raise urllib.error.URLError("pretend: no internet")
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path

        if method == "GET" and path.endswith(DRAFTS_MESSAGES):
            # Like a sloppy search: hand back every draft, the script must pick the exact ones.
            return FakeResponse(json.dumps({"value": copy.deepcopy(list(self.drafts.values()))}).encode())

        if method == "GET" and path.endswith("/me/mailFolders/drafts"):
            return FakeResponse(json.dumps({"id": DRAFTS_ID}).encode())

        if method == "POST" and path.endswith(DRAFTS_MESSAGES):
            body = json.loads(request.data.decode())
            self.post_bodies.append(body)
            if self.post_error is not None:
                raise self.post_error
            new_id = f"new-{len(self.drafts) + 1}"
            created = draft(new_id, subject=body.get("subject", ""))
            created["toRecipients"] = list(body.get("toRecipients", []))
            if self.confirm == "not_draft":
                created["isDraft"] = False
            if self.confirm == "wrong_folder":
                created["parentFolderId"] = "some-other-folder"
            if self.confirm == "has_recipient":
                created["toRecipients"] = [{"emailAddress": {"address": "someone@example.com"}}]
            self.drafts[new_id] = created
            if self.confirm == "no_id":
                return FakeResponse(b"{}")
            return FakeResponse(json.dumps(created).encode())

        found = re.search(r"/me/messages/([^/]+)$", path)
        if found and method == "GET":
            current = self.drafts.get(urllib.parse.unquote(found.group(1)))
            if current is not None:
                if self.confirm == "error500":
                    raise http_error(500)
                if self.confirm == "unreachable":
                    raise urllib.error.URLError("pretend: no internet")
                return FakeResponse(json.dumps(current).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]

    def subjects(self):
        return sorted(d["subject"] for d in self.drafts.values())


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


class DraftCheckTests(unittest.TestCase):
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
            token_patch = mock.patch.object(draft_check, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(draft_check, "get_access_token", return_value=FAKE_TOKEN)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["draft_check.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen), \
                token_patch:
            for name in remove:
                os.environ.pop(name, None)
            code = draft_check.main()
        output = buffer.getvalue().decode("utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a token was printed on screen")
        self.assertEqual(world.unexpected, [], "the script sent an unexpected request")
        return code, output

    # ---- Case 1: dry run changes nothing -----------------------------------

    def test_case1_dry_run_says_what_it_would_do_and_sends_no_changes(self):
        world = PretendOutlook([draft("d1", subject="Something else")])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertIn(SUBJECT, output)
        self.assertIn("would create 1 draft", output)
        self.assertIn("no recipient", output)
        self.assertIn("Nothing is sent", output)
        self.assertEqual(world.changes(), [])
        self.assertEqual(set(world.methods()), {"GET"})
        self.assertEqual(world.subjects(), ["Something else"])

    def test_default_is_a_dry_run(self):
        world = PretendOutlook()
        code, output = self.run_main(world, [])
        self.assertIn("Dry run", output)
        self.assertNotIn("Draft check passed", output)
        self.assertEqual(world.post_bodies, [])
        self.assertEqual(world.drafts, {})

    # ---- Case 2: live creates exactly one draft in Drafts -------------------

    def test_case2_live_creates_one_draft_confirms_it_and_leaves_it(self):
        world = PretendOutlook([draft("d1", subject="Something else")])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.post_bodies), 1)
        self.assertEqual(world.subjects(), sorted(["Something else", SUBJECT]))
        self.assertEqual(world.methods(), ["GET", "POST", "GET", "GET"])
        self.assertIn("Draft check passed", output)
        self.assertIn("NOT sent", output)
        self.assertIn("by hand", output)
        self.assertNotIn("DELETE", world.methods())

    # ---- Case 3: obviously fake, no recipient ---------------------------------

    def test_case3_the_request_has_only_a_fake_subject_and_a_plain_text_body(self):
        world = PretendOutlook()
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        body = world.post_bodies[0]
        self.assertEqual(set(body), {"subject", "body"})
        self.assertEqual(body["subject"], SUBJECT)
        self.assertEqual(set(body["body"]), {"contentType", "content"})
        self.assertEqual(body["body"]["contentType"], "Text")
        text = body["body"]["content"].lower()
        self.assertIn("fake", text)
        self.assertIn("delete", text)
        for key in ("toRecipients", "ccRecipients", "bccRecipients", "from", "sender"):
            self.assertNotIn(key, body)

    # ---- Case 4: a leftover fake draft stops the check -------------------------

    def test_case4_a_leftover_fake_draft_stops_the_check(self):
        for argv in ([], ["--live"]):
            with self.subTest(argv=argv):
                world = PretendOutlook([draft("old")])
                code, output = self.run_main(world, argv)
                self.assertEqual(code, 1)
                self.assertIn("already in your Drafts", output)
                self.assertIn("delete it by hand", output)
                self.assertIn("Nothing was created", output)
                self.assertEqual(world.changes(), [])
                self.assertEqual(list(world.drafts), ["old"])

    def test_case4_similar_subjects_do_not_count_and_are_left_alone(self):
        near = [
            "fake draft - delete me - not for sending",
            SUBJECT + " 2",
            "Re: " + SUBJECT,
        ]
        world = PretendOutlook([draft(f"n{i}", subject=s) for i, s in enumerate(near)])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.subjects(), sorted(near + [SUBJECT]))
        self.assertEqual(len(world.post_bodies), 1)

    def test_case4_extra_spaces_around_the_subject_still_count_as_a_leftover(self):
        world = PretendOutlook([draft("old", subject="  " + SUBJECT + "  ")])
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("already in your Drafts", output)
        self.assertEqual(world.changes(), [])

    # ---- Case 5: Outlook refuses to create the draft ------------------------------

    def test_case5_refused_create_says_the_sign_in_may_need_redoing(self):
        for code_number in (401, 403):
            with self.subTest(code=code_number):
                world = PretendOutlook(post_error=http_error(code_number))
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn("Outlook refused to create the draft", output)
                self.assertIn("sign-in may need redoing", output)
                self.assertIn("outlook_signin.py", output)
                self.assertIn("Nothing was created", output)
                self.assertNotIn(str(code_number), output)
                self.assertNotIn("HTTPError", output)
                self.assertNotIn("Traceback", output)
                self.assertEqual(world.drafts, {})
                self.assertEqual(len(world.post_bodies), 1, "it should not try again")

    def test_case5_a_lost_connection_when_creating_is_explained(self):
        world = PretendOutlook(post_error=urllib.error.URLError("down"))
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Outlook", output)
        self.assertNotIn("URLError", output)
        self.assertEqual(world.drafts, {})

    # ---- Case 6: the confirm step fails ----------------------------------------------

    def test_case6_a_failed_confirm_is_not_a_pass_and_deletes_nothing(self):
        for confirm in ("error500", "unreachable", "no_id", "not_draft", "wrong_folder", "has_recipient"):
            with self.subTest(confirm=confirm):
                world = PretendOutlook(confirm=confirm)
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn("confirm step failed", output)
                self.assertIn("did not pass", output)
                self.assertIn("may be in your Drafts", output)
                self.assertIn("by hand", output)
                self.assertNotIn("Draft check passed", output)
                self.assertEqual(len(world.post_bodies), 1)
                self.assertEqual(len(world.changes()), 1, "only the one create request")
                self.assertNotIn("Traceback", output)

    # ---- Case 7: nothing is ever sent, moved, deleted or changed -------------------------

    def test_case7_the_only_change_is_one_create_in_drafts(self):
        world = PretendOutlook([draft("d1", subject="Hello"), draft("d2", subject="Other")])
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        changes = world.changes()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].get_method(), "POST")
        self.assertTrue(changes[0].full_url.split("?")[0].endswith(DRAFTS_MESSAGES))
        for request in world.requests:
            url = request.full_url.lower()
            for word in ("/move", "/copy", "/send", "/reply", "/forward", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, url)
            self.assertNotIn("/d1", url)
            self.assertNotIn("/d2", url)
            self.assertNotIn(request.get_method(), ("DELETE", "PATCH", "PUT"))
        self.assertEqual(world.drafts["d1"]["subject"], "Hello")

    def test_case7_the_request_guard_refuses_everything_else(self):
        good_body = {"subject": SUBJECT, "body": {"contentType": "Text", "content": "fake"}}
        refused = [
            ("POST", "/me/messages/abc/send", {}),
            ("POST", "/me/sendMail", {"message": {}}),
            ("POST", "/me/messages/abc/reply", {}),
            ("POST", "/me/messages/abc/replyAll", {}),
            ("POST", "/me/messages/abc/forward", {}),
            ("POST", "/me/messages/abc/createReply", {}),
            ("POST", "/me/messages/abc/move", {"destinationId": "x"}),
            ("POST", "/me/messages/abc/copy", {"destinationId": "x"}),
            ("POST", "/me/mailFolders/inbox/messages", good_body),
            ("POST", "/me/messages", good_body),
            ("POST", DRAFTS_MESSAGES, None),
            ("POST", DRAFTS_MESSAGES, {**good_body, "toRecipients": []}),
            ("POST", DRAFTS_MESSAGES, {**good_body, "isRead": True}),
            ("POST", DRAFTS_MESSAGES, {"subject": SUBJECT}),
            ("DELETE", "/me/messages/abc", None),
            ("PATCH", "/me/messages/abc", {"isRead": True}),
            ("PUT", DRAFTS_MESSAGES, good_body),
            ("GET", "/me/messages", None),
            ("GET", "/me/mailFolders/inbox/messages", None),
            ("GET", "/me/messages/abc/attachments", None),
            ("GET", "/me/messages/abc/send", None),
            ("GET", "/me/mailFolders/drafts/childFolders", None),
        ]
        for method, path, body in refused:
            with self.subTest(method=method, path=path, body=body):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(draft_check.BlockedRequest):
                        draft_check.graph(FAKE_TOKEN, method, path, body=body)
                self.assertEqual(calls, [], "a refused request must never leave the script")

    def test_case7_the_request_guard_lets_the_wanted_requests_through(self):
        good_body = {"subject": SUBJECT, "body": {"contentType": "Text", "content": "fake"}}
        allowed = [
            ("GET", DRAFTS_MESSAGES, None),
            ("GET", "/me/mailFolders/drafts", None),
            ("GET", "/me/messages/abc%3D", None),
            ("POST", DRAFTS_MESSAGES, good_body),
        ]
        for method, path, body in allowed:
            with self.subTest(method=method, path=path):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    draft_check.graph(FAKE_TOKEN, method, path, body=body)
                self.assertEqual(len(calls), 1)

    def test_case7_the_script_text_has_no_send_reply_forward_move_or_delete(self):
        text = (WORKFLOWS / "01-outlook-to-slack" / "draft_check.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreply",
            r"/forward", r"createforward", r"/move", r"/copy", r"permanentdelete",
            r"\"delete\"", r"\"patch\"", r"\"put\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in draft_check.py")

    # ---- Case 8: the permission stays Mail.ReadWrite -----------------------------------------

    def test_case8_the_permission_list_is_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])
        self.assertEqual(outlook_to_slack.SCOPES, ["Mail.ReadWrite"])

    def test_case8_no_script_asks_for_the_send_permission_or_names_a_send_address(self):
        scripts = sorted(WORKFLOWS.rglob("*.py"))
        self.assertTrue(scripts)
        for script in scripts:
            text = script.read_text(encoding="utf-8").lower()
            for pattern in (r"mail\.send", r"sendmail", r"/send(?![a-z])"):
                with self.subTest(script=script.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text))

    def test_case8_the_script_gets_its_token_the_same_way_as_the_others(self):
        self.assertIs(draft_check.get_access_token, outlook_to_slack.get_access_token)

    # ---- Case 9: the token is never shown ------------------------------------------------------

    def test_case9_token_is_sent_to_outlook_but_never_printed(self):
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.requests[0].get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertNotIn(FAKE_TOKEN, output)

    def test_case9_token_is_not_printed_when_things_fail(self):
        scenarios = [
            PretendOutlook(post_error=http_error(403)),
            PretendOutlook(confirm="error500"),
            PretendOutlook([draft("old")]),
        ]
        for world in scenarios:
            code, _ = self.run_main(world, ["--live"])
            self.assertEqual(code, 1)

    def test_case9_env_file_is_left_alone(self):
        before = self.env_path.read_bytes()
        self.run_main(PretendOutlook(), ["--live"])
        self.assertEqual(self.env_path.read_bytes(), before)

    # ---- Case 10: sign-in missing or Outlook unreachable ----------------------------------------

    def test_case10_missing_client_id_stops_before_any_request(self):
        self.env_path.write_text("OTHER=1\n", encoding="utf-8")
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"], remove=["MS_CLIENT_ID"])
        self.assertEqual(code, 1)
        self.assertIn("MS_CLIENT_ID", output)
        self.assertEqual(world.requests, [])

    def test_case10_missing_saved_sign_in_says_to_run_outlook_signin(self):
        world = PretendOutlook()
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        with mock.patch.dict(os.environ, {"MS_CLIENT_ID": "fake-client-id"}), \
                mock.patch.object(sys, "argv", ["draft_check.py", "--live"]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen):
            os.environ.pop("MS_TOKEN_CACHE", None)
            code = draft_check.main()
        output = buffer.getvalue().decode("utf-8")
        self.assertEqual(code, 1)
        self.assertIn("MS_TOKEN_CACHE", output)
        self.assertIn("outlook_signin.py", output)
        self.assertEqual(world.requests, [])

    def test_case10_saved_sign_in_that_no_longer_works(self):
        error = outlook_signin.SetupProblem(
            "Your saved sign-in no longer works (reason: x). Please run outlook_signin.py again."
        )
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"], token_error=error)
        self.assertEqual(code, 1)
        self.assertIn("no longer works", output)
        self.assertEqual(world.requests, [])

    def test_case10_no_internet_is_explained_and_nothing_is_created(self):
        for argv in ([], ["--live"]):
            with self.subTest(argv=argv):
                world = PretendOutlook(unreachable=True)
                code, output = self.run_main(world, argv)
                self.assertEqual(code, 1)
                self.assertIn("couldn't reach Outlook", output)
                self.assertNotIn("URLError", output)
                self.assertNotIn("Traceback", output)
                self.assertEqual(world.drafts, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
