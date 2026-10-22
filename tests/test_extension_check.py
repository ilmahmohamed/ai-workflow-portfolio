"""Pretend-data checks for workflows/01-outlook-to-slack/extension_check.py.

These follow tests/cases/09-extension-check.md.
Everything here is fake and self-contained:
  - a pretend Outlook answers in place of the real one,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened).

Run:  python tests\\test_extension_check.py
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

import extension_check  # noqa: E402
import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_ENV = {"MS_CLIENT_ID": "fake-client-id", "MS_TOKEN_CACHE": FAKE_CACHE}
SECRETS = (FAKE_TOKEN, FAKE_CACHE)

SUBJECT = "FAKE EXTENSION CHECK - delete me - not for sending"
DRAFTS_MESSAGES = "/me/mailFolders/drafts/messages"


def draft(message_id, subject=SUBJECT):
    return {"id": message_id, "subject": subject, "folder": "drafts", "isDraft": True, "extensions": []}


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
    """A pretend mailbox with a Drafts folder and a bin. Records every request made to it."""

    def __init__(self, drafts=(), errors=None, move_keeps="yes", unreachable_at=None, unreachable=False):
        self.messages = {d["id"]: copy.deepcopy(d) for d in drafts}
        self.errors = errors or {}                 # {"draft" | "extension" | "move": error to raise}
        self.move_keeps = move_keeps               # yes, no (lost in the move), changed
        self.unreachable_at = unreachable_at       # "extension" or "move": the connection drops there
        self.unreachable = unreachable
        self.requests = []
        self.bodies = []
        self.unexpected = []
        self.moves = 0

    def fail(self, step):
        if self.unreachable_at == step:
            raise urllib.error.URLError("pretend: connection lost")
        if step in self.errors:
            raise self.errors[step]

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        if self.unreachable:
            raise urllib.error.URLError("pretend: no internet")
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path
        body = json.loads(request.data.decode()) if request.data else None

        if method == "GET" and path.endswith(DRAFTS_MESSAGES):
            drafts = [m for m in self.messages.values() if m["folder"] == "drafts"]
            return FakeResponse(json.dumps({"value": copy.deepcopy(drafts)}).encode())

        if method == "POST" and path.endswith(DRAFTS_MESSAGES):
            self.bodies.append(body)
            self.fail("draft")
            new_id = f"new-{len(self.messages) + 1}"
            self.messages[new_id] = {
                "id": new_id, "subject": body["subject"], "folder": "drafts", "isDraft": True, "extensions": [],
            }
            return FakeResponse(json.dumps(self.messages[new_id]).encode())

        found = re.search(r"/me/messages/([^/]+)/extensions$", path)
        if found and method == "POST":
            self.bodies.append(body)
            self.fail("extension")
            message = self.messages[urllib.parse.unquote(found.group(1))]
            message["extensions"].append({
                "@odata.type": "#microsoft.graph.openTypeExtension",
                "id": "Microsoft.OutlookServices.OpenTypeExtension." + body["extensionName"],
                **{k: v for k, v in body.items() if k != "@odata.type"},
            })
            return FakeResponse(json.dumps(message["extensions"][-1]).encode())

        found = re.search(r"/me/messages/([^/]+)/move$", path)
        if found and method == "POST":
            self.bodies.append(body)
            self.fail("move")
            old_id = urllib.parse.unquote(found.group(1))
            message = self.messages.pop(old_id)
            self.moves += 1
            message["id"] = f"moved-{self.moves}"
            message["folder"] = body["destinationId"]
            if self.move_keeps == "no":
                message["extensions"] = []
            elif self.move_keeps == "changed":
                for extension in message["extensions"]:
                    extension["data"] = extension["data"].replace("Fake Person", "Other Person")
            self.messages[message["id"]] = message
            return FakeResponse(json.dumps({"id": message["id"], "subject": message["subject"]}).encode())

        found = re.search(r"/me/messages/([^/]+)/extensions/([^/]+)$", path)
        if found and method == "GET":
            # Strict, like the real Outlook: only the documented form (one extension, read by its
            # name, no query string) is answered. Anything else gets the 400 the real one gave.
            if urllib.parse.urlparse(request.full_url).query:
                raise http_error(400)
            self.fail("read")
            message = self.messages.get(urllib.parse.unquote(found.group(1)))
            wanted = urllib.parse.unquote(found.group(2))
            for extension in (message or {}).get("extensions", []):
                if extension["extensionName"] == wanted:
                    return FakeResponse(json.dumps(copy.deepcopy(extension)).encode())
            raise http_error(404)

        if method == "GET" and "$expand" in urllib.parse.urlparse(request.full_url).query:
            self.unexpected.append(f"{method} {request.full_url}")
            raise http_error(400)

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]

    def in_folder(self, folder):
        return [m for m in self.messages.values() if m["folder"] == folder]


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


class ExtensionCheckTests(unittest.TestCase):
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
            token_patch = mock.patch.object(extension_check, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(extension_check, "get_access_token", return_value=FAKE_TOKEN)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["extension_check.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen), \
                token_patch:
            for name in remove:
                os.environ.pop(name, None)
            code = extension_check.main()
        output = buffer.getvalue().decode("utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a token was printed on screen")
        self.assertEqual(world.unexpected, [], "the script sent an unexpected request")
        return code, output

    # ---- Case 1: dry run changes nothing -------------------------------------

    def test_case1_dry_run_says_what_it_would_do_and_sends_no_changes(self):
        world = PretendOutlook([draft("d1", subject="Something else")])
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertIn(SUBJECT, output)
        for step in ("create 1 fake draft", "save a pretend answer", "move it to Deleted Items", "read the saved answer back"):
            self.assertIn(step, output)
        self.assertIn("Nothing is sent", output)
        self.assertEqual(world.changes(), [])
        self.assertEqual(set(world.methods()), {"GET"})
        self.assertEqual(list(world.messages), ["d1"])

    def test_default_is_a_dry_run(self):
        world = PretendOutlook()
        code, output = self.run_main(world, [])
        self.assertIn("Dry run", output)
        self.assertNotIn("Extension check passed", output)
        self.assertEqual(world.bodies, [])

    # ---- Case 2: the data survives the move -------------------------------------

    def test_case2_live_passes_when_the_data_survives(self):
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.methods(), ["GET", "POST", "POST", "GET", "POST", "GET"])
        paths = [urllib.parse.urlparse(r.full_url).path for r in world.requests]
        self.assertTrue(paths[1].endswith(DRAFTS_MESSAGES))
        self.assertTrue(paths[2].endswith("/extensions"))
        self.assertTrue(paths[4].endswith("/move"))
        self.assertIn("moved-1", paths[5], "the last read must use the draft's NEW id")
        for index in (3, 5):
            self.assertTrue(paths[index].endswith("/extensions/com.novawellness.leadresult"))
            self.assertNotIn("?", world.requests[index].full_url, "the read must have no query string")
        self.assertIn("Extension check passed", output)
        self.assertIn("stayed on the email after it was moved", output)
        self.assertIn("Deleted Items", output)
        self.assertEqual(len(world.in_folder("deleteditems")), 1)
        self.assertEqual(world.in_folder("drafts"), [])

    def test_case2_the_move_goes_only_to_deleted_items(self):
        world = PretendOutlook()
        self.run_main(world, ["--live"])
        moves = [b for b in world.bodies if b and "destinationId" in b]
        self.assertEqual(moves, [{"destinationId": "deleteditems"}])

    def test_case2_the_saved_extension_has_the_fixed_name_and_shape(self):
        world = PretendOutlook()
        self.run_main(world, ["--live"])
        body = next(b for b in world.bodies if b and "extensionName" in b)
        self.assertEqual(set(body), {"@odata.type", "extensionName", "version", "data"})
        self.assertEqual(body["extensionName"], "com.novawellness.leadresult")
        self.assertEqual(body["version"], 1)
        answer = json.loads(body["data"])
        self.assertIn("is_lead", answer)
        self.assertIn("category", answer)
        self.assertTrue(len(body["data"]) > 500, "it should be about the size of a real answer")
        self.assertIn("example.com", body["data"])
        self.assertIn("demostración", body["data"])

    # ---- Case 3: the data is lost in the move --------------------------------------

    def test_case3_a_lost_extension_is_reported_plainly_and_is_not_a_pass(self):
        world = PretendOutlook(move_keeps="no")
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("did NOT survive the move", output)
        self.assertIn("another place", output)
        self.assertNotIn("Extension check passed", output)
        self.assertIn("Deleted Items", output)

    # ---- Case 4: the saved data comes back changed -----------------------------------

    def test_case4_data_that_comes_back_different_after_the_move_is_not_a_pass(self):
        world = PretendOutlook(move_keeps="changed")
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("came back different", output)
        self.assertNotIn("Extension check passed", output)

    def test_case4_data_that_is_missing_before_the_move_is_not_a_pass_and_nothing_is_moved(self):
        world = PretendOutlook()
        real = world.urlopen

        def hide_extension(request, timeout=None):
            path = urllib.parse.urlparse(request.full_url).path
            if request.get_method() == "GET" and re.search(r"/me/messages/new-1/extensions/", path):
                real(request, timeout)       # recorded, but the answer is "not found"
                raise http_error(404)
            return real(request, timeout)

        world.urlopen = hide_extension
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("did not pass", output)
        self.assertEqual(world.moves, 0)
        self.assertIn("Drafts", output)

    # ---- Case 5: a leftover fake draft stops the check ----------------------------------

    def test_case5_a_leftover_stops_the_check(self):
        for argv in ([], ["--live"]):
            with self.subTest(argv=argv):
                world = PretendOutlook([draft("old", subject="  " + SUBJECT + "  ")])
                code, output = self.run_main(world, argv)
                self.assertEqual(code, 1)
                self.assertIn("already in your Drafts", output)
                self.assertIn("delete it by hand", output)
                self.assertIn("Nothing was created", output)
                self.assertEqual(world.changes(), [])

    def test_case5_similar_subjects_do_not_count_and_are_left_alone(self):
        near = ["fake extension check - delete me - not for sending", SUBJECT + " 2", "Re: " + SUBJECT]
        world = PretendOutlook([draft(f"n{i}", subject=s) for i, s in enumerate(near)])
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.in_folder("drafts")), 3)
        for i in range(3):
            self.assertEqual(world.messages[f"n{i}"]["extensions"], [])

    # ---- Case 6: Outlook refuses a step -----------------------------------------------------

    def test_case6_a_refused_step_is_named_and_says_where_the_draft_may_be(self):
        scenarios = [
            ("draft", "create the draft", "Nothing was created"),
            ("extension", "save the data on the draft", "may be in your Drafts folder"),
            ("move", "move the draft to Deleted Items", "may be in your Drafts or Deleted Items"),
        ]
        for step, name, where in scenarios:
            for code_number in (401, 403):
                with self.subTest(step=step, code=code_number):
                    world = PretendOutlook(errors={step: http_error(code_number)})
                    code, output = self.run_main(world, ["--live"])
                    self.assertEqual(code, 1)
                    self.assertIn(f"Outlook refused to {name}", output)
                    self.assertIn("sign-in may need redoing", output)
                    self.assertIn("outlook_signin.py", output)
                    self.assertIn(where, output)
                    self.assertNotIn(str(code_number), output)
                    self.assertNotIn("HTTPError", output)
                    self.assertNotIn("Traceback", output)
                    self.assertNotIn("Extension check passed", output)
                    self.assertEqual(len([b for b in world.bodies]), {"draft": 1, "extension": 2, "move": 3}[step],
                                     "it should not try again")

    def test_case6_a_failed_read_back_is_reported_and_is_not_taken_for_not_found(self):
        cases = ((500, "refused the request"), (403, "didn't accept the sign-in"), (401, "didn't accept the sign-in"))
        for code_number, words in cases:
            with self.subTest(code=code_number):
                world = PretendOutlook(errors={"read": http_error(code_number)})
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn(words, output)
                self.assertIn("may be in your Drafts folder", output)
                self.assertNotIn("did NOT survive", output)
                self.assertNotIn("Extension check passed", output)
                self.assertEqual(world.moves, 0, "nothing is moved after a failed read")
                self.assertNotIn("Traceback", output)

    def test_case6_a_lost_connection_partway_says_so_and_where_the_draft_may_be(self):
        for step, where in (("extension", "Drafts folder"), ("move", "Drafts or Deleted Items")):
            with self.subTest(step=step):
                world = PretendOutlook(unreachable_at=step)
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn("couldn't reach Outlook", output)
                self.assertIn(where, output)
                self.assertNotIn("URLError", output)

    # ---- Case 7: nothing is ever sent, replied to, forwarded, copied or deleted --------------------

    def test_case7_the_only_changes_are_create_extension_and_move(self):
        world = PretendOutlook([draft("d1", subject="Hello"), draft("d2", subject="Other")])
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        changes = world.changes()
        self.assertEqual([r.get_method() for r in changes], ["POST", "POST", "POST"])
        self.assertEqual(len(changes), 3)
        for request in world.requests:
            url = request.full_url.lower()
            for word in ("/send", "/reply", "/forward", "/copy", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, url)
            self.assertNotIn("/d1", url)
            self.assertNotIn("/d2", url)
            self.assertNotIn(request.get_method(), ("DELETE", "PATCH", "PUT"))
        self.assertEqual(world.messages["d1"]["folder"], "drafts")
        self.assertEqual(world.messages["d2"]["folder"], "drafts")

    def good_draft(self):
        return {"subject": SUBJECT, "body": {"contentType": "Text", "content": "fake"}}

    def good_extension(self):
        return {
            "@odata.type": "microsoft.graph.openTypeExtension",
            "extensionName": "com.novawellness.leadresult",
            "version": 1,
            "data": "{}",
        }

    def test_case7_the_request_guard_refuses_everything_else(self):
        good_ext = self.good_extension()
        refused = [
            ("POST", "/me/messages/abc/send", {}),
            ("POST", "/me/sendMail", {"message": {}}),
            ("POST", "/me/messages/abc/reply", {}),
            ("POST", "/me/messages/abc/replyAll", {}),
            ("POST", "/me/messages/abc/forward", {}),
            ("POST", "/me/messages/abc/createReply", {}),
            ("POST", "/me/messages/abc/copy", {"destinationId": "deleteditems"}),
            ("POST", "/me/messages/abc/move", {"destinationId": "inbox"}),
            ("POST", "/me/messages/abc/move", {"destinationId": "drafts"}),
            ("POST", "/me/messages/abc/move", {"destinationId": "deleteditems", "extra": 1}),
            ("POST", "/me/messages/abc/move", None),
            ("POST", "/me/mailFolders/inbox/messages", self.good_draft()),
            ("POST", DRAFTS_MESSAGES, None),
            ("POST", DRAFTS_MESSAGES, {**self.good_draft(), "toRecipients": []}),
            ("POST", DRAFTS_MESSAGES, {"subject": SUBJECT}),
            ("POST", "/me/messages/abc/extensions", {**good_ext, "extensionName": "com.other.app"}),
            ("POST", "/me/messages/abc/extensions", {**good_ext, "isRead": True}),
            ("POST", "/me/messages/abc/extensions", {**good_ext, "data": {"a": 1}}),
            ("POST", "/me/messages/abc/extensions", {**good_ext, "version": True}),
            ("POST", "/me/messages/abc/extensions", {**good_ext, "version": 2}),
            ("POST", "/me/messages/abc/extensions", None),
            ("DELETE", "/me/messages/abc", None),
            ("DELETE", "/me/messages/abc/extensions/com.novawellness.leadresult", None),
            ("PATCH", "/me/messages/abc", {"isRead": True}),
            ("PUT", DRAFTS_MESSAGES, self.good_draft()),
            ("GET", "/me/messages", None),
            ("GET", "/me/messages/abc", None),
            ("GET", "/me/messages/abc/extensions", None),
            ("GET", "/me/messages/abc/extensions/com.other.app", None),
            ("GET", "/me/messages/abc/extensions/com.novawellness.leadresult/extra", None),
            ("GET", "/me/mailFolders/inbox/messages", None),
            ("GET", "/me/messages/abc/attachments", None),
            ("GET", "/me/messages/abc/send", None),
            ("GET", "/me/mailFolders/deleteditems/messages", None),
        ]
        for method, path, body in refused:
            with self.subTest(method=method, path=path, body=body):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(extension_check.BlockedRequest):
                        extension_check.graph(FAKE_TOKEN, method, path, body=body)
                self.assertEqual(calls, [], "a refused request must never leave the script")

    def test_case7_reading_the_extension_with_any_query_string_is_refused(self):
        path = "/me/messages/abc/extensions/com.novawellness.leadresult"
        for params in ({"$expand": "extensions"}, {"$select": "id"}):
            with self.subTest(params=params):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(extension_check.BlockedRequest):
                        extension_check.graph(FAKE_TOKEN, "GET", path, params=params)
                self.assertEqual(calls, [])

    def test_case7_the_request_guard_lets_the_wanted_requests_through(self):
        allowed = [
            ("GET", DRAFTS_MESSAGES, None),
            ("GET", "/me/messages/abc%3D/extensions/com.novawellness.leadresult", None),
            ("POST", DRAFTS_MESSAGES, self.good_draft()),
            ("POST", "/me/messages/abc%3D/extensions", self.good_extension()),
            ("POST", "/me/messages/abc%3D/move", {"destinationId": "deleteditems"}),
        ]
        for method, path, body in allowed:
            with self.subTest(method=method, path=path):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    extension_check.graph(FAKE_TOKEN, method, path, body=body)
                self.assertEqual(len(calls), 1)

    def test_case7_the_script_text_has_no_send_reply_forward_copy_or_delete(self):
        text = (WORKFLOWS / "01-outlook-to-slack" / "extension_check.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreply",
            r"/forward", r"createforward", r"/copy", r"permanentdelete",
            r"\"delete\"", r"\"patch\"", r"\"put\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in extension_check.py")

    def test_case7_the_permission_list_is_still_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])
        self.assertEqual(outlook_to_slack.SCOPES, ["Mail.ReadWrite"])
        self.assertIs(extension_check.get_access_token, outlook_to_slack.get_access_token)

    # ---- Case 8: the token is never shown ------------------------------------------------------------

    def test_case8_token_is_sent_to_outlook_but_never_printed(self):
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(world.requests[0].get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertNotIn(FAKE_TOKEN, output)

    def test_case8_token_is_not_printed_when_things_fail(self):
        scenarios = [
            PretendOutlook(errors={"draft": http_error(403)}),
            PretendOutlook(move_keeps="no"),
            PretendOutlook([draft("old")]),
        ]
        for world in scenarios:
            code, _ = self.run_main(world, ["--live"])
            self.assertEqual(code, 1)

    def test_case8_env_file_is_left_alone(self):
        before = self.env_path.read_bytes()
        self.run_main(PretendOutlook(), ["--live"])
        self.assertEqual(self.env_path.read_bytes(), before)

    # ---- Case 9: sign-in missing or Outlook unreachable ----------------------------------------------------

    def test_case9_missing_client_id_stops_before_any_request(self):
        self.env_path.write_text("OTHER=1\n", encoding="utf-8")
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"], remove=["MS_CLIENT_ID"])
        self.assertEqual(code, 1)
        self.assertIn("MS_CLIENT_ID", output)
        self.assertEqual(world.requests, [])

    def test_case9_missing_saved_sign_in_says_to_run_outlook_signin(self):
        world = PretendOutlook()
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        with mock.patch.dict(os.environ, {"MS_CLIENT_ID": "fake-client-id"}), \
                mock.patch.object(sys, "argv", ["extension_check.py", "--live"]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", world.urlopen):
            os.environ.pop("MS_TOKEN_CACHE", None)
            code = extension_check.main()
        output = buffer.getvalue().decode("utf-8")
        self.assertEqual(code, 1)
        self.assertIn("MS_TOKEN_CACHE", output)
        self.assertIn("outlook_signin.py", output)
        self.assertEqual(world.requests, [])

    def test_case9_saved_sign_in_that_no_longer_works(self):
        error = outlook_signin.SetupProblem(
            "Your saved sign-in no longer works (reason: x). Please run outlook_signin.py again."
        )
        world = PretendOutlook()
        code, output = self.run_main(world, ["--live"], token_error=error)
        self.assertEqual(code, 1)
        self.assertIn("no longer works", output)
        self.assertEqual(world.requests, [])

    def test_case9_no_internet_is_explained_and_nothing_is_created(self):
        for argv in ([], ["--live"]):
            with self.subTest(argv=argv):
                world = PretendOutlook(unreachable=True)
                code, output = self.run_main(world, argv)
                self.assertEqual(code, 1)
                self.assertIn("couldn't reach Outlook", output)
                self.assertNotIn("URLError", output)
                self.assertNotIn("Traceback", output)
                self.assertEqual(world.messages, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
