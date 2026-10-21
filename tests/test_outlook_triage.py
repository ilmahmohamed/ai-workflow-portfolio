"""Pretend-data checks for workflows/02-email-to-attio/outlook_triage.py.

These follow tests/cases/04-outlook-triage.md (which is not changed).
Everything here is fake and self-contained:
  - a pretend Outlook and a pretend Gemini answer in place of the real ones,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened),
  - the state file, config and prompts all live in a throwaway folder.

Run:  python tests\\test_outlook_triage.py
"""

import copy
import datetime
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

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "01-outlook-to-slack"))
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "02-email-to-attio"))

import extract_leads  # noqa: E402
import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402
import outlook_triage  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()
GRAPH = "https://graph.microsoft.com/v1.0"

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_KEY = "FAKE-GEMINI-KEY-SECRET"
FAKE_ENV = {"MS_CLIENT_ID": "fake-client-id", "MS_TOKEN_CACHE": FAKE_CACHE, "GEMINI_API_KEY": FAKE_KEY}
SECRETS = (FAKE_TOKEN, FAKE_CACHE, FAKE_KEY)
BODY_MARKER = "PRIVATE-EMAIL-BODY-MARKER"

NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=datetime.timezone.utc)
RECENT = "2026-10-20T09:00:00Z"
OLD = "2026-10-01T09:00:00Z"

OUR_CATEGORIES = ["Lead - Demo", "Lead - Pricing", "Lead - Other", "Needs review", "Not a lead"]


def msg(message_id, subject, categories=(), received=RECENT, sender="sam@example.com", body="Hello there"):
    return {
        "id": message_id,
        "subject": subject,
        "from": {"emailAddress": {"address": sender, "name": "Sam"}},
        "receivedDateTime": received,
        "categories": list(categories),
        "body": {"contentType": "text", "content": f"{body} {BODY_MARKER}"},
    }


def lead_reply(request_type, urgency=1):
    return json.dumps({
        "is_lead": True,
        "name": "Sam",
        "email": "sam@example.com",
        "company": None,
        "role": None,
        "request_type": request_type,
        "urgency": urgency,
        "summary": "A short summary.",
    })


NOT_LEAD = json.dumps({"is_lead": False})


def http_error(code):
    return urllib.error.HTTPError(GRAPH + "/x", code, "pretend", {}, io.BytesIO(b""))


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
    """A pretend Outlook mailbox with an Inbox and a Leads folder. Records every request."""

    def __init__(self, inbox=(), leads=(), has_leads_folder=True, patch_errors=None,
                 move_errors=None, unreachable=False, page_size=None):
        self.inbox = {m["id"]: copy.deepcopy(m) for m in inbox}
        self.leads = {m["id"]: copy.deepcopy(m) for m in leads}
        self.has_leads_folder = has_leads_folder
        self.patch_errors = patch_errors or {}
        self.move_errors = move_errors or {}
        self.unreachable = unreachable
        self.page_size = page_size
        self.requests = []
        self.patches = []
        self.moves = []
        self.unexpected = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        if self.unreachable:
            raise urllib.error.URLError("pretend: no internet")
        method = request.get_method()
        parsed = urllib.parse.urlparse(request.full_url)
        path = parsed.path.replace("/v1.0", "", 1)
        query = urllib.parse.parse_qs(parsed.query)

        if method == "GET" and path == "/me/mailFolders":
            folders = [{"id": "inbox-id", "displayName": "Inbox"}]
            if self.has_leads_folder:
                folders.append({"id": "leads-id", "displayName": "Leads"})
            return FakeResponse(json.dumps({"value": folders}).encode())
        if method == "GET" and path == "/me/mailFolders/inbox/childFolders":
            return FakeResponse(json.dumps({"value": []}).encode())
        if method == "GET" and path == "/me/mailFolders/inbox/messages":
            values = [copy.deepcopy(m) for m in self.inbox.values()]
            answer = {}
            if self.page_size:
                skip = int(query.get("$skip", ["0"])[0])
                chunk = values[skip:skip + self.page_size]
                if skip + self.page_size < len(values):
                    answer["@odata.nextLink"] = f"{GRAPH}/me/mailFolders/inbox/messages?%24skip={skip + self.page_size}"
                values = chunk
            answer["value"] = values
            return FakeResponse(json.dumps(answer).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)", path)
        if method == "PATCH" and found:
            message_id = urllib.parse.unquote(found.group(1))
            body = json.loads(request.data.decode())
            self.patches.append((message_id, body))
            error = self.patch_errors.get(len(self.patches))
            if error is not None:
                raise error
            target = self.inbox.get(message_id) or self.leads.get(message_id)
            if target is not None:
                target["categories"] = list(body["categories"])
                return FakeResponse(json.dumps(target).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)/move", path)
        if method == "POST" and found:
            message_id = urllib.parse.unquote(found.group(1))
            body = json.loads(request.data.decode())
            self.moves.append((message_id, body))
            error = self.move_errors.get(len(self.moves))
            if error is not None:
                raise error
            moved = self.inbox.pop(message_id, None)
            if moved is not None:
                moved["id"] = "moved-" + message_id
                self.leads[moved["id"]] = moved
                return FakeResponse(json.dumps({"id": moved["id"]}).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]

    def categories(self, message_id):
        return (self.inbox.get(message_id) or self.leads.get(message_id))["categories"]

    def where(self, message_id):
        if message_id in self.inbox:
            return "inbox"
        if "moved-" + message_id in self.leads or message_id in self.leads:
            return "leads"
        return "gone"


class Raise:
    def __init__(self, error):
        self.error = error


class Status:
    def __init__(self, code, message="pretend error"):
        self.code = code
        self.message = message


class FakeHTTP:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class PretendGemini:
    """A pretend Gemini. It decides by a word in the email, and records every request."""

    def __init__(self, replies=None):
        self.replies = {k: list(v) if isinstance(v, list) else [v] for k, v in (replies or {}).items()}
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        headers = headers or {}
        text = json["contents"][0]["parts"][0]["text"]
        self.calls.append({"url": url, "headers": headers, "text": text})
        if headers.get("x-goog-api-key") != FAKE_KEY:
            return FakeHTTP(400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}})
        for marker, queue in self.replies.items():
            if marker in text:
                reply = queue.pop(0) if len(queue) > 1 else queue[0]
                break
        else:
            raise AssertionError("the pretend Gemini was sent an email it knows nothing about")
        if isinstance(reply, Raise):
            raise reply.error
        if isinstance(reply, Status):
            return FakeHTTP(reply.code, {"error": {"code": reply.code, "message": reply.message}})
        return FakeHTTP(200, {"candidates": [{"content": {"parts": [{"text": reply}]}}]})

    def asked_about(self):
        return [c["text"] for c in self.calls]


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


class TriageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        self.env_path = self.tmp / ".env"
        self.env_path.write_text("OTHER=1\n", encoding="utf-8")
        self.assertFalse(touches_real_env(self.env_path))

        self.config_path = self.tmp / "config.json"
        self.config_path.write_text(json.dumps({"model": "fake-model-a"}), encoding="utf-8")
        self.prompts_dir = self.tmp / "prompts"
        self.prompts_dir.mkdir()
        (self.prompts_dir / "v1.md").write_text("PROMPT-V1: read the email.", encoding="utf-8")
        (self.prompts_dir / "v2.md").write_text("PROMPT-V2: read the email carefully.", encoding="utf-8")
        self.state_path = self.tmp / "triage-state.json"

        for module, name, value in (
            (outlook_signin, "ENV_PATH", self.env_path),
            (outlook_to_slack, "ENV_PATH", self.env_path),
            (extract_leads, "ENV_PATH", self.env_path),
            (extract_leads, "CONFIG_PATH", self.config_path),
            (extract_leads, "PROMPTS_DIR", self.prompts_dir),
            (outlook_triage, "STATE_PATH", self.state_path),
        ):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = mock.patch.object(outlook_triage, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

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

        import socket

        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.sleeps = []
        patcher = mock.patch("time.sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- helpers ---------------------------------------------------------

    def run_main(self, outlook, gemini, argv=(), remove=(), token_error=None):
        """Run main() against the pretend Outlook and Gemini. Returns (code, output)."""
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        if token_error is not None:
            token_patch = mock.patch.object(outlook_triage, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(outlook_triage, "get_access_token", return_value=FAKE_TOKEN)
        env_before = self.env_path.read_bytes()
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["outlook_triage.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", outlook.urlopen), \
                mock.patch("requests.post", gemini.post), \
                token_patch:
            for name in remove:
                os.environ.pop(name, None)
            code = outlook_triage.main()
        output = buffer.getvalue().decode("utf-8")
        saved = self.saved_text()
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a token or key was printed on screen")
            self.assertNotIn(secret, saved, "a token or key was saved to a file")
            for request in outlook.requests:
                self.assertNotIn(secret, request.full_url)
            for call in gemini.calls:
                self.assertNotIn(secret, call["url"])
        self.assertNotIn(BODY_MARKER, saved, "email text was saved to a file")
        self.assertEqual(outlook.unexpected, [], "the script sent an unexpected request to Outlook")
        self.assertEqual(self.env_path.read_bytes(), env_before, ".env must not change")
        return code, output

    def saved_text(self):
        parts = []
        for path in self.tmp.iterdir():
            if path.is_file() and path.name not in (".env", "config.json"):
                parts.append(path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def live(self, outlook, gemini, *extra, **kwargs):
        return self.run_main(outlook, gemini, ["--live", *extra], **kwargs)

    # ---- Case 1: a demo lead ----------------------------------------------

    def test_case1_demo_lead_is_categorised_and_moved(self):
        outlook = PretendOutlook([msg("m1", "Demo please", categories=["Blue"])])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.patches, [("m1", {"categories": ["Blue", "Lead - Demo"]})])
        self.assertEqual(outlook.moves, [("m1", {"destinationId": "leads-id"})])
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertIn("m1", self.state()["emails"])
        self.assertEqual(self.state()["emails"]["m1"]["outcome"], "Lead - Demo")

    # ---- Case 2: a pricing lead ----------------------------------------------

    def test_case2_pricing_lead_is_categorised_and_moved(self):
        outlook = PretendOutlook([msg("m1", "Prices please")])
        gemini = PretendGemini({"Prices please": lead_reply("pricing")})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.categories("moved-m1"), ["Lead - Pricing"])
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(self.state()["emails"]["m1"]["outcome"], "Lead - Pricing")

    # ---- Case 3: partnership or other lead ------------------------------------

    def test_case3_partnership_and_other_leads_get_lead_other_and_move(self):
        for request_type in ("partnership", "other"):
            with self.subTest(request_type=request_type):
                outlook = PretendOutlook([msg("m1", "Let us work together")])
                gemini = PretendGemini({"Let us work together": lead_reply(request_type)})
                self.state_path.unlink(missing_ok=True)
                code, _ = self.live(outlook, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(outlook.categories("moved-m1"), ["Lead - Other"])
                self.assertEqual(outlook.where("m1"), "leads")
                self.assertEqual(self.state()["emails"]["m1"]["outcome"], "Lead - Other")

    # ---- Case 4: a non-lead (newsletter) --------------------------------------

    def test_case4_newsletter_is_not_a_lead_and_stays_in_the_inbox(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.categories("m1"), ["Not a lead"])
        self.assertEqual(outlook.where("m1"), "inbox")
        self.assertEqual(outlook.moves, [])
        self.assertEqual(self.state()["emails"]["m1"]["outcome"], "Not a lead")

    # ---- Case 5: an existing customer's support email --------------------------

    def test_case5_support_email_is_not_a_lead_and_not_moved(self):
        outlook = PretendOutlook([msg("m1", "Invoice copy please")])
        gemini = PretendGemini({"Invoice copy please": NOT_LEAD})
        self.live(outlook, gemini)
        self.assertEqual(outlook.categories("m1"), ["Not a lead"])
        self.assertEqual(outlook.where("m1"), "inbox")
        self.assertEqual(outlook.moves, [])

    # ---- Case 6: Gemini is unsure -------------------------------------------------

    def test_case6_incomplete_or_odd_answers_need_review(self):
        replies = {
            "no type": json.dumps({"is_lead": True, "name": "Sam", "email": "s@example.com",
                                   "company": None, "role": None, "urgency": 1}),
            "odd type": lead_reply("buy-everything"),
            "no flag": json.dumps({"name": "Sam"}),
            "text flag": json.dumps({"is_lead": "maybe"}),
        }
        for subject, reply in replies.items():
            with self.subTest(subject=subject):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", subject)])
                gemini = PretendGemini({subject: reply})
                code, _ = self.live(outlook, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(outlook.categories("m1"), ["Needs review"])
                self.assertEqual(outlook.where("m1"), "inbox")
                self.assertEqual(outlook.moves, [])
                self.assertEqual(self.state()["emails"]["m1"]["outcome"], "Needs review")

    # ---- Case 7: broken reply ----------------------------------------------------------

    def test_case7_broken_reply_needs_review_and_the_run_continues(self):
        outlook = PretendOutlook([
            msg("m1", "First one", received="2026-10-20T08:00:00Z"),
            msg("m2", "Second one", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"First one": "Sorry, I can only chat about yoga.", "Second one": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.categories("m1"), ["Needs review"])
        self.assertEqual(outlook.where("m1"), "inbox")
        self.assertEqual(outlook.where("m2"), "leads")
        self.assertIn("Broken replies: 1", output)
        self.assertNotIn("Traceback", output)

    # ---- Case 8: the same email twice -------------------------------------------------------

    def test_case8_same_email_listed_twice_is_handled_once(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        listed_twice = [copy.deepcopy(outlook.inbox["m1"]), copy.deepcopy(outlook.inbox["m1"])]

        original = outlook.urlopen

        def twice(request, timeout=None):
            response = original(request, timeout)
            if request.get_method() == "GET" and request.full_url.split("?")[0].endswith("/inbox/messages"):
                return FakeResponse(json.dumps({"value": listed_twice}).encode())
            return response

        outlook.urlopen = twice
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(len(outlook.patches), 1)
        self.assertEqual(len(outlook.moves), 1)

    def test_case8_second_run_skips_it_because_of_the_state_file(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini)
        outlook.inbox["m1"]["categories"] = []          # someone clears the tag
        before = (len(gemini.calls), len(outlook.patches))
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual((len(gemini.calls), len(outlook.patches)), before)
        self.assertEqual(outlook.categories("m1"), [])

    # ---- Case 9: the state file is missing -------------------------------------------------------

    def test_case9_missing_state_file_starts_safely_from_the_start_date(self):
        outlook = PretendOutlook([
            msg("old1", "Ancient demo", received=OLD),
            msg("old2", "Ancient price", received="2026-10-10T09:00:00Z"),
            msg("new1", "Fresh demo", received=RECENT),
        ])
        gemini = PretendGemini({"Fresh demo": lead_reply("demo")})
        self.assertFalse(self.state_path.exists())
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Starting fresh", output)
        self.assertIn("7 days", output)
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(outlook.categories("old1"), [])
        self.assertEqual(outlook.where("old1"), "inbox")
        self.assertEqual(outlook.where("new1"), "leads")
        self.assertTrue(self.state_path.exists())

    # ---- Case 10: the state file is damaged ----------------------------------------------------------

    def test_case10_damaged_state_file_is_set_aside_and_the_run_starts_safely(self):
        self.state_path.write_text("{ this is not valid json", encoding="utf-8")
        outlook = PretendOutlook([
            msg("old1", "Ancient demo", received=OLD),
            msg("done1", "Already tagged", categories=["Not a lead"]),
            msg("new1", "Fresh demo"),
        ])
        gemini = PretendGemini({"Fresh demo": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("damaged", output)
        aside = [p for p in self.tmp.iterdir() if p.name.startswith("triage-state.json.damaged")]
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_text(encoding="utf-8"), "{ this is not valid json")
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(outlook.categories("done1"), ["Not a lead"])
        self.assertEqual(outlook.categories("old1"), [])
        self.assertIn("new1", self.state()["emails"])

    def test_case10_an_empty_state_file_is_treated_as_damaged(self):
        self.state_path.write_text("", encoding="utf-8")
        outlook = PretendOutlook([msg("new1", "Fresh demo")])
        gemini = PretendGemini({"Fresh demo": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("damaged", output)

    # ---- Case 11: a large old Inbox on the first run -----------------------------------------------------

    def test_case11_only_recent_emails_and_at_most_the_maximum_are_handled(self):
        old = [msg(f"old{i}", f"Old thing {i}", received=OLD) for i in range(30)]
        recent = [msg(f"new{i}", f"New thing {i}", received=f"2026-10-20T0{i}:00:00Z") for i in range(5)]
        outlook = PretendOutlook(old + recent)
        gemini = PretendGemini({f"New thing {i}": NOT_LEAD for i in range(5)})
        with mock.patch.object(outlook_triage, "MAX_PER_RUN", 3):
            code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 3)
        self.assertIn("2 still waiting", output)
        for i in range(30):
            self.assertEqual(outlook.categories(f"old{i}"), [])
        handled = [p[0] for p in outlook.patches]
        self.assertEqual(handled, ["new0", "new1", "new2"], "the oldest of the new emails go first")

    def test_case11_the_next_run_picks_up_the_rest(self):
        recent = [msg(f"new{i}", f"New thing {i}", received=f"2026-10-20T0{i}:00:00Z") for i in range(5)]
        outlook = PretendOutlook(recent)
        gemini = PretendGemini({f"New thing {i}": NOT_LEAD for i in range(5)})
        with mock.patch.object(outlook_triage, "MAX_PER_RUN", 3):
            self.live(outlook, gemini)
            self.live(outlook, gemini)
        self.assertEqual(len(gemini.calls), 5)
        self.assertEqual(sorted(self.state()["emails"]), [f"new{i}" for i in range(5)])

    def test_case11_a_long_inbox_across_several_pages_is_read_fully(self):
        recent = [msg(f"new{i}", f"New thing {i}", received=f"2026-10-20T0{i}:00:00Z") for i in range(5)]
        outlook = PretendOutlook(recent, page_size=2)
        gemini = PretendGemini({f"New thing {i}": NOT_LEAD for i in range(5)})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 5)

    # ---- Case 12: the Outlook sign-in has expired -------------------------------------------------------------

    def test_case12_expired_sign_in_stops_with_a_plain_message(self):
        error = outlook_signin.SetupProblem("Your saved sign-in no longer works (reason: x). Please run outlook_signin.py again.")
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini, token_error=error)
        self.assertEqual(code, 1)
        self.assertIn("no longer works", output)
        self.assertEqual(outlook.requests, [])
        self.assertEqual(gemini.calls, [])
        self.assertFalse(self.state_path.exists())

    # ---- Case 13: Outlook refuses the write ----------------------------------------------------------------------

    def test_case13_refused_write_stops_and_leaves_that_email_alone(self):
        outlook = PretendOutlook(
            [msg("m1", "First one", received="2026-10-20T08:00:00Z"),
             msg("m2", "Second one", received="2026-10-20T09:00:00Z")],
            patch_errors={2: http_error(403)},
        )
        gemini = PretendGemini({"First one": NOT_LEAD, "Second one": NOT_LEAD})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("Outlook refused the change", output)
        self.assertIn("sign-in may need redoing", output)
        self.assertNotIn("403", output)
        self.assertEqual(outlook.categories("m2"), [])
        self.assertEqual(outlook.categories("m1"), ["Not a lead"])
        self.assertIn("m1", self.state()["emails"])
        self.assertNotIn("m2", self.state()["emails"])

    # ---- Case 14: the category is added but the move is refused ---------------------------------------------------

    def test_case14_a_refused_move_is_retried_on_the_next_run_without_asking_gemini_again(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], move_errors={1: http_error(403)})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("could not be moved", output)
        self.assertIn("Next run tries the move again", output)
        self.assertEqual(outlook.categories("m1"), ["Lead - Demo"])
        self.assertEqual(outlook.where("m1"), "inbox")
        self.assertEqual(self.state()["emails"]["m1"]["stage"], "categorised")

        outlook.move_errors = {}
        calls_before, patches_before = len(gemini.calls), len(outlook.patches)
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), calls_before, "Gemini must not be asked again")
        self.assertEqual(len(outlook.patches), patches_before, "the category must not be added twice")
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(self.state()["emails"]["m1"]["stage"], "done")

    # ---- Case 15: Gemini is down ------------------------------------------------------------------------------------

    def test_case15_gemini_unreachable_stops_and_leaves_the_email_alone(self):
        outlook = PretendOutlook(
            [msg("m1", "First one", received="2026-10-20T08:00:00Z"),
             msg("m2", "Second one", received="2026-10-20T09:00:00Z")]
        )
        gemini = PretendGemini({
            "First one": NOT_LEAD,
            "Second one": Raise(requests.exceptions.ConnectionError("down")),
        })
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the AI service", output)
        self.assertNotIn("ConnectionError", output)
        self.assertEqual(outlook.categories("m2"), [])
        self.assertNotIn("m2", self.state()["emails"])
        self.assertIn("m1", self.state()["emails"])

    def test_case15_a_gemini_server_error_is_treated_the_same(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": Status(503, "overloaded")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the AI service", output)
        self.assertEqual(outlook.changes(), [])

    # ---- Case 16: the free limit is reached -----------------------------------------------------------------------------

    def test_case16_free_limit_waits_once_then_stops(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": Status(429, "slow down")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("free limit", output)
        self.assertIn(extract_leads.RETRY_WAIT_SECONDS, self.sleeps)
        self.assertEqual(len(gemini.calls), 2, "one try plus one retry")
        self.assertEqual(outlook.changes(), [])

    # ---- Case 17: key rejected or missing -------------------------------------------------------------------------------------

    def test_case17_rejected_key_stops_with_a_plain_message(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": Status(403, "denied")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("Gemini did not accept the key", output)
        self.assertEqual(outlook.changes(), [])
        self.assertNotIn("m1", self.state()["emails"] if self.state_path.exists() else {})

    def test_case17_missing_key_stops_before_anything_happens(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini, remove=["GEMINI_API_KEY"])
        self.assertEqual(code, 1)
        self.assertIn("I can't find GEMINI_API_KEY", output)
        self.assertEqual(gemini.calls, [])
        self.assertEqual(outlook.changes(), [])
        self.assertFalse(self.state_path.exists())

    # ---- Case 18: already has one of our four categories ----------------------------------------------------------------------------

    def test_case18_emails_with_our_categories_are_skipped(self):
        inbox = [msg(f"m{i}", f"Subject {i}", categories=[name]) for i, name in enumerate(OUR_CATEGORIES)]
        inbox.append(msg("lower", "Lower case", categories=["needs review"]))
        outlook = PretendOutlook(inbox)
        gemini = PretendGemini()
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(gemini.calls, [])
        self.assertEqual(outlook.changes(), [])
        self.assertIn("skipped", output.lower())

    def test_case18_own_categories_do_not_cause_a_skip_and_are_kept(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter", categories=["Blue", "Red"])])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini)
        self.assertEqual(outlook.categories("m1"), ["Blue", "Red", "Not a lead"])

    # ---- Case 19: already in the Leads folder ------------------------------------------------------------------------------------------

    def test_case19_the_leads_folder_is_never_read_or_touched(self):
        outlook = PretendOutlook(
            [msg("m1", "Weekly newsletter")],
            leads=[msg("lead1", "Already a lead", categories=["Lead - Demo"])],
        )
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 1)
        for request in outlook.requests:
            self.assertNotIn("lead1", request.full_url)
        self.assertEqual(outlook.categories("lead1"), ["Lead - Demo"])

    # ---- Case 20: the Leads folder is missing ---------------------------------------------------------------------------------------------

    def test_case20_missing_leads_folder_stops_before_anything_changes(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], has_leads_folder=False)
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("couldn't find a folder called Leads", output)
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(gemini.calls, [])
        self.assertFalse(self.state_path.exists())

    # ---- Case 21: a dry run changes nothing ----------------------------------------------------------------------------------------------------

    def test_case21_dry_run_lists_emails_and_changes_nothing(self):
        outlook = PretendOutlook([msg("m1", "Demo please"), msg("m2", "Weekly newsletter")])
        gemini = PretendGemini()
        code, output = self.run_main(outlook, gemini, [])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertIn("would send to Gemini: sam@example.com, Demo please", output)
        self.assertIn("would send to Gemini: sam@example.com, Weekly newsletter", output)
        self.assertEqual(set(outlook.methods()), {"GET"})
        self.assertEqual(gemini.calls, [])
        self.assertFalse(self.state_path.exists())

    def test_case21_dry_run_needs_no_gemini_key(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        code, output = self.run_main(outlook, PretendGemini(), [], remove=["GEMINI_API_KEY"])
        self.assertEqual(code, 0)
        self.assertIn("would send to Gemini", output)

    def test_case21_dry_run_does_not_touch_a_damaged_state_file(self):
        self.state_path.write_text("not json", encoding="utf-8")
        outlook = PretendOutlook([msg("m1", "Demo please")])
        code, output = self.run_main(outlook, PretendGemini(), [])
        self.assertEqual(code, 0)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), "not json")
        self.assertEqual([p.name for p in self.tmp.iterdir() if "damaged" in p.name], [])

    def test_default_is_a_dry_run(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        code, output = self.run_main(outlook, PretendGemini(), [])
        self.assertIn("Dry run", output)
        self.assertEqual(outlook.changes(), [])

    # ---- Case 22: nothing is sent, replied to or deleted -----------------------------------------------------------------------------------------------

    def test_case22_only_category_updates_and_moves_to_leads_are_ever_sent(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T07:00:00Z"),
            msg("m2", "Weekly newsletter", received="2026-10-20T08:00:00Z"),
            msg("m3", "Prices please", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({
            "Demo please": lead_reply("demo"),
            "Weekly newsletter": NOT_LEAD,
            "Prices please": lead_reply("pricing"),
        })
        self.live(outlook, gemini)
        self.assertEqual(set(outlook.methods()), {"GET", "PATCH", "POST"})
        for request in outlook.changes():
            url = request.full_url.lower()
            if request.get_method() == "POST":
                self.assertTrue(url.split("?")[0].endswith("/move"))
            for word in ("/send", "/reply", "/forward", "/copy", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, url)
        for _, body in outlook.patches:
            self.assertEqual(set(body), {"categories"})
        for _, body in outlook.moves:
            self.assertEqual(set(body), {"destinationId"})

    # ---- Case 23: the token and key are never shown -----------------------------------------------------------------------------------------------------

    def test_case23_token_and_key_are_sent_to_the_right_places_but_never_shown(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.requests[0].get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertEqual(gemini.calls[0]["headers"].get("x-goog-api-key"), FAKE_KEY)
        self.assertNotIn("m1", output.replace("moved", ""))  # ids are not needed on screen

    def test_case23_state_file_holds_no_email_text(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        self.assertNotIn(BODY_MARKER, self.state_path.read_text(encoding="utf-8"))

    # ---- Cases 24 to 29: --only-subject ---------------------------------------------------------------------------------------------------------------------

    def triage_test_inbox(self):
        return PretendOutlook([
            msg("t1", "TRIAGE-TEST demo", received="2026-10-20T07:00:00Z"),
            msg("t2", "Re: triage-test newsletter", received="2026-10-20T08:00:00Z"),
            msg("x1", "Real customer demo", received="2026-10-20T09:00:00Z"),
            msg("x2", "Real newsletter", received="2026-10-20T10:00:00Z"),
        ])

    def triage_test_gemini(self):
        return PretendGemini({
            "TRIAGE-TEST demo": lead_reply("demo"),
            "triage-test newsletter": NOT_LEAD,
            "Real customer demo": lead_reply("demo"),
            "Real newsletter": NOT_LEAD,
        })

    def test_case24_only_matching_emails_are_handled(self):
        outlook, gemini = self.triage_test_inbox(), self.triage_test_gemini()
        code, output = self.live(outlook, gemini, "--only-subject", "TRIAGE-TEST")
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 2)
        self.assertEqual(outlook.where("t1"), "leads")
        self.assertEqual(outlook.categories("t2"), ["Not a lead"])
        self.assertEqual(outlook.categories("x1"), [])
        self.assertEqual(outlook.categories("x2"), [])
        self.assertEqual(sorted(self.state()["emails"]), ["t1", "t2"])

    def test_case25_no_matching_email_says_so_and_changes_nothing(self):
        outlook, gemini = self.triage_test_inbox(), self.triage_test_gemini()
        code, output = self.live(outlook, gemini, "--only-subject", "NO-SUCH-TEXT")
        self.assertEqual(code, 0)
        self.assertIn("No new email", output)
        self.assertIn("NO-SUCH-TEXT", output)
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(gemini.calls, [])

    def test_case26_dry_run_shows_only_matching_emails(self):
        outlook, gemini = self.triage_test_inbox(), self.triage_test_gemini()
        code, output = self.run_main(outlook, gemini, ["--only-subject", "TRIAGE-TEST"])
        self.assertEqual(code, 0)
        self.assertIn("TRIAGE-TEST demo", output)
        self.assertIn("triage-test newsletter", output)
        self.assertNotIn("Real customer demo", output)
        self.assertNotIn("Real newsletter", output)
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(gemini.calls, [])

    def test_case27_other_emails_are_still_handled_by_a_later_normal_run(self):
        outlook, gemini = self.triage_test_inbox(), self.triage_test_gemini()
        self.live(outlook, gemini, "--only-subject", "TRIAGE-TEST")
        calls_before = len(gemini.calls)
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls) - calls_before, 2, "only the two others are new")
        self.assertEqual(outlook.where("x1"), "leads")
        self.assertEqual(outlook.categories("x2"), ["Not a lead"])
        self.assertEqual(sorted(self.state()["emails"]), ["t1", "t2", "x1", "x2"])

    def test_case28_the_option_never_widens_the_search(self):
        outlook = PretendOutlook([
            msg("old", "TRIAGE-TEST ancient", received=OLD),
            msg("tagged", "TRIAGE-TEST tagged", categories=["Needs review"]),
        ])
        gemini = PretendGemini()
        code, _ = self.live(outlook, gemini, "--only-subject", "TRIAGE-TEST")
        self.assertEqual(code, 0)
        self.assertEqual(gemini.calls, [])
        self.assertEqual(outlook.changes(), [])

    def test_case29_empty_text_is_refused_before_anything_is_read(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                outlook, gemini = self.triage_test_inbox(), self.triage_test_gemini()
                code, output = self.live(outlook, gemini, "--only-subject", text)
                self.assertEqual(code, 1)
                self.assertIn("must not be empty", output)
                self.assertEqual(outlook.requests, [])
                self.assertEqual(gemini.calls, [])

    # ---- Default behaviour -------------------------------------------------------------------------------------------------------------------------------

    def test_case30_when_gemini_cannot_be_reached_the_message_says_why(self):
        scenarios = [
            (Raise(requests.exceptions.Timeout("slow")), "(the request timed out)"),
            (Raise(requests.exceptions.ConnectionError("down")), "(no connection)"),
            (Status(503, f"overloaded {FAKE_KEY} {BODY_MARKER}"), "(Gemini answered with code 503)"),
        ]
        for reply, hint in scenarios:
            with self.subTest(hint=hint):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", "Demo please")])
                gemini = PretendGemini({"Demo please": reply})
                code, output = self.live(outlook, gemini)
                self.assertEqual(code, 1)
                self.assertIn("I couldn't reach the AI service", output)
                self.assertIn(hint, output)
                self.assertIn("Stopped", output)
                self.assertNotIn("overloaded", output)
                self.assertNotIn(BODY_MARKER, output)
                self.assertEqual(outlook.changes(), [])
                self.assertFalse(self.state_path.exists())

    def test_prompt_v2_and_the_config_model_are_used(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        self.assertIn("PROMPT-V2", gemini.asked_about()[0])
        self.assertNotIn("PROMPT-V1", gemini.asked_about()[0])
        self.assertIn("fake-model-a", gemini.calls[0]["url"])

    def test_the_email_text_reaches_gemini(self):
        outlook = PretendOutlook([msg("m1", "Demo please", body="I would like a demo")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        sent = gemini.asked_about()[0]
        self.assertIn("sam@example.com", sent)
        self.assertIn("Subject: Demo please", sent)
        self.assertIn("I would like a demo", sent)

    def test_a_pause_is_taken_between_gemini_calls(self):
        outlook = PretendOutlook([
            msg("m1", "First one", received="2026-10-20T08:00:00Z"),
            msg("m2", "Second one", received="2026-10-20T09:00:00Z"),
            msg("m3", "Third one", received="2026-10-20T10:00:00Z"),
        ])
        gemini = PretendGemini({"First one": NOT_LEAD, "Second one": NOT_LEAD, "Third one": NOT_LEAD})
        self.live(outlook, gemini)
        self.assertEqual(self.sleeps.count(extract_leads.PAUSE_SECONDS), 2)

    def test_no_new_emails_is_a_calm_finish(self):
        outlook = PretendOutlook([])
        code, output = self.live(outlook, PretendGemini())
        self.assertEqual(code, 0)
        self.assertIn("No new emails", output)
        self.assertEqual(outlook.changes(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
