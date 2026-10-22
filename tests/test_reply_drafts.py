"""Pretend-data checks for workflows/02-email-to-attio/reply_drafts.py (not written yet: step 2).

These follow tests/cases/12-reply-drafts.md, Part A. They say what the new module must offer:

  reply_drafts.make_reply_draft(token, message, answer) -> (status, detail)
        status is "created", "exists" or "skipped"; detail is a plain reason for "skipped", else None.
        A problem is raised as reply_drafts.ReplyDraftProblem (a plain-words message).
  reply_drafts.reply_skip_reason(message, answer) -> a plain reason, or None when a draft would be made
        (no request to Outlook at all).
  reply_drafts.graph(token, method, path, params=None, body=None)   the guard; refused requests raise
        reply_drafts.BlockedRequest before anything leaves the computer.
  reply_drafts.MARKER_NAME, reply_drafts.DRAFT_SELECT, reply_drafts.ReplyDraftProblem, BlockedRequest.

message is an email as Outlook lists it (id, subject, conversationId, from, replyTo); answer is the
answer saved on it (see saved_results.py).

Everything here is fake and self-contained:
  - a strict pretend Outlook answers in place of the real one (only documented request forms),
  - the internet is blocked, so nothing can leave this computer,
  - while the code runs, opening any local file stops the test, and anything printed fails it.

Run:  python tests\\test_reply_drafts.py
"""

import copy
import io
import json
import re
import socket
import sys
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
import reply_drafts  # noqa: E402
import v3_reply  # noqa: E402

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
SENDER = "sam.fake@example.com"
OTHER = "someone.else@example.com"
MARKER = "com.novawellness.draftmade"
DRAFT_SELECT = "id,isDraft,parentFolderId,toRecipients,ccRecipients,bccRecipients,conversationId,bodyPreview"
DRAFTS_ID = "drafts-id"

CLEAN = "Hello Sam, thank you for getting in touch about a demo. A member of our team will be in touch."
# Outlook's "create reply" puts the comment into an HTML page, where plain line breaks collapse into one
# paragraph. So the finished text goes in as simple HTML: the AI line, the greeting, the body and the
# sign-off each in their own paragraph (the body starts with a capital letter after the greeting is split off).
BODY_TEXT = "Thank you for getting in touch about a demo. A member of our team will be in touch."
SIGN_OFF_HTML = "<p>Kind regards,<br>Nova Wellness Team</p>"
EXPECTED_HTML = (
    "<p>AI draft - please review before sending</p>\n"
    "<p>Hello Sam,</p>\n"
    "<p>" + BODY_TEXT + "</p>\n"
    + SIGN_OFF_HTML
)


def plain_preview(comment):
    """What Outlook's bodyPreview shows for an HTML comment: the text without the tags."""
    return " ".join(re.sub(r"<[^>]+>", " ", comment).split())[:255]


def original(mid="orig-1", sender=SENDER, reply_to=(), conversation="conv-1", subject="Demo please"):
    message = {
        "id": mid,
        "subject": subject,
        "conversationId": conversation,
        "from": {"emailAddress": {"address": sender, "name": "Sam Fake"}},
        "replyTo": [{"emailAddress": {"address": a}} for a in reply_to],
    }
    return message


def answer(**changes):
    base = {
        "is_lead": True,
        "category": "Lead - Demo",
        "name": "Sam Fake",
        "email": SENDER,
        "company": None,
        "role": None,
        "request_type": "demo",
        "urgency": 1,
        "summary": "Wants a demo.",
        "draft_reply": CLEAN,
    }
    base.update(changes)
    return base


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


def marker_body(data="{}"):
    return {
        "@odata.type": "microsoft.graph.openTypeExtension",
        "extensionName": MARKER,
        "version": 1,
        "data": data,
    }


STEPS = ("marker_read", "drafts_check", "create", "verify_message", "verify_folder", "marker_write")


class PretendOutlook:
    """A strict pretend mailbox. Answers only the documented request forms (anything else is a 400)
    and records every request. `errors` makes a step fail: {step name: the error to raise}."""

    def __init__(self, messages=(), markers=(), existing_drafts=(), errors=None, create_to=None,
                 create_cc=(), create_bcc=(), create_folder=DRAFTS_ID, create_is_draft=True,
                 create_preview=None):
        self.messages = {m["id"]: copy.deepcopy(m) for m in (messages or [original()])}
        self.markers = set(markers)
        self.drafts = {}
        for draft_id, conversation in existing_drafts:
            self.drafts[draft_id] = {"id": draft_id, "conversationId": conversation, "isDraft": True,
                                     "parentFolderId": DRAFTS_ID, "toRecipients": [], "ccRecipients": [],
                                     "bccRecipients": [], "bodyPreview": "Something I wrote myself"}
        self.errors = dict(errors or {})
        self.create_to, self.create_cc, self.create_bcc = create_to, list(create_cc), list(create_bcc)
        self.create_folder, self.create_is_draft, self.create_preview = create_folder, create_is_draft, create_preview
        self.requests = []
        self.create_bodies = []
        self.marker_bodies = []
        self.unexpected = []

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
            found = [{"id": d["id"]} for d in self.drafts.values()
                     if d["conversationId"] == wanted.group(1) and d["parentFolderId"] == DRAFTS_ID]
            return FakeResponse(json.dumps({"value": found}).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions/([^/]+)", path)
        if method == "GET" and found:
            if query:
                raise http_error(400)
            self.fail("marker_read")
            if urllib.parse.unquote(found.group(1)) in self.markers and urllib.parse.unquote(found.group(2)) == MARKER:
                return FakeResponse(json.dumps({"extensionName": MARKER, "version": 1, "data": "{}"}).encode())
            raise http_error(404)

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions", path)
        if method == "POST" and found:
            if query or not isinstance(body, dict) or set(body) != {"@odata.type", "extensionName", "version", "data"} \
                    or body["extensionName"] != MARKER or body["version"] != 1 or not isinstance(body["data"], str):
                raise http_error(400)
            self.marker_bodies.append(body)
            self.fail("marker_write")
            self.markers.add(urllib.parse.unquote(found.group(1)))
            return FakeResponse(json.dumps(body).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)/createReply", path)
        if method == "POST" and found:
            if query or not isinstance(body, dict) or set(body) != {"comment"} or not isinstance(body["comment"], str):
                raise http_error(400)
            self.create_bodies.append(body)
            self.fail("create")
            source = self.messages[urllib.parse.unquote(found.group(1))]
            if self.create_to is not None:
                to = list(self.create_to)
            elif source.get("replyTo"):
                to = [r["emailAddress"]["address"] for r in source["replyTo"]]
            else:
                to = [source["from"]["emailAddress"]["address"]]
            draft_id = f"draft-{len(self.drafts) + 1}"
            self.drafts[draft_id] = {
                "id": draft_id, "conversationId": source["conversationId"], "isDraft": self.create_is_draft,
                "parentFolderId": self.create_folder, "toRecipients": self.people(to),
                "ccRecipients": self.people(self.create_cc), "bccRecipients": self.people(self.create_bcc),
                "bodyPreview": self.create_preview if self.create_preview is not None else plain_preview(body["comment"]),
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

    def paths(self):
        return [urllib.parse.urlparse(r.full_url).path for r in self.requests]


class ReplyDraftTests(unittest.TestCase):
    def setUp(self):
        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, world, function, *args):
        """Call a reply_drafts function against the pretend Outlook.

        While it runs, opening any local file stops the test, and anything printed fails it."""
        buffer = io.StringIO()

        def no_files(*a, **k):
            raise AssertionError("TEST SAFETY: the reply-draft code must not open any local file")

        with mock.patch("urllib.request.urlopen", world.urlopen), \
                mock.patch("builtins.open", no_files), \
                mock.patch.object(sys, "stdout", buffer):
            try:
                return function(*args)
            finally:
                self.assertEqual(buffer.getvalue(), "", "the reply-draft code must print nothing")
                self.assertEqual(world.unexpected, [], "an unexpected request was sent")

    def make(self, world, message=None, saved=None):
        return self.call(world, reply_drafts.make_reply_draft, FAKE_TOKEN, message or original(), saved or answer())

    def make_raises(self, world, message=None, saved=None):
        with self.assertRaises(reply_drafts.ReplyDraftProblem) as caught:
            self.make(world, message, saved)
        return str(caught.exception)

    # ---- Case R1: a clean lead gets exactly one reply draft --------------------------------

    def test_r1_a_clean_lead_gets_exactly_one_reply_draft(self):
        world = PretendOutlook()
        status, detail = self.make(world)
        self.assertEqual((status, detail), ("created", None))
        self.assertEqual(len(world.create_bodies), 1)
        self.assertEqual(world.create_bodies[0], {"comment": EXPECTED_HTML})
        self.assertEqual(len(world.drafts), 1)
        self.assertEqual(world.markers, {"orig-1"})

    def test_r1_the_requests_come_in_this_order(self):
        world = PretendOutlook()
        self.make(world)
        order = [(r.get_method(), p) for r, p in zip(world.requests, world.paths())]
        self.assertEqual(order, [
            ("GET", "/v1.0/me/messages/orig-1/extensions/" + MARKER),
            ("GET", "/v1.0/me/mailFolders/drafts/messages"),
            ("POST", "/v1.0/me/messages/orig-1/createReply"),
            ("GET", "/v1.0/me/messages/draft-1"),
            ("GET", "/v1.0/me/mailFolders/drafts"),
            ("POST", "/v1.0/me/messages/orig-1/extensions"),
        ])

    def test_r1_only_the_reply_draft_and_the_marker_are_changed(self):
        world = PretendOutlook([original(), original("other-1", conversation="conv-9")])
        self.make(world)
        changes = world.changes()
        self.assertEqual(len(changes), 2)
        self.assertTrue(changes[0].full_url.endswith("/me/messages/orig-1/createReply"))
        self.assertTrue(changes[1].full_url.endswith("/me/messages/orig-1/extensions"))
        for path in world.paths():
            self.assertNotIn("other-1", path)
        for request in world.requests:
            self.assertNotIn(request.get_method(), ("DELETE", "PATCH", "PUT"))
            for word in ("/move", "/copy", "/send", "/forward", "replyall"):
                self.assertNotIn(word, request.full_url.lower())

    # ---- Case R2: the finished text is exact -----------------------------------------------------

    def test_r2_the_text_is_the_ai_line_gemini_text_and_the_fixed_sign_off(self):
        world = PretendOutlook()
        self.make(world)
        sent = world.create_bodies[0]["comment"]
        self.assertEqual(sent, EXPECTED_HTML)
        self.assertTrue(sent.startswith("<p>" + v3_reply.AI_DRAFT_LINE + "</p>"))
        self.assertTrue(sent.endswith(SIGN_OFF_HTML))
        self.assertEqual(sent.count("AI draft"), 1)

    def test_r2_the_ai_line_the_greeting_the_body_and_the_sign_off_each_have_their_own_paragraph(self):
        world = PretendOutlook()
        self.make(world)
        sent = world.create_bodies[0]["comment"]
        paragraphs = re.findall(r"<p>(.*?)</p>", sent, re.S)
        self.assertEqual(paragraphs, [
            "AI draft - please review before sending",
            "Hello Sam,",
            BODY_TEXT,
            "Kind regards,<br>Nova Wellness Team",
        ])
        self.assertEqual(re.sub(r"</?p>|<br>|\n", "", sent).count("<"), 0)

    def test_r2_every_paragraph_is_made_from_the_pieces_and_nothing_is_added(self):
        for saved_text, expected_middle in (
            ("Hello, thank you for getting in touch about pricing. A member of our team will be in touch.",
             ["Hello,", "Thank you for getting in touch about pricing. A member of our team will be in touch."]),
            ("Hello Anna Maria, thank you for getting in touch about a demo. A member of our team will be in touch.",
             ["Hello Anna Maria,", "Thank you for getting in touch about a demo. A member of our team will be in touch."]),
            # no greeting to split off: the whole text stays together, unchanged
            ("Thank you for getting in touch about a demo. A member of our team will be in touch.",
             ["Thank you for getting in touch about a demo. A member of our team will be in touch."]),
            # a body that already has its own paragraphs and line breaks keeps them
            ("Hello Sam, thank you for getting in touch about a demo.\n\nA member of our team will be in touch.\nThank you.",
             ["Hello Sam,", "Thank you for getting in touch about a demo.",
              "A member of our team will be in touch.<br>Thank you."]),
        ):
            with self.subTest(text=saved_text[:25]):
                world = PretendOutlook()
                status, _ = self.make(world, saved=answer(draft_reply=saved_text))
                self.assertEqual(status, "created")
                paragraphs = re.findall(r"<p>(.*?)</p>", world.create_bodies[0]["comment"], re.S)
                self.assertEqual(paragraphs, ["AI draft - please review before sending", *expected_middle,
                                              "Kind regards,<br>Nova Wellness Team"])

    def test_r2_html_or_symbols_in_gemini_text_are_written_safely(self):
        saved_text = "Hello Sam, thank you for asking about a <b>demo</b> & more. A member of our team will be in touch."
        world = PretendOutlook()
        status, _ = self.make(world, saved=answer(draft_reply=saved_text))
        self.assertEqual(status, "created")
        sent = world.create_bodies[0]["comment"]
        self.assertIn("&lt;b&gt;demo&lt;/b&gt; &amp; more", sent)
        self.assertNotIn("<b>", sent)

    def test_r2_a_saved_draft_with_its_own_ai_line_or_sign_off_is_not_used(self):
        for text in (
            "AI draft - please review before sending\n\n" + CLEAN,
            CLEAN + "\n\nKind regards,\nSomeone",
            CLEAN + " Best wishes",
        ):
            with self.subTest(text=text[:30]):
                world = PretendOutlook()
                status, reason = self.make(world, saved=answer(draft_reply=text))
                self.assertEqual(status, "skipped")
                self.assertEqual(world.requests, [])

    # ---- Case R3: addressed only to the original sender, and checked ---------------------------------

    def test_r3_the_draft_is_read_back_and_is_addressed_only_to_the_sender(self):
        world = PretendOutlook()
        self.make(world)
        (draft,) = world.drafts.values()
        self.assertEqual([r["emailAddress"]["address"] for r in draft["toRecipients"]], [SENDER])
        self.assertEqual((draft["ccRecipients"], draft["bccRecipients"]), ([], []))
        reads = [r.full_url for r in world.requests if r.full_url.endswith(("/draft-1", "/drafts"))
                 or "/draft-1?" in r.full_url]
        self.assertTrue(reads)
        self.assertEqual(reply_drafts.DRAFT_SELECT, DRAFT_SELECT)
        self.assertEqual(reply_drafts.MARKER_NAME, MARKER)

    def test_r3_capitals_and_spaces_in_the_address_do_not_matter(self):
        world = PretendOutlook([original(sender="  Sam.Fake@Example.COM ")])
        status, _ = self.make(world, message=original(sender="  Sam.Fake@Example.COM "))
        self.assertEqual(status, "created")

    def test_r3_a_reply_to_that_is_the_sender_itself_is_fine(self):
        message = original(reply_to=[SENDER.upper()])
        world = PretendOutlook([message])
        status, _ = self.make(world, message=message)
        self.assertEqual(status, "created")

    def test_r3_a_draft_that_comes_back_wrong_stops_with_the_confirm_message_and_changes_nothing_else(self):
        wrong = {
            "another recipient": dict(create_to=[OTHER]),
            "two recipients": dict(create_to=[SENDER, OTHER]),
            "a cc": dict(create_cc=[OTHER]),
            "a bcc": dict(create_bcc=[OTHER]),
            "not a draft": dict(create_is_draft=False),
            "not in Drafts": dict(create_folder="some-other-folder"),
            "no AI line": dict(create_preview="Hello Sam"),
        }
        for name, options in wrong.items():
            with self.subTest(problem=name):
                world = PretendOutlook(**options)
                message = self.make_raises(world)
                self.assertIn("confirm step failed", message)
                self.assertIn("may be in your Drafts folder", message)
                self.assertIn("by hand", message)
                for private in (SENDER, OTHER, CLEAN, "Sam", FAKE_TOKEN):
                    self.assertNotIn(private, message)
                self.assertEqual(world.markers, set(), "no marker when the check failed")
                self.assertEqual(len(world.changes()), 1, "only the one create request was sent")
                for request in world.requests:
                    self.assertNotIn(request.get_method(), ("DELETE", "PATCH", "PUT"))

    # ---- Case R4: no draft is made when it would be wrong -----------------------------------------------

    SKIPS = {
        "forwarded": (dict(), dict(email=OTHER), "sender"),
        "reply-to somewhere else": (dict(reply_to=["boss@example.com"]), dict(), "Reply-To"),
        "no draft saved": (dict(), dict(draft_reply=None), "no draft"),
        "empty draft": (dict(), dict(draft_reply="   "), "no draft"),
        "draft breaks a rule": (dict(), dict(draft_reply="It costs $5 per month, thanks for asking today."), "draft rules"),
        "not a lead": (dict(), dict(is_lead=False, category="Not a lead", draft_reply=None), "confirmed lead"),
        "needs review": (dict(), dict(category="Needs review"), "confirmed lead"),
        "lead flag but not a lead category": (dict(), dict(category="Not a lead"), "confirmed lead"),
        "no sender address": (dict(sender=""), dict(), "sender address"),
        "sender is not an address": (dict(sender="not-an-address"), dict(), "sender address"),
    }

    def test_r4_nothing_is_sent_to_outlook_and_the_reason_is_plain(self):
        for name, (message_options, answer_options, word) in self.SKIPS.items():
            with self.subTest(case=name):
                world = PretendOutlook()
                status, reason = self.make(world, message=original(**message_options), saved=answer(**answer_options))
                self.assertEqual(status, "skipped")
                self.assertIsInstance(reason, str)
                self.assertIn(word, reason)
                self.assertEqual(world.requests, [], "a skipped email must send nothing to Outlook")
                for private in (SENDER, OTHER, "boss@example.com", CLEAN, "Sam", "$5"):
                    self.assertNotIn(private, reason)

    def test_r4_a_missing_draft_field_is_the_same_as_no_draft(self):
        saved = {k: v for k, v in answer().items() if k != "draft_reply"}
        world = PretendOutlook()
        status, reason = self.make(world, saved=saved)
        self.assertEqual(status, "skipped")
        self.assertIn("no draft", reason)
        self.assertEqual(world.requests, [])

    # ---- Case R5: a second draft is never made ---------------------------------------------------------------

    def test_r5_a_marker_on_the_email_means_nothing_is_created(self):
        world = PretendOutlook(markers=["orig-1"])
        status, detail = self.make(world)
        self.assertEqual((status, detail), ("exists", None))
        self.assertEqual(world.create_bodies, [])
        self.assertEqual(len(world.requests), 1)
        self.assertTrue(world.requests[0].full_url.endswith("/extensions/" + MARKER))

    def test_r5_a_draft_already_in_the_conversation_counts_as_done_and_gets_the_marker(self):
        world = PretendOutlook(existing_drafts=[("mine-1", "conv-1")])
        status, _ = self.make(world)
        self.assertEqual(status, "exists")
        self.assertEqual(world.create_bodies, [])
        self.assertEqual(world.markers, {"orig-1"})
        self.assertEqual(len(world.changes()), 1, "only the marker is written")

    def test_r5_a_draft_in_another_conversation_does_not_block_a_new_one(self):
        world = PretendOutlook(existing_drafts=[("mine-1", "conv-OTHER")])
        status, _ = self.make(world)
        self.assertEqual(status, "created")
        self.assertEqual(len(world.create_bodies), 1)

    def test_r5_a_stop_after_creating_then_a_second_run_makes_no_second_draft(self):
        world = PretendOutlook(errors={"marker_write": http_error(500)})
        self.make_raises(world)
        self.assertEqual(len(world.create_bodies), 1)
        self.assertEqual(world.markers, set())
        world.errors.clear()
        status, _ = self.make(world)
        self.assertEqual(status, "exists")
        self.assertEqual(len(world.create_bodies), 1, "exactly one create request in total")
        self.assertEqual(world.markers, {"orig-1"})
        self.assertEqual(len(world.drafts), 1)

    def test_r5_the_same_email_twice_in_a_row_changes_nothing_the_second_time(self):
        world = PretendOutlook()
        self.make(world)
        before = len(world.changes())
        status, _ = self.make(world)
        self.assertEqual(status, "exists")
        self.assertEqual(len(world.changes()), before)

    def test_r5_a_draft_you_deleted_by_hand_is_not_made_again(self):
        world = PretendOutlook()
        self.make(world)
        world.drafts.clear()
        status, _ = self.make(world)
        self.assertEqual(status, "exists")
        self.assertEqual(len(world.create_bodies), 1)
        self.assertEqual(world.drafts, {})

    # ---- Case R6: when Outlook has a problem -----------------------------------------------------------------------

    def test_r6_a_refused_sign_in_says_it_may_need_redoing_and_does_not_try_again(self):
        for code_number in (401, 403):
            with self.subTest(code=code_number):
                world = PretendOutlook(errors={"create": http_error(code_number)})
                message = self.make_raises(world)
                self.assertIn("sign-in may need redoing", message)
                self.assertIn("outlook_signin.py", message)
                self.assertNotIn(str(code_number), message)
                self.assertNotIn("HTTPError", message)
                self.assertEqual(len(world.create_bodies), 1, "no second attempt")
                self.assertEqual(world.drafts, {})
                self.assertEqual(world.markers, set())

    def test_r6_another_error_at_any_step_stops_with_a_plain_message(self):
        for step in STEPS:
            with self.subTest(step=step):
                world = PretendOutlook(errors={step: http_error(500)})
                message = self.make_raises(world)
                self.assertIn("refused", message)
                self.assertNotIn("Traceback", message)
                self.assertNotIn(FAKE_TOKEN, message)
                if step in ("marker_read", "drafts_check"):
                    self.assertEqual(world.create_bodies, [], "a failed read is not taken to mean 'nothing there'")
                if step in ("verify_message", "verify_folder"):
                    self.assertIn("confirm step failed", message)
                    self.assertIn("may be in your Drafts folder", message)
                if step != "marker_write":
                    self.assertEqual(world.markers, set())

    def test_r6_a_lost_connection_at_any_step_is_explained(self):
        for step in STEPS:
            with self.subTest(step=step):
                world = PretendOutlook(errors={step: urllib.error.URLError("pretend: down")})
                message = self.make_raises(world)
                self.assertIn("couldn't reach Outlook", message)
                self.assertNotIn("URLError", message)
                if step in ("verify_message", "verify_folder"):
                    self.assertIn("confirm step failed", message)
                    self.assertIn("by hand", message)
                if step in ("marker_read", "drafts_check"):
                    self.assertEqual(world.create_bodies, [])

    # ---- Case R7: the guard -----------------------------------------------------------------------------------------

    def refused(self):
        good = EXPECTED_HTML
        ext = marker_body()
        return [
            ("POST", "/me/messages/abc/send", None, {}),
            ("POST", "/me/sendMail", None, {"message": {}}),
            ("POST", "/me/messages/abc/reply", None, {"comment": good}),
            ("POST", "/me/messages/abc/replyAll", None, {"comment": good}),
            ("POST", "/me/messages/abc/createReplyAll", None, {"comment": good}),
            ("POST", "/me/messages/abc/forward", None, {}),
            ("POST", "/me/messages/abc/createForward", None, {}),
            ("POST", "/me/messages/abc/move", None, {"destinationId": "deleteditems"}),
            ("POST", "/me/messages/abc/copy", None, {"destinationId": "inbox"}),
            ("POST", "/me/mailFolders/drafts/messages", None, {"subject": "x", "body": {"contentType": "Text", "content": "y"}}),
            ("POST", "/me/messages/abc/createReply", None, None),
            ("POST", "/me/messages/abc/createReply", None, {}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good, "toRecipients": [{"emailAddress": {"address": OTHER}}]}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good, "message": {}}),
            ("POST", "/me/messages/abc/createReply", None, {"message": {"body": {"content": good}}}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": "Hello without the AI line"}),
            # the old plain-text form would lose its line breaks in Outlook, so it is refused too
            ("POST", "/me/messages/abc/createReply", None, {"comment": v3_reply.assemble_draft(CLEAN)}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good.replace("<p>Hello Sam,</p>", '<p><a href="https://x.example">Hello</a></p>')}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good.replace("<p>Hello Sam,</p>", "<script>x</script>")}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good.replace("<p>Hello Sam,</p>", '<img src="https://x.example/a.png">')}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good.replace("<p>", "<p style=\"x\">", 1)}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good.replace(SIGN_OFF_HTML, "<p>Kind regards,<br>Someone</p>")}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": good.replace(SIGN_OFF_HTML, "")}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": "<p>Not the AI line</p>" + good[len("<p>AI draft - please review before sending</p>"):]}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": None}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": ["x"]}),
            ("POST", "/me/messages/abc/createReply", {"x": "1"}, {"comment": good}),
            ("POST", "/me/messages/abc/extensions", None, {**ext, "extensionName": "com.novawellness.leadresult"}),
            ("POST", "/me/messages/abc/extensions", None, {**ext, "isRead": True}),
            ("POST", "/me/messages/abc/extensions", None, {**ext, "data": {"a": 1}}),
            ("POST", "/me/messages/abc/extensions", None, {**ext, "version": "1"}),
            ("DELETE", "/me/messages/abc", None, None),
            ("DELETE", "/me/messages/abc/extensions/" + MARKER, None, None),
            ("PATCH", "/me/messages/abc", None, {"body": {"contentType": "Text", "content": "x"}}),
            ("PATCH", "/me/messages/abc", None, {"toRecipients": []}),
            ("PUT", "/me/messages/abc", None, {"x": 1}),
            ("GET", "/me/messages", None, None),
            ("GET", "/me/mailFolders/inbox/messages", {"$top": "5"}, None),
            ("GET", "/me/mailFolders/drafts/messages", None, None),
            ("GET", "/me/mailFolders/drafts/messages", {"$filter": "isDraft eq true", "$select": "id", "$top": "5"}, None),
            ("GET", "/me/mailFolders/drafts/messages", {"$filter": "conversationId eq 'c'", "$select": "id,subject,body", "$top": "5"}, None),
            ("GET", "/me/mailFolders/drafts", None, None),
            ("GET", "/me/mailFolders/drafts", {"$select": "id,displayName"}, None),
            ("GET", "/me/mailFolders/deleteditems", {"$select": "id"}, None),
            ("GET", "/me/messages/abc", None, None),
            ("GET", "/me/messages/abc", {"$select": "id,body"}, None),
            ("GET", "/me/messages/abc/attachments", {"$select": DRAFT_SELECT}, None),
            ("GET", "/me/messages/abc/send", None, None),
            ("GET", "/me/messages/abc/extensions", None, None),
            ("GET", "/me/messages/abc/extensions/com.other.app", None, None),
            ("GET", "/me/messages/abc/extensions/" + MARKER, {"$select": "id"}, None),
        ]

    def test_r7_the_guard_refuses_everything_else_before_it_leaves_the_computer(self):
        for method, path, params, body in self.refused():
            with self.subTest(method=method, path=path, params=params):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(reply_drafts.BlockedRequest):
                        reply_drafts.graph(FAKE_TOKEN, method, path, params=params, body=body)
                self.assertEqual(calls, [], "a refused request must never leave the code")

    def test_r7_the_guard_lets_the_six_wanted_requests_through(self):
        good = EXPECTED_HTML
        allowed = [
            ("GET", "/me/mailFolders/drafts", {"$select": "id"}, None),
            ("GET", "/me/mailFolders/drafts/messages", {"$filter": "conversationId eq 'AAQk=='", "$select": "id", "$top": "5"}, None),
            ("GET", "/me/messages/abc%3D", {"$select": DRAFT_SELECT}, None),
            ("GET", "/me/messages/abc%3D/extensions/" + MARKER, None, None),
            ("POST", "/me/messages/abc%3D/createReply", None, {"comment": good}),
            ("POST", "/me/messages/abc%3D/extensions", None, marker_body()),
        ]
        for method, path, params, body in allowed:
            with self.subTest(method=method, path=path):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    reply_drafts.graph(FAKE_TOKEN, method, path, params=params, body=body)
                self.assertEqual(len(calls), 1)

    def test_r7_the_code_text_has_no_send_reply_all_forward_move_copy_or_delete(self):
        text = (WORKFLOWS / "02-email-to-attio" / "reply_drafts.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreplyall",
            r"/forward", r"createforward", r"/move", r"/copy", r"permanentdelete",
            r"\"delete\"", r"\"patch\"", r"\"put\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in reply_drafts.py")

    def test_r7_the_permission_list_is_still_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])

    def test_r7_the_draft_step_makes_no_move_and_sends_nothing_that_sends(self):
        world = PretendOutlook()
        self.make(world)
        for request in world.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/move", "/copy", "/send", "/forward", "replyall", "/reply"):
                self.assertNotIn(word, path)
            self.assertIn(request.get_method(), ("GET", "POST"))

    # ---- Case R8: nothing leaks and nothing is written locally -------------------------------------------------------

    def test_r8_it_prints_nothing_and_opens_no_file(self):
        # self.call() fails the test if a file is opened or anything is printed.
        for world in (PretendOutlook(), PretendOutlook(markers=["orig-1"]), PretendOutlook(existing_drafts=[("d", "conv-1")])):
            self.make(world)
        self.make(PretendOutlook(), saved=answer(draft_reply=None))

    def test_r8_no_problem_message_holds_the_token_the_draft_the_address_or_the_name(self):
        scenarios = [
            PretendOutlook(errors={"create": http_error(403)}),
            PretendOutlook(errors={"create": http_error(500)}),
            PretendOutlook(errors={"marker_read": urllib.error.URLError("down")}),
            PretendOutlook(errors={"verify_message": http_error(500)}),
            PretendOutlook(errors={"marker_write": http_error(500)}),
            PretendOutlook(create_to=[OTHER]),
        ]
        for world in scenarios:
            message = self.make_raises(world)
            for private in (FAKE_TOKEN, SENDER, OTHER, CLEAN, "Sam", "Fake"):
                self.assertNotIn(private, message)

    # ---- Case R9: planning without sending anything --------------------------------------------------------------------

    def test_r9_a_clean_lead_would_get_a_draft(self):
        self.assertIsNone(reply_drafts.reply_skip_reason(original(), answer()))

    def test_r9_the_same_reasons_are_given_without_any_request(self):
        def boom(*a, **k):
            raise AssertionError("planning must not send any request")

        with mock.patch("urllib.request.urlopen", boom):
            for name, (message_options, answer_options, word) in self.SKIPS.items():
                with self.subTest(case=name):
                    reason = reply_drafts.reply_skip_reason(original(**message_options), answer(**answer_options))
                    self.assertIsInstance(reason, str)
                    self.assertIn(word, reason)
                    for private in (SENDER, OTHER, "boss@example.com", CLEAN, "Sam"):
                        self.assertNotIn(private, reason)

    # ---- Case R10: looking without changing anything (used by the dry run of the one-off check) ---------------------------------

    def looked(self, world):
        return self.call(world, reply_drafts.draft_already_made, FAKE_TOKEN, original())

    def test_r10_nothing_there_means_no_and_only_two_reads_are_sent(self):
        world = PretendOutlook()
        self.assertIs(self.looked(world), False)
        self.assertEqual(world.changes(), [])
        self.assertEqual(world.paths(), ["/v1.0/me/messages/orig-1/extensions/" + MARKER, "/v1.0/me/mailFolders/drafts/messages"])

    def test_r10_a_marker_means_yes_after_one_read(self):
        world = PretendOutlook(markers=["orig-1"])
        self.assertIs(self.looked(world), True)
        self.assertEqual(len(world.requests), 1)
        self.assertEqual(world.changes(), [])

    def test_r10_a_draft_in_the_conversation_means_yes_and_writes_no_marker(self):
        world = PretendOutlook(existing_drafts=[("mine-1", "conv-1")])
        self.assertIs(self.looked(world), True)
        self.assertEqual(world.changes(), [])
        self.assertEqual(world.markers, set())

    def test_r10_a_failed_read_is_not_taken_to_mean_no(self):
        for step in ("marker_read", "drafts_check"):
            with self.subTest(step=step):
                world = PretendOutlook(errors={step: http_error(500)})
                with self.assertRaises(reply_drafts.ReplyDraftProblem):
                    self.looked(world)
                self.assertEqual(world.changes(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
