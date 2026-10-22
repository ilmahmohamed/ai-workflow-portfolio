"""Pretend-data checks for workflows/02-email-to-attio/reply_draft_check.py (not written yet).

These follow tests/cases/15-reply-draft-check.md. They say what the new one-off script must offer:

  reply_draft_check.TAG          "[DAY8]"
  reply_draft_check.LIST_PARAMS  the one fixed question it asks about a folder (tagged emails only)
  reply_draft_check.graph(token, method, path, params=None)   its own guard (reads the tagged emails of a folder
                                 only); refused requests raise reply_draft_check.BlockedRequest
  reply_draft_check.main()       command line: --only-subject TEXT [--only-from ADDRESS] [--live]; returns 0 or 1

Everything here is fake and self-contained: a strict pretend Outlook, the internet blocked, a throwaway .env,
and any attempt to touch the real .env or to write a file stops the test.

Run:  python tests\\test_reply_draft_check.py
"""

import copy
import io
import json
import os
import re
import socket
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
sys.path.insert(0, str(WORKFLOWS / "02-email-to-attio"))

import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402
import reply_draft_check  # noqa: E402
import v3_reply  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()
FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_ENV = {"MS_CLIENT_ID": "fake-client-id", "MS_TOKEN_CACHE": FAKE_CACHE}
TAG = "[DAY8]"
SENDER = "sam.fake@example.com"
OTHER = "someone.else@example.com"
NAME = "Sam Fake"
LEAD_MARKER = "com.novawellness.leadresult"
DRAFT_MARKER = "com.novawellness.draftmade"
DRAFT_SELECT = "id,isDraft,parentFolderId,toRecipients,ccRecipients,bccRecipients,conversationId,bodyPreview"
LIST_PARAMS = {
    "$filter": ["startswith(subject,'[DAY8]')"],
    "$select": ["id,subject,from,replyTo,conversationId"],
    "$top": ["100"],
}
DRAFTS_ID = "drafts-id"
CLEAN = "Hello Sam, thank you for getting in touch about a demo. A member of our team will be in touch."
SUBJECT = "[DAY8] Demo please"


EXPECTED_HTML = (
    "<p>AI draft - please review before sending</p>\n"
    "<p>Hello Sam,</p>\n"
    "<p>Thank you for getting in touch about a demo. A member of our team will be in touch.</p>\n"
    "<p>Kind regards,<br>Nova Wellness Team</p>"
)


def plain_preview(comment):
    """What Outlook's bodyPreview shows for an HTML comment: the text without the tags."""
    return " ".join(re.sub(r"<[^>]+>", " ", comment).split())[:255]


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


def original(mid="orig-1", sender=SENDER, reply_to=(), conversation="conv-1", subject=SUBJECT):
    return {
        "id": mid,
        "subject": subject,
        "conversationId": conversation,
        "from": {"emailAddress": {"address": sender, "name": NAME}},
        "replyTo": [{"emailAddress": {"address": a}} for a in reply_to],
    }


def answer(**changes):
    base = {
        "is_lead": True, "category": "Lead - Demo", "name": NAME, "email": SENDER, "company": None, "role": None,
        "request_type": "demo", "urgency": 1, "summary": "Wants a demo.", "draft_reply": CLEAN,
    }
    base.update(changes)
    return base


class PretendOutlook:
    """A strict pretend mailbox: Inbox, Leads, Leads/Processed and Drafts. Answers only documented request forms
    (anything else is a 400) and records every request. `errors` = {step: error to raise}."""

    def __init__(self, inbox=(), leads=(), processed=None, answers=None, markers=(), existing_drafts=(),
                 errors=None, create_to=None):
        self.folders = {
            "inbox": [copy.deepcopy(m) for m in inbox],
            "leads-id": [copy.deepcopy(m) for m in leads],
            "processed-id": [copy.deepcopy(m) for m in (processed if processed is not None else [original()])],
        }
        self.answers = {"orig-1": answer()} if answers is None else dict(answers)
        self.markers = set(markers)
        self.drafts = {}
        for draft_id, conversation in existing_drafts:
            self.drafts[draft_id] = {"id": draft_id, "conversationId": conversation, "isDraft": True,
                                     "parentFolderId": DRAFTS_ID, "toRecipients": [], "ccRecipients": [],
                                     "bccRecipients": [], "bodyPreview": "Something I wrote myself"}
        self.errors = dict(errors or {})
        self.create_to = create_to
        self.requests = []
        self.create_bodies = []
        self.unexpected = []

    def all_messages(self):
        return {m["id"]: m for rows in self.folders.values() for m in rows}

    def fail(self, step):
        error = self.errors.get(step)
        if error is not None:
            raise error

    @staticmethod
    def people(addresses):
        return [{"emailAddress": {"address": a}} for a in addresses]

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        method = request.get_method()
        parsed = urllib.parse.urlparse(request.full_url)
        path = parsed.path.replace("/v1.0", "", 1)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        body = json.loads(request.data.decode()) if request.data else None

        if method == "GET" and path == "/me/mailFolders":
            folders = [{"id": "inbox-id", "displayName": "Inbox"}, {"id": "leads-id", "displayName": "Leads"}]
            return FakeResponse(json.dumps({"value": folders}).encode())
        if method == "GET" and path == "/me/mailFolders/inbox/childFolders":
            return FakeResponse(json.dumps({"value": []}).encode())
        if method == "GET" and path == "/me/mailFolders/leads-id/childFolders":
            return FakeResponse(json.dumps({"value": [{"id": "processed-id", "displayName": "Processed"}]}).encode())

        if method == "GET" and path == "/me/mailFolders/drafts":
            if query != {"$select": ["id"]}:
                raise http_error(400)
            self.fail("verify_folder")
            return FakeResponse(json.dumps({"id": DRAFTS_ID}).encode())

        if method == "GET" and path == "/me/mailFolders/drafts/messages":
            wanted = re.fullmatch(r"conversationId eq '(.+)'", (query.get("$filter") or [""])[0])
            if set(query) != {"$filter", "$select", "$top"} or query["$select"] != ["id"] or not wanted:
                raise http_error(400)
            self.fail("drafts_check")
            found = [{"id": d["id"]} for d in self.drafts.values() if d["conversationId"] == wanted.group(1)]
            return FakeResponse(json.dumps({"value": found}).encode())

        found = re.fullmatch(r"/me/mailFolders/([^/]+)/messages", path)
        if method == "GET" and found:
            folder = urllib.parse.unquote(found.group(1))
            if query != LIST_PARAMS or folder not in self.folders:
                raise http_error(400)
            self.fail("list")
            rows = [m for m in self.folders[folder] if m["subject"].startswith(TAG)]
            return FakeResponse(json.dumps({"value": rows}).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions/([^/]+)", path)
        if method == "GET" and found:
            if query:
                raise http_error(400)
            message_id, name = urllib.parse.unquote(found.group(1)), urllib.parse.unquote(found.group(2))
            if name == LEAD_MARKER:
                self.fail("answer_read")
                if message_id in self.answers:
                    data = json.dumps(self.answers[message_id])
                    return FakeResponse(json.dumps({"extensionName": name, "version": 1, "data": data}).encode())
                raise http_error(404)
            if name == DRAFT_MARKER:
                self.fail("marker_read")
                if message_id in self.markers:
                    return FakeResponse(json.dumps({"extensionName": name, "version": 1, "data": "{}"}).encode())
                raise http_error(404)
            raise http_error(400)

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions", path)
        if method == "POST" and found:
            if query or not isinstance(body, dict) or body.get("extensionName") != DRAFT_MARKER or body.get("version") != 1:
                raise http_error(400)
            self.fail("marker_write")
            self.markers.add(urllib.parse.unquote(found.group(1)))
            return FakeResponse(json.dumps(body).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)/createReply", path)
        if method == "POST" and found:
            if query or not isinstance(body, dict) or set(body) != {"comment"}:
                raise http_error(400)
            self.create_bodies.append(body)
            self.fail("create")
            source = self.all_messages()[urllib.parse.unquote(found.group(1))]
            to = [self.create_to] if self.create_to else [source["from"]["emailAddress"]["address"]]
            draft_id = f"draft-{len(self.drafts) + 1}"
            self.drafts[draft_id] = {
                "id": draft_id, "conversationId": source["conversationId"], "isDraft": True,
                "parentFolderId": DRAFTS_ID, "toRecipients": self.people(to), "ccRecipients": [],
                "bccRecipients": [], "bodyPreview": plain_preview(body["comment"]),
            }
            return FakeResponse(json.dumps(self.drafts[draft_id]).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)", path)
        if method == "GET" and found:
            if query != {"$select": [DRAFT_SELECT]}:
                raise http_error(400)
            self.fail("verify_message")
            draft = self.drafts.get(urllib.parse.unquote(found.group(1)))
            if draft is None:
                raise http_error(404)
            return FakeResponse(json.dumps(draft).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]


def touches_real_env(path):
    try:
        return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())
    except (OSError, ValueError, TypeError):
        return False


class ReplyDraftCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env_path = self.tmp / ".env"
        self.env_path.write_text("MS_CLIENT_ID=fake-from-file\n", encoding="utf-8")
        self.assertFalse(touches_real_env(self.env_path))
        for module in (outlook_signin, outlook_to_slack):
            patcher = mock.patch.object(module, "ENV_PATH", self.env_path)
            patcher.start()
            self.addCleanup(patcher.stop)

        real_open = open

        def guarded_open(file, mode="r", *args, **kwargs):
            if touches_real_env(file):
                raise AssertionError("TEST SAFETY: the real .env must not be touched")
            if any(flag in mode for flag in "wax+") and not str(file).startswith(str(self.tmp)):
                raise AssertionError("the check must not write any file")
            return real_open(file, mode, *args, **kwargs)

        patcher = mock.patch("builtins.open", guarded_open)
        patcher.start()
        self.addCleanup(patcher.stop)

        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch("time.sleep", lambda seconds: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, outlook, argv=("--only-subject", "Demo please"), token_error=None):
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        if token_error is not None:
            token_patch = mock.patch.object(reply_draft_check, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(reply_draft_check, "get_access_token", return_value=FAKE_TOKEN)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["reply_draft_check.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", outlook.urlopen), \
                token_patch:
            code = reply_draft_check.main()
        output = buffer.getvalue().decode("utf-8")
        self.assertNotIn(FAKE_TOKEN, output, "a token was printed")
        self.assertNotIn(FAKE_CACHE, output, "a token was printed")
        self.assertNotIn(CLEAN, output, "the draft text was printed")
        self.assertNotIn(NAME, output, "a lead's name was printed")
        self.assertEqual(outlook.unexpected, [], "an unexpected request was sent")
        return code, output

    def live(self, outlook, *extra):
        return self.run_main(outlook, ("--only-subject", "Demo please", *extra, "--live"))

    # ---- Case K1: choosing the one email ---------------------------------------------------------------

    def test_k1_the_one_matching_email_is_found_in_any_of_the_three_folders(self):
        for folder in ("inbox", "leads", "processed"):
            with self.subTest(folder=folder):
                world = PretendOutlook(**{"inbox": [], "leads": [], "processed": [], folder: [original()]})
                code, output = self.run_main(world, ("--only-subject", "DEMO PLEASE"))
                self.assertEqual(code, 0, output)
                self.assertIn(SENDER, output)
                self.assertIn(SUBJECT, output)

    def test_k1_a_real_email_with_a_matching_subject_is_never_chosen(self):
        real = original("real-1", subject="Demo please", conversation="conv-real")
        world = PretendOutlook(inbox=[real], processed=[original()], answers={"orig-1": answer(), "real-1": answer()})
        code, output = self.run_main(world, ("--only-subject", "Demo please"))
        self.assertEqual(code, 0, output)
        for request in world.requests:
            self.assertNotIn("real-1", request.full_url)

    def test_k1_two_matches_stop_the_script_and_change_nothing(self):
        world = PretendOutlook(processed=[
            original("orig-1", subject="[DAY8] Demo please"),
            original("orig-2", sender=OTHER, subject="[DAY8] Demo please too", conversation="conv-2"),
        ], answers={"orig-1": answer(), "orig-2": answer(email=OTHER)})
        code, output = self.live(world)
        self.assertEqual(code, 1)
        self.assertIn("more than one", output)
        self.assertIn("--only-from", output)
        self.assertEqual(world.changes(), [])
        self.assertEqual(world.create_bodies, [])
        self.assertFalse([r for r in world.requests if "/extensions/" in r.full_url or "drafts" in r.full_url])

    def test_k1_only_from_picks_one_of_two_and_ignores_capitals_and_spaces(self):
        world = PretendOutlook(processed=[
            original("orig-1", subject="[DAY8] Demo please"),
            original("orig-2", sender=OTHER, subject="[DAY8] Demo please too", conversation="conv-2"),
        ], answers={"orig-1": answer(), "orig-2": answer(email=OTHER)})
        code, output = self.run_main(world, ("--only-subject", "Demo please", "--only-from", "  SAM.Fake@Example.COM "))
        self.assertEqual(code, 0, output)
        self.assertIn(SENDER, output)
        self.assertNotIn(OTHER, output)

    def test_k1_no_match_says_so_and_changes_nothing(self):
        world = PretendOutlook()
        code, output = self.live_other(world, "Nothing like this")
        self.assertEqual(code, 1)
        self.assertIn("No [DAY8] email", output)
        self.assertEqual(world.changes(), [])
        self.assertFalse([r for r in world.requests if "/extensions/" in r.full_url])

    def live_other(self, world, text):
        return self.run_main(world, ("--only-subject", text, "--live"))

    def test_k1_a_sender_that_does_not_match_gives_no_match(self):
        world = PretendOutlook()
        code, output = self.run_main(world, ("--only-subject", "Demo please", "--only-from", "nobody@example.com", "--live"))
        self.assertEqual(code, 1)
        self.assertIn("No [DAY8] email", output)
        self.assertEqual(world.changes(), [])

    def test_k1_empty_text_is_refused_before_any_request(self):
        for text in ("", "   "):
            with self.subTest(text=repr(text)):
                world = PretendOutlook()
                code, output = self.run_main(world, ("--only-subject", text))
                self.assertEqual(code, 1)
                self.assertIn("must not be empty", output)
                self.assertEqual(world.requests, [])

    def test_k1_a_blank_only_from_is_refused_before_any_request(self):
        world = PretendOutlook()
        code, output = self.run_main(world, ("--only-subject", "Demo please", "--only-from", "  "))
        self.assertEqual(code, 1)
        self.assertIn("must not be empty", output)
        self.assertEqual(world.requests, [])

    def test_k1_only_subject_is_required(self):
        world = PretendOutlook()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                self.run_main(world, ())
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(world.requests, [])

    # ---- Case K2: dry run by default ----------------------------------------------------------------------

    def test_k2_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        world = PretendOutlook()
        code, output = self.run_main(world)
        self.assertEqual(code, 0, output)
        self.assertEqual(world.changes(), [])
        self.assertEqual(world.drafts, {})
        self.assertEqual(world.markers, set())
        self.assertIn("Dry run", output)
        self.assertIn(SENDER, output)
        self.assertIn(SUBJECT, output)
        self.assertIn("Lead - Demo", output)
        self.assertIn("would create", output)
        self.assertIn(v3_reply.AI_DRAFT_LINE, output)
        self.assertIn("Nova Wellness Team", output)
        self.assertIn(f"{len(CLEAN.split())} words", output)
        self.assertIn("own line", output, "the dry run says each part sits on its own line")
        self.assertIn("greeting", output)
        self.assertIn("--live", output)
        self.assertIn("Nothing is sent", output)

    def test_k2_a_dry_run_says_when_a_draft_was_already_made(self):
        for options in (dict(markers=["orig-1"]), dict(existing_drafts=[("mine-1", "conv-1")])):
            with self.subTest(options=list(options)):
                world = PretendOutlook(**options)
                code, output = self.run_main(world)
                self.assertEqual(code, 0, output)
                self.assertIn("already made", output)
                self.assertNotIn("would create", output)
                self.assertEqual(world.changes(), [])

    def test_k2_a_dry_run_gives_the_reason_when_no_draft_would_be_made(self):
        cases = {
            "not a lead": (dict(is_lead=False, category="Not a lead", draft_reply=None), "confirmed lead"),
            "forwarded": (dict(email=OTHER), "sender"),
            "no draft": (dict(draft_reply=None), "no draft"),
        }
        for name, (changes, word) in cases.items():
            with self.subTest(case=name):
                world = PretendOutlook(answers={"orig-1": answer(**changes)})
                code, output = self.run_main(world)
                self.assertEqual(code, 0, output)
                self.assertIn("No reply draft would be made", output)
                self.assertIn(word, output)
                self.assertNotIn("would create", output)
                self.assertEqual(world.changes(), [])
                self.assertFalse([r for r in world.requests if "drafts" in r.full_url], "no need to look in Drafts")

    def test_k2_no_saved_answer_says_to_run_the_triage_first(self):
        world = PretendOutlook(answers={})
        code, output = self.run_main(world)
        self.assertEqual(code, 1)
        self.assertIn("triage", output)
        self.assertEqual(world.changes(), [])

    # ---- Case K3 and K4: --live makes exactly one draft, never twice -------------------------------------------

    def test_k3_live_makes_exactly_one_draft_and_one_marker(self):
        world = PretendOutlook(inbox=[original("other-1", sender=OTHER, subject="Not tagged", conversation="conv-9")])
        code, output = self.live(world)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(world.create_bodies), 1)
        self.assertEqual(world.create_bodies[0], {"comment": EXPECTED_HTML})
        self.assertEqual(len(world.drafts), 1)
        self.assertEqual(world.markers, {"orig-1"})
        self.assertEqual(len(world.changes()), 2)
        (draft,) = world.drafts.values()
        self.assertEqual([r["emailAddress"]["address"] for r in draft["toRecipients"]], [SENDER])
        for request in world.requests:
            self.assertNotIn("other-1", request.full_url)
        self.assertIn("created", output.lower())
        self.assertIn("Drafts", output)
        self.assertIn("by hand", output)
        self.assertIn("Nothing was sent", output)

    def test_k4_a_second_live_run_changes_nothing(self):
        world = PretendOutlook()
        self.live(world)
        before = len(world.changes())
        code, output = self.live(world)
        self.assertEqual(code, 0, output)
        self.assertIn("already made", output)
        self.assertEqual(len(world.changes()), before)
        self.assertEqual(len(world.create_bodies), 1)

    def test_k4_a_draft_already_in_the_conversation_blocks_a_new_one(self):
        world = PretendOutlook(existing_drafts=[("mine-1", "conv-1")])
        code, output = self.live(world)
        self.assertEqual(code, 0, output)
        self.assertIn("already made", output)
        self.assertEqual(world.create_bodies, [])

    # ---- Case K5: no draft when it would be wrong -----------------------------------------------------------------

    def test_k5_live_makes_no_draft_for_an_email_that_should_get_none(self):
        world = PretendOutlook(answers={"orig-1": answer(email=OTHER)})
        code, output = self.live(world)
        self.assertEqual(code, 1)
        self.assertIn("No reply draft was made", output)
        self.assertEqual(world.changes(), [])
        self.assertNotIn(OTHER, output)

    # ---- Case K6: when something goes wrong --------------------------------------------------------------------------

    def test_k6_a_refused_sign_in_and_other_errors_are_explained_plainly(self):
        scenarios = [
            ("create", http_error(403), "sign-in may need redoing"),
            ("create", http_error(500), "refused"),
            ("marker_read", http_error(500), "refused"),
            ("list", http_error(500), "refused"),
            ("answer_read", http_error(500), "refused"),
            ("list", urllib.error.URLError("pretend: down"), "couldn't reach Outlook"),
            ("create", urllib.error.URLError("pretend: down"), "couldn't reach Outlook"),
        ]
        for step, error, words in scenarios:
            with self.subTest(step=step, words=words):
                world = PretendOutlook(errors={step: error})
                code, output = self.live(world)
                self.assertEqual(code, 1)
                self.assertIn(words, output)
                for technical in ("Traceback", "HTTPError", "URLError", "403", "(code 500)" if words == "sign-in may need redoing" else "Traceback"):
                    self.assertNotIn(technical, output)
                self.assertLessEqual(len(world.create_bodies), 1, "no second attempt")

    def test_k6_a_draft_that_comes_back_wrong_gives_the_confirm_message(self):
        world = PretendOutlook(create_to=OTHER)
        code, output = self.live(world)
        self.assertEqual(code, 1)
        self.assertIn("confirm step failed", output)
        self.assertIn("may be in your Drafts folder", output)
        self.assertIn("by hand", output)
        self.assertNotIn(OTHER, output)
        self.assertEqual(world.markers, set())
        self.assertEqual(len(world.changes()), 1)

    def test_k6_a_sign_in_that_does_not_work_stops_before_any_request(self):
        world = PretendOutlook()
        problem = outlook_signin.SetupProblem("Please run outlook_signin.py again (pretend).")
        code, output = self.run_main(world, token_error=problem)
        self.assertEqual(code, 1)
        self.assertIn("outlook_signin.py", output)
        self.assertEqual(world.requests, [])

    # ---- Case K7: only safe requests -----------------------------------------------------------------------------------

    def refused(self):
        listing = {"$filter": "startswith(subject,'[DAY8]')", "$select": "id,subject,from,replyTo,conversationId", "$top": "100"}
        return [
            ("POST", "/me/mailFolders/inbox/messages", listing),
            ("POST", "/me/messages/abc/send", None),
            ("POST", "/me/messages/abc/createReply", None),
            ("POST", "/me/messages/abc/forward", None),
            ("POST", "/me/messages/abc/move", None),
            ("DELETE", "/me/messages/abc", None),
            ("PATCH", "/me/messages/abc", None),
            ("GET", "/me/messages/abc", None),
            ("GET", "/me/messages", listing),
            ("GET", "/me/mailFolders/inbox/messages", None),
            ("GET", "/me/mailFolders/inbox/messages", {**listing, "$top": "5"}),
            ("GET", "/me/mailFolders/inbox/messages", {**listing, "$filter": "isRead eq false"}),
            ("GET", "/me/mailFolders/inbox/messages", {**listing, "$select": "id,subject,body"}),
            ("GET", "/me/mailFolders/inbox/messages/abc", listing),
            ("GET", "/me/mailFolders/inbox/childFolders", listing),
        ]

    def test_k7_the_scripts_own_guard_allows_only_reading_the_tagged_emails(self):
        for method, path, params in self.refused():
            with self.subTest(method=method, path=path, params=params):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(reply_draft_check.BlockedRequest):
                        reply_draft_check.graph(FAKE_TOKEN, method, path, params=params)
                self.assertEqual(calls, [])

    def test_k7_the_guard_lets_the_one_wanted_read_through(self):
        listing = {"$filter": "startswith(subject,'[DAY8]')", "$select": "id,subject,from,replyTo,conversationId", "$top": "100"}
        for folder in ("inbox", "AAMk%3D", "abc-123"):
            calls = []
            with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b'{"value": []}')):
                reply_draft_check.graph(FAKE_TOKEN, "GET", f"/me/mailFolders/{folder}/messages", params=listing)
            self.assertEqual(len(calls), 1)

    def test_k7_the_script_text_has_no_send_reply_forward_move_copy_or_delete(self):
        text = (WORKFLOWS / "02-email-to-attio" / "reply_draft_check.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreply", r"/forward", r"createforward",
            r"/move", r"/copy", r"permanentdelete", r"\"delete\"", r"\"patch\"", r"\"put\"", r"\"post\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in reply_draft_check.py")

    def test_k7_the_permission_list_is_still_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])

    def test_k7_nothing_else_uses_this_one_off_script(self):
        for name in ("outlook_triage.py", "leads_to_attio.py", "lead_slack.py", "reply_drafts.py"):
            text = (WORKFLOWS / "02-email-to-attio" / name).read_text(encoding="utf-8")
            self.assertNotIn("reply_draft_check", text, name)

    def test_k7_the_attio_and_slack_steps_do_not_make_drafts(self):
        for name in ("leads_to_attio.py", "lead_slack.py"):
            text = (WORKFLOWS / "02-email-to-attio" / name).read_text(encoding="utf-8")
            self.assertNotIn("reply_draft", text, name)

    def test_k7_the_live_run_makes_no_move_and_sends_nothing_that_sends(self):
        world = PretendOutlook()
        self.live(world)
        for request in world.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/move", "/copy", "/send", "/forward", "replyall", "/reply"):
                self.assertNotIn(word, path)
            self.assertIn(request.get_method(), ("GET", "POST"))
            if request.get_method() == "POST":
                self.assertRegex(path, r"(/createreply|/extensions)$")

    # ---- Case K8: no leaks --------------------------------------------------------------------------------------------

    def test_k8_nothing_secret_is_printed_in_any_scenario(self):
        # run_main fails the test if a token, the draft text or the lead's name is in the output.
        scenarios = [
            PretendOutlook(), PretendOutlook(markers=["orig-1"]), PretendOutlook(answers={}),
            PretendOutlook(answers={"orig-1": answer(email=OTHER)}), PretendOutlook(create_to=OTHER),
            PretendOutlook(errors={"create": http_error(403)}),
        ]
        for world in scenarios:
            self.live(world)
            self.run_main(world)


if __name__ == "__main__":
    unittest.main(verbosity=2)
