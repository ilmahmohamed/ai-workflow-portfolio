"""Pretend-data checks for workflows/02-email-to-attio/lead_slack.py (not written yet).

These follow tests/cases/13-slack-step.md, Part A. They say what the new module must offer:

  lead_slack.announce_lead(token, message, category, webhook_url) -> "posted" or "exists"
        Posts one short message to Slack for a lead, once: it checks a marker on the email first and saves
        the marker after a successful post. Problems are raised as lead_slack.LeadSlackProblem (plain words).
  lead_slack.slack_text(category, sender, subject) -> the one-line message text
  lead_slack.post_to_slack(webhook_url, text)         the only place a message is posted
  lead_slack.graph(token, method, path, params=None, body=None)   the guard for Outlook (the marker only);
        refused requests raise lead_slack.BlockedRequest before anything leaves the computer
  lead_slack.MARKER_NAME, lead_slack.PAUSE_SECONDS, lead_slack.BlockedRequest, lead_slack.LeadSlackProblem

message is an email as Outlook lists it (id, subject, from).

Everything here is fake and self-contained: a pretend Outlook and a pretend Slack, the internet blocked, and
while the code runs, opening any local file stops the test and anything printed fails it.

Run:  python tests\\test_lead_slack.py
"""

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

import lead_slack  # noqa: E402
import outlook_signin  # noqa: E402

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
SECRET_PART = "FAKE-WEBHOOK-SECRET-VALUE"
WEBHOOK = f"https://hooks.slack.com/services/T000FAKE/B000FAKE/{SECRET_PART}"
MARKER = "com.novawellness.slackposted"
SENDER = "sam.fake@example.com"


def message(mid="msg-1", subject="Demo please", sender=SENDER, name="Sam Fake"):
    return {"id": mid, "subject": subject,
            "from": {"emailAddress": {"address": sender, "name": name}}}


def http_error(code):
    return urllib.error.HTTPError("https://pretend.test/x", code, "pretend", {}, io.BytesIO(b""))


class FakeResponse:
    def __init__(self, body=b"ok"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def marker_body(data="{}"):
    return {"@odata.type": "microsoft.graph.openTypeExtension", "extensionName": MARKER,
            "version": 1, "data": data}


class PretendWorld:
    """A pretend Outlook (only the marker) and a pretend Slack, both strict. Records everything in order.

    errors: {"marker_read" | "slack" | "marker_write": the error to raise}."""

    def __init__(self, markers=(), errors=None):
        self.markers = set(markers)
        self.errors = dict(errors or {})
        self.events = []
        self.posts = []
        self.marker_bodies = []
        self.requests = []
        self.unexpected = []

    def fail(self, step):
        error = self.errors.get(step)
        if error is not None:
            raise error

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        method = request.get_method()
        url = request.full_url
        parsed = urllib.parse.urlparse(url)

        if url.startswith("https://hooks.slack.com/services/"):
            body = json.loads(request.data.decode())
            if method != "POST" or set(body) != {"text"} or not isinstance(body["text"], str):
                raise http_error(400)
            self.events.append("slack")
            self.fail("slack")
            self.posts.append(body)
            return FakeResponse(b"ok")

        path = parsed.path.replace("/v1.0", "", 1)
        found = re.fullmatch(r"/me/messages/([^/]+)/extensions/([^/]+)", path)
        if method == "GET" and found:
            if parsed.query:
                raise http_error(400)
            self.events.append("marker_read")
            self.fail("marker_read")
            if urllib.parse.unquote(found.group(1)) in self.markers and urllib.parse.unquote(found.group(2)) == MARKER:
                return FakeResponse(json.dumps({"extensionName": MARKER, "version": 1, "data": "{}"}).encode())
            raise http_error(404)

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions", path)
        if method == "POST" and found:
            body = json.loads(request.data.decode())
            if parsed.query or set(body) != {"@odata.type", "extensionName", "version", "data"} \
                    or body["extensionName"] != MARKER or body["version"] != 1 or not isinstance(body["data"], str):
                raise http_error(400)
            self.events.append("marker_write")
            self.fail("marker_write")
            self.marker_bodies.append(body)
            self.markers.add(urllib.parse.unquote(found.group(1)))
            return FakeResponse(json.dumps(body).encode())

        self.unexpected.append(f"{method} {url}")
        raise urllib.error.URLError("pretend world: unexpected request")


class LeadSlackTests(unittest.TestCase):
    def setUp(self):
        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, world, function, *args):
        """Call a lead_slack function against the pretend world.

        While it runs, opening any local file stops the test and anything printed fails it."""
        buffer = io.StringIO()

        def no_files(*a, **k):
            raise AssertionError("TEST SAFETY: the Slack code must not open any local file")

        with mock.patch("urllib.request.urlopen", world.urlopen), \
                mock.patch("builtins.open", no_files), \
                mock.patch.object(sys, "stdout", buffer):
            try:
                return function(*args)
            finally:
                self.assertEqual(buffer.getvalue(), "", "the Slack code must print nothing")
                self.assertEqual(world.unexpected, [], "an unexpected request was sent")

    def announce(self, world, msg=None, category="Lead - Demo", url=WEBHOOK):
        return self.call(world, lead_slack.announce_lead, FAKE_TOKEN, msg or message(), category, url)

    def announce_raises(self, world, msg=None, category="Lead - Demo", url=WEBHOOK):
        with self.assertRaises(lead_slack.LeadSlackProblem) as caught:
            self.announce(world, msg, category, url)
        return str(caught.exception)

    # ---- Case S1: one post, then a marker -------------------------------------------------

    def test_s1_one_post_then_the_marker(self):
        world = PretendWorld()
        self.assertEqual(self.announce(world), "posted")
        self.assertEqual(world.events, ["marker_read", "slack", "marker_write"])
        self.assertEqual(world.posts, [{"text": f"Lead - Demo: {SENDER}, Demo please"}])
        self.assertEqual(len(world.marker_bodies), 1)
        self.assertEqual(world.marker_bodies[0]["extensionName"], MARKER)
        self.assertEqual(world.markers, {"msg-1"})
        self.assertEqual(lead_slack.MARKER_NAME, MARKER)

    def test_s1_the_text_is_one_line_escaped_and_shortened(self):
        text = lead_slack.slack_text("Lead - Pricing", SENDER, "Q&A <now> >> go")
        self.assertEqual(text, f"Lead - Pricing: {SENDER}, Q&amp;A &lt;now&gt; &gt;&gt; go")
        text = lead_slack.slack_text("Lead - Demo", SENDER, "Hello\nthere\r\nfriend\tok")
        self.assertEqual(text, f"Lead - Demo: {SENDER}, Hello there friend ok")
        long = lead_slack.slack_text("Lead - Demo", SENDER, "word " * 200)
        self.assertLessEqual(len(long), 300)
        self.assertTrue(long.endswith("..."))
        self.assertNotIn("\n", long)

    def test_s1_a_missing_sender_or_subject_gets_a_plain_placeholder(self):
        world = PretendWorld()
        self.announce(world, msg={"id": "m", "from": None, "subject": None})
        self.assertEqual(world.posts, [{"text": "Lead - Demo: (unknown sender), (no subject)"}])
        world = PretendWorld()
        self.announce(world, msg={"id": "m", "from": {"emailAddress": {"name": "Sam Fake"}}, "subject": "Hi"})
        self.assertEqual(world.posts, [{"text": "Lead - Demo: Sam Fake, Hi"}])

    def test_s1_only_the_category_sender_and_subject_are_in_the_text(self):
        msg = message()
        msg["body"] = {"content": "PRIVATE-BODY-MARKER"}
        msg["bodyPreview"] = "PRIVATE-PREVIEW-MARKER"
        world = PretendWorld()
        self.announce(world, msg=msg)
        text = world.posts[0]["text"]
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("Sam Fake", text)

    # ---- Case S2: never twice ------------------------------------------------------------------

    def test_s2_a_marker_means_nothing_is_posted(self):
        world = PretendWorld(markers=["msg-1"])
        self.assertEqual(self.announce(world), "exists")
        self.assertEqual(world.events, ["marker_read"])
        self.assertEqual(world.posts, [])

    def test_s2_a_second_call_after_a_post_posts_nothing_more(self):
        world = PretendWorld()
        self.announce(world)
        self.assertEqual(self.announce(world), "exists")
        self.assertEqual(len(world.posts), 1)

    def test_s2_a_failed_marker_read_is_never_taken_for_no_marker(self):
        errors = [http_error(500), http_error(401), http_error(403), urllib.error.URLError("pretend: down")]
        for error in errors:
            with self.subTest(error=str(error)[:30]):
                world = PretendWorld(errors={"marker_read": error})
                message_text = self.announce_raises(world)
                self.assertEqual(world.posts, [])
                self.assertNotIn(SECRET_PART, message_text)
                self.assertNotIn(FAKE_TOKEN, message_text)

    def test_s2_the_outlook_messages_are_plain(self):
        for code_number in (401, 403):
            text = self.announce_raises(PretendWorld(errors={"marker_read": http_error(code_number)}))
            self.assertIn("sign-in may need redoing", text)
            self.assertIn("outlook_signin.py", text)
            self.assertNotIn(str(code_number), text)
        self.assertIn("refused", self.announce_raises(PretendWorld(errors={"marker_read": http_error(500)})))
        self.assertIn("couldn't reach Outlook",
                      self.announce_raises(PretendWorld(errors={"marker_read": urllib.error.URLError("down")})))

    # ---- Case S3: Slack has a problem -----------------------------------------------------------

    def test_s3_slack_errors_are_plain_and_nothing_is_marked(self):
        for code_number in (400, 403, 404, 410, 429, 500):
            with self.subTest(code=code_number):
                world = PretendWorld(errors={"slack": http_error(code_number)})
                text = self.announce_raises(world)
                self.assertIn(f"Slack refused the message (code {code_number})", text)
                self.assertNotIn(SECRET_PART, text)
                self.assertNotIn("HTTPError", text)
                self.assertEqual(world.markers, set())
                self.assertEqual(world.marker_bodies, [])

    def test_s3_slack_unreachable_is_explained(self):
        world = PretendWorld(errors={"slack": urllib.error.URLError("pretend: down")})
        text = self.announce_raises(world)
        self.assertIn("couldn't reach Slack", text)
        self.assertNotIn("URLError", text)
        self.assertNotIn(SECRET_PART, text)
        self.assertEqual(world.markers, set())

    # ---- Case S4: Outlook has a problem with the marker ---------------------------------------------

    def test_s4_a_post_that_could_not_be_marked_says_so_and_warns_about_a_second_post(self):
        for error in (http_error(500), http_error(403), urllib.error.URLError("pretend: down")):
            with self.subTest(error=str(error)[:30]):
                world = PretendWorld(errors={"marker_write": error})
                text = self.announce_raises(world)
                self.assertEqual(len(world.posts), 1)
                self.assertIn("posted to Slack", text)
                self.assertIn("could not mark", text)
                self.assertIn("twice", text)
                for private in (SECRET_PART, FAKE_TOKEN, SENDER, "Demo please"):
                    self.assertNotIn(private, text)

    def test_s4_known_limit_if_the_marker_could_not_be_saved_a_retry_posts_again(self):
        # Slack cannot be asked what it already holds, so this one small gap cannot be closed.
        world = PretendWorld(errors={"marker_write": http_error(500)})
        self.announce_raises(world)
        world.errors.clear()
        self.assertEqual(self.announce(world), "posted")
        self.assertEqual(len(world.posts), 2)

    # ---- Case S5: only safe requests --------------------------------------------------------------------

    def refused(self):
        marker = marker_body()
        return [
            ("POST", "/me/messages/abc/send", None, {}),
            ("POST", "/me/sendMail", None, {"message": {}}),
            ("POST", "/me/messages/abc/reply", None, {"comment": "x"}),
            ("POST", "/me/messages/abc/replyAll", None, {"comment": "x"}),
            ("POST", "/me/messages/abc/createReply", None, {"comment": "x"}),
            ("POST", "/me/messages/abc/forward", None, {}),
            ("POST", "/me/messages/abc/move", None, {"destinationId": "inbox"}),
            ("POST", "/me/messages/abc/copy", None, {"destinationId": "inbox"}),
            ("POST", "/me/mailFolders/drafts/messages", None, {"subject": "x"}),
            ("POST", "/me/messages/abc/extensions", None, {**marker, "extensionName": "com.novawellness.leadresult"}),
            ("POST", "/me/messages/abc/extensions", None, {**marker, "extensionName": "com.novawellness.draftmade"}),
            ("POST", "/me/messages/abc/extensions", None, {**marker, "isRead": True}),
            ("POST", "/me/messages/abc/extensions", None, {**marker, "data": {"a": 1}}),
            ("POST", "/me/messages/abc/extensions", None, {**marker, "version": "1"}),
            ("POST", "/me/messages/abc/extensions", {"x": "1"}, marker),
            ("POST", "/me/messages/abc/extensions", None, None),
            ("DELETE", "/me/messages/abc", None, None),
            ("DELETE", "/me/messages/abc/extensions/" + MARKER, None, None),
            ("PATCH", "/me/messages/abc", None, {"categories": []}),
            ("PUT", "/me/messages/abc", None, {"x": 1}),
            ("GET", "/me/messages", None, None),
            ("GET", "/me/messages/abc", None, None),
            ("GET", "/me/messages/abc", {"$select": "id,body"}, None),
            ("GET", "/me/mailFolders/inbox/messages", None, None),
            ("GET", "/me/messages/abc/extensions", None, None),
            ("GET", "/me/messages/abc/extensions/com.other.app", None, None),
            ("GET", "/me/messages/abc/extensions/" + MARKER, {"$select": "id"}, None),
            ("GET", "/me/messages/abc/send", None, None),
        ]

    def test_s5_the_outlook_guard_refuses_everything_else_before_it_leaves_the_computer(self):
        for method, path, params, body in self.refused():
            with self.subTest(method=method, path=path, params=params):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(lead_slack.BlockedRequest):
                        lead_slack.graph(FAKE_TOKEN, method, path, params=params, body=body)
                self.assertEqual(calls, [], "a refused request must never leave the code")

    def test_s5_the_outlook_guard_lets_the_two_wanted_requests_through(self):
        allowed = [
            ("GET", "/me/messages/abc%3D/extensions/" + MARKER, None, None),
            ("POST", "/me/messages/abc%3D/extensions", None, marker_body()),
        ]
        for method, path, params, body in allowed:
            with self.subTest(method=method, path=path):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    lead_slack.graph(FAKE_TOKEN, method, path, params=params, body=body)
                self.assertEqual(len(calls), 1)

    def test_s5_only_a_slack_webhook_address_is_ever_posted_to(self):
        bad_urls = [
            "http://hooks.slack.com/services/T0/B0/SECRET-IN-BAD-URL",
            "https://example.com/hook/SECRET-IN-BAD-URL",
            "https://hooks.slack.com.evil.example/services/T0/B0/SECRET-IN-BAD-URL",
            "https://hooks.slack.com/other/T0/B0/SECRET-IN-BAD-URL",
            "https://evil.example/https://hooks.slack.com/services/T0/B0/SECRET-IN-BAD-URL",
            "ftp://hooks.slack.com/services/SECRET-IN-BAD-URL",
            "SECRET-IN-BAD-URL",
            "",
            None,
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                world = PretendWorld()
                text = self.announce_raises(world, url=url)
                self.assertEqual(world.requests, [], "no request may be made for a bad address")
                if url:
                    self.assertNotIn("SECRET-IN-BAD-URL", text)
                self.assertIn("Slack", text)

    def test_s5_the_code_text_has_no_send_reply_forward_move_copy_or_delete(self):
        text = (WORKFLOWS / "02-email-to-attio" / "lead_slack.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreply",
            r"/forward", r"createforward", r"/move", r"/copy", r"permanentdelete",
            r"\"delete\"", r"\"patch\"", r"\"put\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in lead_slack.py")

    def test_s5_the_permission_list_is_still_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])

    # ---- Case S6: no leaks -----------------------------------------------------------------------------------

    def test_s6_it_prints_nothing_and_opens_no_file(self):
        # self.call() fails the test if a file is opened or anything is printed.
        self.announce(PretendWorld())
        self.announce(PretendWorld(markers=["msg-1"]))

    def test_s6_no_problem_message_holds_the_token_the_link_or_a_lead_detail(self):
        scenarios = [
            PretendWorld(errors={"marker_read": http_error(500)}),
            PretendWorld(errors={"slack": http_error(500)}),
            PretendWorld(errors={"slack": urllib.error.URLError("down")}),
            PretendWorld(errors={"marker_write": http_error(500)}),
        ]
        for world in scenarios:
            text = self.announce_raises(world)
            for private in (FAKE_TOKEN, SECRET_PART, "hooks.slack.com", SENDER, "Sam Fake", "Demo please"):
                self.assertNotIn(private, text)

    def test_s6_the_pause_between_posts_is_a_short_whole_or_decimal_number_of_seconds(self):
        self.assertIsInstance(lead_slack.PAUSE_SECONDS, (int, float))
        self.assertTrue(0 < lead_slack.PAUSE_SECONDS <= 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
