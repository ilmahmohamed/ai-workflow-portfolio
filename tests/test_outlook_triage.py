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
LIVE_SLACK_SECRET = "FAKE-LIVE-WEBHOOK-SECRET"
TEST_SLACK_SECRET = "FAKE-TEST-WEBHOOK-SECRET"
LIVE_SLACK_URL = f"https://hooks.slack.com/services/T0LIVE/B0LIVE/{LIVE_SLACK_SECRET}"
TEST_SLACK_URL = f"https://hooks.slack.com/services/T0TEST/B0TEST/{TEST_SLACK_SECRET}"
SLACK_ENV = {"SLACK_WEBHOOK_URL": LIVE_SLACK_URL, "SLACK_TEST_WEBHOOK_URL": TEST_SLACK_URL}
SECRETS = (FAKE_TOKEN, FAKE_CACHE, FAKE_KEY, LIVE_SLACK_SECRET, TEST_SLACK_SECRET)
SLACK_MARKER = "com.novawellness.slackposted"
DRAFT_MARKER = "com.novawellness.draftmade"
DRAFTS_ID = "drafts-id"
DRAFT_SELECT = "id,isDraft,parentFolderId,toRecipients,ccRecipients,bccRecipients,conversationId,bodyPreview"
BODY_MARKER = "PRIVATE-EMAIL-BODY-MARKER"

NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=datetime.timezone.utc)
RECENT = "2026-10-20T09:00:00Z"
OLD = "2026-10-01T09:00:00Z"

OUR_CATEGORIES = ["Lead - Demo", "Lead - Pricing", "Lead - Other", "Needs review", "Not a lead"]


def msg(message_id, subject, categories=(), received=RECENT, sender="sam@example.com", body="Hello there",
        name="Sam", internet_id=None, reply_to=()):
    message = {
        "id": message_id,
        "subject": subject,
        "from": {"emailAddress": {"address": sender, "name": name}},
        "replyTo": [{"emailAddress": {"address": a}} for a in reply_to],
        "conversationId": f"conv-{message_id}",
        "receivedDateTime": received,
        "categories": list(categories),
        "body": {"contentType": "text", "content": f"{body} {BODY_MARKER}"},
    }
    if internet_id:
        message["internetMessageId"] = internet_id
    return message


CATEGORY_FOR = {"demo": "Lead - Demo", "pricing": "Lead - Pricing",
                "partnership": "Lead - Other", "other": "Lead - Other"}
CLEAN_DRAFT = "Hello Sam, thank you for getting in touch about your enquiry. A member of our team will be in touch."
EXTENSION_NAME = "com.novawellness.leadresult"


def lead_answer(request_type, urgency=1, **changes):
    """What prompt v3 gets back for a lead (as a dict)."""
    answer = {
        "is_lead": True,
        "category": CATEGORY_FOR.get(request_type, "Lead - Other"),
        "name": "Sam",
        "email": "sam@example.com",
        "company": None,
        "role": None,
        "request_type": request_type,
        "urgency": urgency,
        "summary": "A short summary.",
        "draft_reply": CLEAN_DRAFT,
    }
    answer.update(changes)
    return answer


def lead_reply(request_type, urgency=1, **changes):
    return json.dumps(lead_answer(request_type, urgency, **changes))


NOT_LEAD = json.dumps({"is_lead": False, "category": "Not a lead"})


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
                 move_errors=None, unreachable=False, page_size=None, extension_errors=None,
                 draft_errors=None, create_to=None, existing_drafts=()):
        self.inbox = {m["id"]: copy.deepcopy(m) for m in inbox}
        self.leads = {m["id"]: copy.deepcopy(m) for m in leads}
        self.has_leads_folder = has_leads_folder
        self.patch_errors = patch_errors or {}
        self.move_errors = move_errors or {}
        self.unreachable = unreachable
        self.page_size = page_size
        self.extension_errors = extension_errors or {}   # {n: error} for the n-th save of an answer
        self.extensions = {}          # message id -> the saved extension (ours)
        self.extension_posts = []     # (message id, body) of every save
        self.markers = {}             # (message id, extension name) -> a marker such as the Slack one
        self.marker_posts = []        # (message id, body) of every marker saved
        self.marker_errors = {}       # {n: error} for the n-th marker save
        self.draft_errors = draft_errors or {}    # {step: error}: create, verify_message, verify_folder, drafts_check, marker_write
        self.create_to = create_to                # makes the new draft come back addressed to this address instead
        self.drafts = {}                          # draft id -> the draft as Outlook would show it
        for draft_id, conversation in existing_drafts:
            self.drafts[draft_id] = {"id": draft_id, "conversationId": conversation, "isDraft": True,
                                     "parentFolderId": DRAFTS_ID, "toRecipients": [], "ccRecipients": [],
                                     "bccRecipients": [], "bodyPreview": "Something I wrote myself"}
        self.draft_comments = []                  # the text of every create-reply request
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

        # ---- Drafts (strict, like the real Outlook: only the documented request forms are answered) ----
        if method == "GET" and path == "/me/mailFolders/drafts":
            if query != {"$select": ["id"]}:
                raise http_error(400)
            self.fail_draft("verify_folder")
            return FakeResponse(json.dumps({"id": DRAFTS_ID}).encode())
        if method == "GET" and path == "/me/mailFolders/drafts/messages":
            wanted = re.fullmatch(r"conversationId eq '(.+)'", (query.get("$filter") or [""])[0])
            if set(query) != {"$filter", "$select", "$top"} or query["$select"] != ["id"] or not wanted:
                raise http_error(400)
            self.fail_draft("drafts_check")
            found_drafts = [{"id": d["id"]} for d in self.drafts.values() if d["conversationId"] == wanted.group(1)]
            return FakeResponse(json.dumps({"value": found_drafts}).encode())
        found = re.fullmatch(r"/me/messages/([^/]+)/createReply", path)
        if method == "POST" and found:
            body = json.loads(request.data.decode())
            if parsed.query or set(body) != {"comment"} or not isinstance(body["comment"], str):
                raise http_error(400)
            self.draft_comments.append(body["comment"])
            self.fail_draft("create")
            source = self.inbox.get(urllib.parse.unquote(found.group(1))) or self.leads.get(urllib.parse.unquote(found.group(1)))
            if source is None:
                raise http_error(404)
            reply_to = [r["emailAddress"]["address"] for r in source.get("replyTo") or []]
            to = [self.create_to] if self.create_to else (reply_to or [source["from"]["emailAddress"]["address"]])
            draft_id = f"draft-{len(self.drafts) + 1}"
            preview = " ".join(re.sub(r"<[^>]+>", " ", body["comment"]).split())[:255]
            self.drafts[draft_id] = {
                "id": draft_id, "conversationId": source["conversationId"], "isDraft": True,
                "parentFolderId": DRAFTS_ID, "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                "ccRecipients": [], "bccRecipients": [], "bodyPreview": preview,
            }
            return FakeResponse(json.dumps(self.drafts[draft_id]).encode())
        found = re.fullmatch(r"/me/messages/([^/]+)", path)
        if method == "GET" and found:
            if query != {"$select": [DRAFT_SELECT]}:
                raise http_error(400)
            self.fail_draft("verify_message")
            draft = self.drafts.get(urllib.parse.unquote(found.group(1)))
            if draft is None:
                raise http_error(404)
            return FakeResponse(json.dumps(draft).encode())

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

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions/([^/]+)", path)
        if method == "GET" and found:
            # Strict, like the real Outlook: only the documented form (one extension, read by its
            # name, no query string) is answered.
            if parsed.query:
                raise http_error(400)
            saved = self.extensions.get(urllib.parse.unquote(found.group(1)))
            if saved is not None and saved["extensionName"] == urllib.parse.unquote(found.group(2)):
                return FakeResponse(json.dumps(saved).encode())
            marker = self.markers.get((urllib.parse.unquote(found.group(1)), urllib.parse.unquote(found.group(2))))
            if marker is not None:
                return FakeResponse(json.dumps(marker).encode())
            raise http_error(404)

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions", path)
        if method == "POST" and found:
            message_id = urllib.parse.unquote(found.group(1))
            body = json.loads(request.data.decode())
            if body.get("extensionName") != EXTENSION_NAME:
                # a marker (the Slack one or the draft one): kept apart from the saved answers
                if body.get("extensionName") == DRAFT_MARKER:
                    self.fail_draft("marker_write")
                self.marker_posts.append((message_id, body))
                error = self.marker_errors.get(len(self.marker_posts))
                if error is not None:
                    raise error
                if message_id in self.inbox or message_id in self.leads:
                    stored = {"@odata.type": "#microsoft.graph.openTypeExtension",
                              "id": "Microsoft.OutlookServices.OpenTypeExtension." + body["extensionName"],
                              **{k: v for k, v in body.items() if k != "@odata.type"}}
                    self.markers[(message_id, body["extensionName"])] = stored
                    return FakeResponse(json.dumps(stored).encode())
                self.unexpected.append(f"{method} {request.full_url}")
                raise urllib.error.URLError("pretend world: unknown email")
            self.extension_posts.append((message_id, body))
            error = self.extension_errors.get(len(self.extension_posts))
            if error is not None:
                raise error
            if message_id in self.inbox or message_id in self.leads:
                stored = {"@odata.type": "#microsoft.graph.openTypeExtension",
                          "id": "Microsoft.OutlookServices.OpenTypeExtension." + body["extensionName"],
                          **{k: v for k, v in body.items() if k != "@odata.type"}}
                self.extensions[message_id] = stored
                return FakeResponse(json.dumps(stored).encode())

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
                if message_id in self.extensions:        # saved data travels with the email
                    self.extensions[moved["id"]] = self.extensions.pop(message_id)
                for key in [k for k in self.markers if k[0] == message_id]:
                    self.markers[("moved-" + message_id, key[1])] = self.markers.pop(key)
                return FakeResponse(json.dumps({"id": moved["id"]}).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def fail_draft(self, step):
        error = self.draft_errors.get(step)
        if error is not None:
            raise error

    def draft_marked(self, message_id):
        """True if the email (by its Inbox id or its Leads id) carries the draft-made marker."""
        return ((message_id, DRAFT_MARKER) in self.markers) or (("moved-" + message_id, DRAFT_MARKER) in self.markers)

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]

    def categories(self, message_id):
        return (self.inbox.get(message_id) or self.leads.get(message_id))["categories"]

    def slack_marked(self, message_id):
        """True if the email (by its Inbox id or its Leads id) carries the Slack marker."""
        return ((message_id, SLACK_MARKER) in self.markers) or (("moved-" + message_id, SLACK_MARKER) in self.markers)

    def saved_answer(self, message_id):
        """The answer saved on an email (by its Inbox id or its Leads id), or None."""
        saved = self.extensions.get(message_id) or self.extensions.get("moved-" + message_id)
        return json.loads(saved["data"]) if saved else None

    def where(self, message_id):
        if message_id in self.inbox:
            return "inbox"
        if "moved-" + message_id in self.leads or message_id in self.leads:
            return "leads"
        return "gone"


class PretendSlack:
    """A pretend Slack webhook. Records every post (link used, text). errors: {n: error} for the n-th post."""

    def __init__(self, errors=None):
        self.errors = errors or {}
        self.attempts = 0
        self.posts = []          # (the link posted to, the text)

    def urlopen(self, request, timeout=None):
        self.attempts += 1
        body = json.loads(request.data.decode())
        if request.get_method() != "POST" or set(body) != {"text"} or not isinstance(body["text"], str):
            raise http_error(400)
        error = self.errors.get(self.attempts)
        if error is not None:
            raise error
        self.posts.append((request.full_url, body["text"]))
        return FakeResponse(b"ok")

    def texts(self):
        return [text for _, text in self.posts]


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
        (self.prompts_dir / "v3.md").write_text(
            'PROMPT-V3: read the email, answer in JSON with "category" and "draft_reply".', encoding="utf-8")
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

    def run_main(self, outlook, gemini, argv=(), remove=(), token_error=None, slack=None, env_changes=None):
        """Run main() against the pretend Outlook, Gemini and Slack. Returns (code, output).

        self.events lists, in order, every change the run made: save, slack:<which link>, slack_marker,
        patch, move. A pretend Slack that nobody asked for fails the test if it is posted to."""
        slack = slack if slack is not None else PretendSlack()
        self.slack = slack
        self.events = []

        def dispatch(request, timeout=None):
            if request.full_url.startswith("https://hooks.slack.com/"):
                which = "test" if TEST_SLACK_SECRET in request.full_url else "live"
                self.events.append(f"slack:{which}")
                return slack.urlopen(request, timeout)
            if request.get_method() != "GET":
                path = urllib.parse.urlparse(request.full_url).path
                if path.endswith("/extensions"):
                    named = json.loads(request.data.decode()).get("extensionName") if request.data else None
                    self.events.append({EXTENSION_NAME: "save", DRAFT_MARKER: "draft_marker"}.get(named, "slack_marker"))
                elif path.endswith("/createReply"):
                    self.events.append("draft")
                elif path.endswith("/move"):
                    self.events.append("move")
                else:
                    self.events.append(request.get_method().lower())
            return outlook.urlopen(request, timeout)

        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        if token_error is not None:
            token_patch = mock.patch.object(outlook_triage, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(outlook_triage, "get_access_token", return_value=FAKE_TOKEN)
        env_before = self.env_path.read_bytes()
        env = {**FAKE_ENV, **SLACK_ENV, **(env_changes or {})}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["outlook_triage.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", dispatch), \
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

    def test_case22_only_category_updates_saved_answers_and_moves_to_leads_are_ever_sent(self):
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
                # a lead's move, or saving its answer on it (one extension of the fixed name)
                self.assertTrue(url.split("?")[0].endswith(("/move", "/extensions")))
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

    # ---- Cases 31 to 39: system and security emails are never sent to Gemini ---

    SYSTEM_WORDS = ["noreply", "no-reply", "do-not-reply", "donotreply", "accountprotection", "security"]
    SYSTEM_DOMAINS = ["microsoft.com", "microsoftonline.com", "anthropic.com", "slack.com", "github.com", "google.com"]
    SYSTEM_PHRASES = [
        "verification code", "confirmation code", "one-time", "secure link", "sign in",
        "sign-in", "password", "security alert", "new app connected",
    ]

    def assert_skipped_untouched(self, message):
        """Run live on one email that must be skipped, and check nothing happened to it."""
        self.state_path.unlink(missing_ok=True)
        outlook = PretendOutlook([message])
        gemini = PretendGemini()
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(gemini.calls, [], "a system email was sent to Gemini")
        self.assertEqual(outlook.changes(), [])
        # It gets no record among the handled emails (the state file may note that it was reported).
        if self.state_path.exists():
            self.assertNotIn(message["id"], self.state()["emails"])
        self.assertEqual(outlook.where(message["id"]), "inbox")
        self.assertEqual(outlook.categories(message["id"]), message["categories"])
        self.assertIn(f"Skipped (system or security email): {message['subject']}", output)
        self.assertIn("System or security emails skipped: 1", output)
        return output

    def assert_handled_as_a_normal_lead(self, message, marker):
        """Run live on one email that must NOT be skipped: it goes to Gemini and becomes a lead."""
        self.state_path.unlink(missing_ok=True)
        outlook = PretendOutlook([message])
        gemini = PretendGemini({marker: lead_reply("pricing")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 1, "the email should have been sent to Gemini")
        self.assertEqual(outlook.categories("moved-" + message["id"]), ["Lead - Pricing"])
        self.assertEqual(outlook.where(message["id"]), "leads")
        self.assertIn("System or security emails skipped: 0", output)
        self.assertNotIn("Skipped (system or security email)", output)

    def test_case31_system_words_before_the_at_sign_are_skipped(self):
        for word in self.SYSTEM_WORDS:
            for sender in (f"{word.upper()}@example.com", f"info-{word}-team@example.com"):
                with self.subTest(sender=sender):
                    self.assert_skipped_untouched(msg("m1", "Hello from us", sender=sender))

    def test_case31_do_not_reply_at_something_dot_com_is_skipped(self):
        self.assert_skipped_untouched(msg("m1", "Hello from us", sender="do-not-reply@something.com"))

    def test_case32_system_domains_and_their_subdomains_are_skipped(self):
        for domain in self.SYSTEM_DOMAINS:
            for host in (domain, "account." + domain, domain.upper()):
                with self.subTest(host=host):
                    self.assert_skipped_untouched(msg("m1", "Hello from us", sender=f"Info@{host}"))

    def test_case32_look_alike_and_ordinary_domains_are_not_skipped(self):
        for host in ("notmicrosoft.com", "notgoogle.com", "mygithub.com", "google.com.example.net",
                     "microsoft.com.evil.example", "gmail.com", "example.com"):
            with self.subTest(host=host):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", "Hello from us", sender=f"info@{host}")])
                gemini = PretendGemini({"Hello from us": NOT_LEAD})
                code, output = self.live(outlook, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(len(gemini.calls), 1)
                self.assertEqual(outlook.categories("m1"), ["Not a lead"])
                self.assertIn("System or security emails skipped: 0", output)

    def test_case33_security_subjects_are_skipped_in_any_capitals(self):
        for phrase in self.SYSTEM_PHRASES:
            for subject in (f"Your {phrase} is ready", f"YOUR {phrase.upper()} IS READY"):
                with self.subTest(subject=subject):
                    self.assert_skipped_untouched(msg("m1", subject))

    def test_case40_whole_phrases_are_skipped_even_with_punctuation(self):
        for subject in ("Sign in to your account", "SIGN IN NOW", "Reset your password.",
                        "Verification code: 123456", "(Your one-time code)", "A security alert for you",
                        "password", "Please sign-in again"):
            with self.subTest(subject=subject):
                self.assert_skipped_untouched(msg("m1", subject))

    def test_case40_a_phrase_inside_a_longer_word_is_not_skipped(self):
        for subject in ("Design information", "Resign in writing"):
            with self.subTest(subject=subject):
                self.assert_handled_as_a_normal_lead(msg("m1", subject), subject)

    def test_case41_the_display_name_is_not_checked(self):
        message = msg("m1", "Pricing question", sender="hello@acme.example.com", name="Acme Security Ltd")
        self.assert_handled_as_a_normal_lead(message, "Pricing question")
        for name in ("No Reply Team", "Do Not Reply", "Account Protection"):
            with self.subTest(name=name):
                message = msg("m1", "Pricing question", sender="hello@acme.example.com", name=name)
                self.assert_handled_as_a_normal_lead(message, "Pricing question")

    def test_case42_sender_words_after_the_at_sign_do_not_count(self):
        for sender in ("info@security-experts.example.com", "sam@no-reply-mail.example.com",
                       "sam@donotreply.example.com"):
            with self.subTest(sender=sender):
                self.assert_handled_as_a_normal_lead(msg("m1", "Pricing question", sender=sender), "Pricing question")

    def test_case34_a_skipped_email_keeps_its_own_categories_and_is_skipped_again_later(self):
        message = msg("m1", "Your password", categories=["Blue"], sender="noreply@example.com")
        self.assert_skipped_untouched(message)
        self.assert_skipped_untouched(message)

    def test_case35_each_skipped_email_is_reported_and_counted(self):
        outlook = PretendOutlook([
            msg("s1", "Your verification code", received="2026-10-20T07:00:00Z"),
            msg("s2", "Hello", sender="noreply@example.com", received="2026-10-20T08:00:00Z"),
            msg("n1", "Weekly newsletter", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your verification code", output)
        self.assertIn("Skipped (system or security email): Hello", output)
        self.assertIn("System or security emails skipped: 2", output)
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(outlook.categories("s1"), [])
        self.assertEqual(outlook.categories("s2"), [])
        self.assertEqual(sorted(self.state()["emails"]), ["n1"])

    def test_case35_a_dry_run_reports_skipped_emails_and_does_not_list_them_as_to_send(self):
        outlook = PretendOutlook([
            msg("s1", "Your verification code", received="2026-10-20T07:00:00Z"),
            msg("s2", "Hello", sender="noreply@example.com", received="2026-10-20T08:00:00Z"),
            msg("n1", "Weekly newsletter", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini()
        code, output = self.run_main(outlook, gemini, [])
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your verification code", output)
        self.assertIn("Skipped (system or security email): Hello", output)
        self.assertIn("System or security emails skipped: 2", output)
        self.assertIn("would send to Gemini: sam@example.com, Weekly newsletter", output)
        self.assertNotIn("would send to Gemini: sam@example.com, Your verification code", output)
        self.assertEqual(outlook.changes(), [])

    def test_case36_the_body_is_not_used_for_this_rule(self):
        outlook = PretendOutlook([msg("m1", "Quick hello", body="your verification code and password are in the security alert")])
        gemini = PretendGemini({"Quick hello": NOT_LEAD})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 1)
        self.assertIn("System or security emails skipped: 0", output)

    def test_case37_triage_test_emails_are_exempt(self):
        cases = [
            ("noreply@example.com", "TRIAGE-TEST hello"),
            ("alerts@microsoft.com", "triage-test Security alert"),
            ("sam@example.com", "TRIAGE-TEST password reset"),
        ]
        for sender, subject in cases:
            with self.subTest(sender=sender, subject=subject):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", subject, sender=sender)])
                gemini = PretendGemini({subject: NOT_LEAD})
                code, output = self.live(outlook, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(len(gemini.calls), 1)
                self.assertEqual(outlook.categories("m1"), ["Not a lead"])
                self.assertIn("System or security emails skipped: 0", output)

    def test_case38_skipped_emails_do_not_use_up_the_maximum(self):
        skipped = [msg(f"s{i}", f"Your password {i}", received=f"2026-10-20T0{i}:00:00Z") for i in range(3)]
        normal = [msg(f"n{i}", f"Plain thing {i}", received=f"2026-10-20T1{i}:00:00Z") for i in range(2)]
        outlook = PretendOutlook(skipped + normal)
        gemini = PretendGemini({f"Plain thing {i}": NOT_LEAD for i in range(2)})
        with mock.patch.object(outlook_triage, "MAX_PER_RUN", 2):
            code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 2)
        self.assertNotIn("still waiting", output)
        self.assertIn("System or security emails skipped: 3", output)

    def test_case38_only_subject_still_applies_first(self):
        outlook = PretendOutlook([
            msg("a", "Your invoice", sender="noreply@example.com", received="2026-10-20T07:00:00Z"),
            msg("b", "Security alert", sender="noreply@example.com", received="2026-10-20T08:00:00Z"),
        ])
        gemini = PretendGemini()
        code, output = self.live(outlook, gemini, "--only-subject", "invoice")
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your invoice", output)
        self.assertNotIn("Security alert", output)
        self.assertIn("System or security emails skipped: 1", output)
        self.assertEqual(gemini.calls, [])

    def test_case39_emails_handled_earlier_are_not_announced_again(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini)
        outlook.inbox["m1"]["categories"] = []
        outlook.inbox["m1"]["from"]["emailAddress"]["address"] = "noreply@example.com"
        calls_before = len(gemini.calls)
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), calls_before)
        self.assertNotIn("Skipped (system or security email)", output)
        self.assertIn("System or security emails skipped: 0", output)

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

    # ---- Case 43: the triage records each email's own message ID ----------------

    def test_case43_the_state_record_includes_the_message_id(self):
        outlook = PretendOutlook([msg("m1", "Demo please", internet_id="<abc123@mail.test.example>")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["emails"]["m1"]["internet_message_id"], "<abc123@mail.test.example>")
        self.assertNotIn(BODY_MARKER, self.state_path.read_text(encoding="utf-8"))

    def test_case43_the_message_id_is_asked_for_when_reading_the_inbox(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        self.run_main(outlook, PretendGemini(), [])
        listing = [r for r in outlook.requests if r.full_url.split("?")[0].endswith("/inbox/messages")]
        self.assertTrue(listing)
        self.assertTrue(all("internetMessageId" in r.full_url for r in listing))

    def test_case43_an_email_without_a_message_id_still_works(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertNotIn("internet_message_id", self.state()["emails"]["m1"])

    def test_case43_an_older_state_file_without_message_ids_still_loads(self):
        self.state_path.write_text(json.dumps({"version": 1, "emails": {
            "old": {"outcome": "Not a lead", "stage": "done", "urgency": None, "date": "2026-10-01"}}}),
            encoding="utf-8")
        outlook = PretendOutlook([msg("m1", "Weekly newsletter", internet_id="<n@mail.test.example>")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("old", self.state()["emails"])
        self.assertEqual(self.state()["emails"]["m1"]["internet_message_id"], "<n@mail.test.example>")

    # ---- Cases 44 and 45: a system email is reported once, not on every run ------------------

    def system_inbox(self):
        return PretendOutlook([
            msg("s1", "Your verification code", sender="noreply@example.com", received="2026-10-20T07:00:00Z"),
            msg("n1", "Weekly newsletter", received="2026-10-20T09:00:00Z"),
        ])

    def test_case44_a_system_email_is_reported_only_the_first_time(self):
        outlook = self.system_inbox()
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, first = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your verification code", first)
        self.assertIn("System or security emails skipped: 1", first)
        code, second = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertNotIn("Skipped (system or security email): Your verification code", second)
        self.assertIn("System or security emails skipped: 1 (1 already reported earlier)", second)
        self.assertEqual(outlook.categories("s1"), [])
        self.assertEqual(outlook.where("s1"), "inbox")
        self.assertNotIn("s1", self.state()["emails"])
        self.assertIn("s1", self.state()["system_skipped"])

    def test_case44_a_new_system_email_is_reported_when_it_arrives(self):
        outlook = self.system_inbox()
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini)
        outlook.inbox["s2"] = msg("s2", "Your password was changed", received="2026-10-20T10:00:00Z")
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your password was changed", output)
        self.assertNotIn("Your verification code", output)
        self.assertIn("System or security emails skipped: 2 (1 already reported earlier)", output)

    def test_case44_a_dry_run_hides_reported_emails_and_never_changes_the_state(self):
        outlook = self.system_inbox()
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini)
        before = self.state_path.read_bytes()
        outlook.inbox["s2"] = msg("s2", "Your password was changed", received="2026-10-20T10:00:00Z")
        code, output = self.run_main(outlook, gemini, [])
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your password was changed", output)
        self.assertNotIn("Your verification code", output)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_case44_a_dry_run_on_a_fresh_state_reports_but_creates_no_file(self):
        outlook = self.system_inbox()
        code, output = self.run_main(outlook, PretendGemini(), [])
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your verification code", output)
        self.assertFalse(self.state_path.exists())

    def test_case45_old_memories_are_forgotten_and_recent_ones_kept(self):
        self.state_path.write_text(json.dumps({
            "version": 1, "emails": {},
            "system_skipped": {"ancient": "2026-01-01", "recent": "2026-10-20"},
        }), encoding="utf-8")
        outlook = self.system_inbox()
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        remembered = self.state()["system_skipped"]
        self.assertNotIn("ancient", remembered)
        self.assertIn("recent", remembered)
        self.assertIn("s1", remembered)

    def test_case45_forgetting_old_memories_is_saved_even_when_nothing_else_happens(self):
        self.state_path.write_text(json.dumps({
            "version": 1, "emails": {}, "system_skipped": {"ancient": "2026-01-01"},
        }), encoding="utf-8")
        code, _ = self.live(PretendOutlook([]), PretendGemini())
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["system_skipped"], {})

    def test_case45_a_state_file_without_the_list_gets_one_when_needed(self):
        self.state_path.write_text(json.dumps({"version": 1, "emails": {}}), encoding="utf-8")
        outlook = self.system_inbox()
        code, _ = self.live(outlook, PretendGemini({"Weekly newsletter": NOT_LEAD}))
        self.assertEqual(code, 0)
        self.assertIn("s1", self.state()["system_skipped"])

    def test_prompt_v3_and_the_config_model_are_used(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        self.assertIn("PROMPT-V3", gemini.asked_about()[0])
        self.assertNotIn("PROMPT-V2", gemini.asked_about()[0])
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


    # ==== Step 3: one Gemini call per email, the answer saved on the email ===========
    # (tests/cases/11-single-call-pipeline.md, Part A)

    def change_kinds(self, outlook):
        """The change requests sent, in order, as short labels."""
        kinds = []
        for request in outlook.changes():
            path = urllib.parse.urlparse(request.full_url).path
            if path.endswith("/extensions"):
                kinds.append("save")
            elif path.endswith("/move"):
                kinds.append("move")
            else:
                kinds.append(request.get_method().lower())
        return kinds

    def test_t1_the_default_prompt_is_v3_and_each_new_email_gets_exactly_one_request(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T08:00:00Z"),
            msg("m2", "Weekly newsletter", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"Demo please": lead_reply("demo"), "Weekly newsletter": NOT_LEAD})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 2)
        for call in gemini.calls:
            self.assertIn("PROMPT-V3", call["text"])
            self.assertNotIn("PROMPT-V2", call["text"])
        self.assertEqual(outlook.where("m1"), "leads")

    def test_t1_a_second_run_asks_nothing_about_emails_already_handled(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        self.assertEqual(len(gemini.calls), 1)
        self.live(outlook, gemini)
        self.assertEqual(len(gemini.calls), 1)

    def test_t1_a_move_retry_sends_no_request_to_gemini(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], move_errors={1: http_error(500)})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertEqual(self.state()["emails"]["m1"]["stage"], "categorised")
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 1, "the retry only moves the email")
        self.assertEqual(len(outlook.extension_posts), 1, "the answer is not saved twice")
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertIsNotNone(outlook.saved_answer("m1"))

    def test_t2_a_leads_full_answer_is_saved_on_the_email(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        answer = lead_answer("demo", 3, name="Sam Jones", company="Jones Gym", role="Owner",
                             summary="Wants a demo.")
        gemini = PretendGemini({"Demo please": json.dumps(answer)})
        self.live(outlook, gemini)
        self.assertEqual(len(outlook.extension_posts), 1)
        message_id, body = outlook.extension_posts[0]
        self.assertEqual(message_id, "m1")
        self.assertEqual(set(body), {"@odata.type", "extensionName", "version", "data"})
        self.assertEqual(body["extensionName"], EXTENSION_NAME)
        self.assertEqual(body["version"], 1)
        saved = outlook.saved_answer("m1")
        self.assertEqual(saved, answer)
        self.assertEqual(set(saved), {"is_lead", "category", "name", "email", "company", "role",
                                      "request_type", "urgency", "summary", "draft_reply"})

    def test_t2_only_the_extracted_fields_are_saved_never_the_email_itself(self):
        outlook = PretendOutlook([msg("m1", "A very particular subject", body="Some private words",
                                      name="Display Name Marker")])
        gemini = PretendGemini({"A very particular subject": lead_reply("pricing")})
        self.live(outlook, gemini)
        raw = json.dumps(outlook.extension_posts[0][1])
        for text in (BODY_MARKER, "Some private words", "A very particular subject", "Display Name Marker"):
            self.assertNotIn(text, raw)

    def test_t2_the_draft_is_saved_as_written_without_the_ai_line_or_the_sign_off(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        saved = outlook.saved_answer("m1")
        self.assertEqual(saved["draft_reply"], CLEAN_DRAFT)
        self.assertNotIn("AI draft", saved["draft_reply"])
        self.assertNotIn("Kind regards", saved["draft_reply"])

    def test_t3_a_lead_is_saved_then_categorised_then_moved(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(self.change_kinds(outlook), ["save", "patch", "move"])
        self.assertEqual(self.state()["emails"]["m1"]["stage"], "done")

    def test_t3_a_refused_save_stops_the_run_and_leaves_the_email_untouched(self):
        for code_number in (401, 403):
            with self.subTest(code=code_number):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", "Demo please")], extension_errors={1: http_error(code_number)})
                gemini = PretendGemini({"Demo please": lead_reply("demo")})
                code, output = self.live(outlook, gemini)
                self.assertEqual(code, 1)
                self.assertIn("sign-in may need redoing", output)
                self.assertNotIn(str(code_number), output)
                self.assertEqual(outlook.patches, [])
                self.assertEqual(outlook.moves, [])
                self.assertEqual(outlook.where("m1"), "inbox")
                self.assertEqual(outlook.categories("m1"), [])
                self.assertFalse(self.state_path.exists() and "m1" in self.state()["emails"])

    def test_t3_a_lost_connection_while_saving_is_explained_and_leaves_the_email_untouched(self):
        outlook = PretendOutlook([msg("m1", "Demo please")],
                                 extension_errors={1: urllib.error.URLError("pretend: down")})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Outlook", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))
        self.assertFalse(self.state_path.exists() and "m1" in self.state()["emails"])

    def test_t3_after_a_refused_save_the_next_run_tries_the_email_again(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], extension_errors={1: http_error(500)})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini)
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(outlook.saved_answer("m1")["category"], "Lead - Demo")

    def test_t3_a_failed_category_change_is_retried_with_a_new_request_and_no_second_save(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], patch_errors={1: http_error(403)})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 1)
        self.assertEqual(len(outlook.extension_posts), 1)
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 2, "it asks again, because the answer is kept nowhere but on the email")
        self.assertEqual(len(outlook.extension_posts), 1, "the first saved answer is kept, no second copy")
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(outlook.categories("moved-m1"), ["Lead - Demo"])

    def test_t4_non_leads_get_a_category_only_and_nothing_is_saved(self):
        outlook = PretendOutlook([
            msg("m1", "Weekly newsletter", received="2026-10-20T08:00:00Z"),
            msg("m2", "Odd one", received="2026-10-20T09:00:00Z"),
            msg("m3", "Broken one", received="2026-10-20T10:00:00Z"),
        ])
        gemini = PretendGemini({
            "Weekly newsletter": NOT_LEAD,
            "Odd one": json.dumps({"is_lead": False, "category": "Needs review"}),
            "Broken one": "this is not json",
        })
        code, _ = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.extension_posts, [])
        self.assertEqual(outlook.categories("m1"), ["Not a lead"])
        self.assertEqual(outlook.categories("m2"), ["Needs review"])
        self.assertEqual(outlook.categories("m3"), ["Needs review"])
        for message_id in ("m1", "m2", "m3"):
            self.assertEqual(outlook.where(message_id), "inbox")

    def test_t5_a_category_that_does_not_match_the_request_type_is_needs_review(self):
        for answer in (
            lead_answer("pricing", category="Lead - Demo"),
            {k: v for k, v in lead_answer("demo").items() if k != "category"},
            lead_answer("demo", category="Hot lead"),
            lead_answer("complaint"),
        ):
            with self.subTest(category=answer.get("category"), request_type=answer["request_type"]):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", "Demo please")])
                gemini = PretendGemini({"Demo please": json.dumps(answer)})
                code, _ = self.live(outlook, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(outlook.categories("m1"), ["Needs review"])
                self.assertEqual(outlook.where("m1"), "inbox")
                self.assertEqual(outlook.extension_posts, [])

    def test_t6_a_lead_whose_draft_breaks_a_rule_is_still_saved_and_moved_without_a_draft(self):
        bad = "Hello Sam, the price is $50 per month for your enquiry. A member of our team will be in touch."
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo", draft_reply=bad)})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.where("m1"), "leads")
        saved = outlook.saved_answer("m1")
        self.assertEqual(saved["category"], "Lead - Demo")
        self.assertIsNone(saved["draft_reply"])
        self.assertIn("no usable draft", output)
        self.assertNotIn("$50", output)
        self.assertNotIn("price is", output)

    def test_t7_a_dry_run_sends_nothing_to_gemini_saves_nothing_and_changes_nothing(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.run_main(outlook, gemini, [])
        self.assertEqual(code, 0)
        self.assertEqual(gemini.calls, [])
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(outlook.extension_posts, [])

    def test_t8_no_lead_details_reach_the_output_or_any_local_file(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        answer = lead_answer("demo", name="NAME-MARKER-PERSON", company="COMPANY-MARKER-CO",
                             role="ROLE-MARKER-JOB", summary="SUMMARY-MARKER-TEXT",
                             draft_reply="Hello there, thank you for your enquiry. A member of our team will be in touch. DRAFT-MARKER-TEXT")
        gemini = PretendGemini({"Demo please": json.dumps(answer)})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        everything = output + "\n" + self.saved_text()
        for marker in ("NAME-MARKER-PERSON", "COMPANY-MARKER-CO", "ROLE-MARKER-JOB",
                       "SUMMARY-MARKER-TEXT", "DRAFT-MARKER-TEXT"):
            self.assertNotIn(marker, everything)
        # the answer really was saved, on the email in Outlook
        self.assertIn("SUMMARY-MARKER-TEXT", json.dumps(outlook.extension_posts))

    def test_t8_a_rule_breaking_draft_text_never_reaches_the_output(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo", draft_reply="DRAFT-MARKER-TEXT costs $9 today")})
        code, output = self.live(outlook, gemini)
        self.assertEqual(code, 0)
        self.assertNotIn("DRAFT-MARKER-TEXT", output + self.saved_text())

    def test_t9_only_safe_requests_are_ever_sent(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T08:00:00Z"),
            msg("m2", "Weekly newsletter", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"Demo please": lead_reply("demo"), "Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini)
        for request in outlook.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/send", "/reply", "/forward", "/copy", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, path)
            self.assertNotIn(request.get_method(), ("DELETE", "PUT"))
        self.assertEqual(self.change_kinds(outlook), ["save", "patch", "move", "patch"])
        for request in outlook.changes():
            if request.get_method() == "POST" and request.full_url.endswith("/extensions"):
                body = json.loads(request.data.decode())
                self.assertEqual(body["extensionName"], EXTENSION_NAME)

    def test_t9_the_permission_is_still_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])


    # ==== The Slack step (tests/cases/13-slack-step.md, Part B) ===========================

    def two_leads(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T08:00:00Z", sender="one@example.com"),
            msg("m2", "Prices please", received="2026-10-20T09:00:00Z", sender="two@example.com"),
        ])
        gemini = PretendGemini({"Demo please": lead_reply("demo"), "Prices please": lead_reply("pricing")})
        return outlook, gemini

    def test_s7_slack_is_off_by_default_and_with_off(self):
        for extra in ((), ("--slack", "off")):
            with self.subTest(extra=extra):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", "Demo please")])
                gemini = PretendGemini({"Demo please": lead_reply("demo")})
                code, output = self.live(outlook, gemini, *extra)
                self.assertEqual(code, 0)
                self.assertEqual(self.slack.attempts, 0)
                self.assertEqual(outlook.marker_posts, [])
                self.assertNotIn("Slack", output)
                self.assertEqual(self.events, ["save", "patch", "move"])

    def test_s8_test_posts_only_to_the_test_link_and_live_only_to_the_live_link(self):
        for mode, link in (("test", TEST_SLACK_URL), ("live", LIVE_SLACK_URL)):
            with self.subTest(mode=mode):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([msg("m1", "Demo please")])
                gemini = PretendGemini({"Demo please": lead_reply("demo")})
                code, _ = self.live(outlook, gemini, "--slack", mode)
                self.assertEqual(code, 0)
                self.assertEqual([url for url, _ in self.slack.posts], [link])

    def test_s8_a_missing_setting_stops_before_anything_is_read_or_changed(self):
        for mode, name in (("test", "SLACK_TEST_WEBHOOK_URL"), ("live", "SLACK_WEBHOOK_URL")):
            for argv in (["--live", "--slack", mode], ["--slack", mode]):
                with self.subTest(mode=mode, live="--live" in argv):
                    outlook = PretendOutlook([msg("m1", "Demo please")])
                    gemini = PretendGemini({"Demo please": lead_reply("demo")})
                    code, output = self.run_main(outlook, gemini, argv, remove=[name])
                    self.assertEqual(code, 1)
                    self.assertIn(name, output)
                    self.assertIn("Nothing was changed", output)
                    self.assertEqual(outlook.requests, [])
                    self.assertEqual(gemini.calls, [])
                    self.assertEqual(self.slack.attempts, 0)

    def test_s8_the_other_mode_setting_is_not_needed(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, _ = self.live(outlook, gemini, "--slack", "test", remove=["SLACK_WEBHOOK_URL"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.slack.posts), 1)

    def test_s8_a_value_other_than_off_test_or_live_is_refused(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        errors = io.StringIO()
        with mock.patch.object(sys, "stderr", errors):
            with self.assertRaises(SystemExit):
                self.live(outlook, gemini, "--slack", "banana")
        self.assertIn("invalid choice", errors.getvalue())
        self.assertIn("off", errors.getvalue())
        self.assertEqual(outlook.requests, [])

    def test_s9_only_leads_are_posted_each_exactly_once(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T07:00:00Z"),
            msg("m2", "Weekly newsletter", received="2026-10-20T08:00:00Z"),
            msg("m3", "Odd one", received="2026-10-20T09:00:00Z"),
            msg("m4", "Mismatch", received="2026-10-20T10:00:00Z"),
            msg("m5", "Prices please", received="2026-10-20T11:00:00Z"),
        ])
        gemini = PretendGemini({
            "Demo please": lead_reply("demo"),
            "Weekly newsletter": NOT_LEAD,
            "Odd one": json.dumps({"is_lead": False, "category": "Needs review"}),
            "Mismatch": json.dumps(lead_answer("pricing", category="Lead - Demo")),
            "Prices please": lead_reply("pricing"),
        })
        code, _ = self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(code, 0)
        self.assertEqual(self.slack.texts(), ["Lead - Demo: sam@example.com, Demo please",
                                              "Lead - Pricing: sam@example.com, Prices please"])
        self.assertEqual(len(outlook.marker_posts), 2)
        for message_id in ("m2", "m3", "m4"):
            self.assertFalse(outlook.slack_marked(message_id))

    def test_s9_a_second_run_posts_nothing_more(self):
        outlook, gemini = self.two_leads()
        self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(len(self.slack.posts), 2)
        self.live(outlook, gemini, "--slack", "test", slack=self.slack)
        self.assertEqual(len(self.slack.posts), 2)

    def test_s9_a_move_retry_does_not_post_again(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], move_errors={1: http_error(500)})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, _ = self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.slack.posts), 1)
        code, _ = self.live(outlook, gemini, "--slack", "test", slack=self.slack)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.slack.posts), 1)
        self.assertEqual(outlook.where("m1"), "leads")

    def test_s9_a_retry_after_a_failed_category_change_does_not_post_again(self):
        outlook = PretendOutlook([msg("m1", "Demo please")], patch_errors={1: http_error(403)})
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, _ = self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.slack.posts), 1)
        code, output = self.live(outlook, gemini, "--slack", "test", slack=self.slack)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 2, "it asks again, as before")
        self.assertEqual(len(self.slack.posts), 1, "but the marker stops a second post")
        self.assertIn("already posted to Slack earlier", output)

    def test_s10_the_order_is_save_then_slack_then_category_then_move(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(self.events, ["save", "slack:test", "slack_marker", "patch", "move"])
        self.assertTrue(outlook.slack_marked("m1"))
        self.assertIn(("moved-m1", SLACK_MARKER), outlook.markers, "the marker travels with the email")

    def test_s10_a_non_lead_has_no_slack_in_its_changes(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(self.events, ["patch"])

    def test_s11_if_slack_is_down_the_run_stops_and_the_email_is_left_untouched(self):
        outlook, gemini = self.two_leads()
        slack = PretendSlack(errors={1: http_error(500)})
        code, output = self.live(outlook, gemini, "--slack", "test", slack=slack)
        self.assertEqual(code, 1)
        self.assertIn("Slack refused the message (code 500)", output)
        self.assertEqual(outlook.patches, [])
        self.assertEqual(outlook.moves, [])
        self.assertEqual(outlook.where("m1"), "inbox")
        self.assertEqual(outlook.categories("m1"), [])
        self.assertFalse(self.state_path.exists() and "m1" in self.state()["emails"])
        self.assertIsNotNone(outlook.saved_answer("m1"), "the saved answer stays")
        self.assertEqual(slack.attempts, 1, "nothing is posted for the later email")
        self.assertEqual(outlook.where("m2"), "inbox")
        self.assertFalse(outlook.slack_marked("m1"))
        for secret in (TEST_SLACK_SECRET, "hooks.slack.com"):
            self.assertNotIn(secret, output)

    def test_s11_the_next_run_with_slack_working_finishes_the_lead_with_one_post(self):
        outlook, gemini = self.two_leads()
        self.live(outlook, gemini, "--slack", "test", slack=PretendSlack(errors={1: http_error(500)}))
        code, _ = self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(code, 0)
        self.assertEqual(self.slack.texts(), ["Lead - Demo: one@example.com, Demo please",
                                              "Lead - Pricing: two@example.com, Prices please"])
        self.assertEqual(len(outlook.extension_posts), 2, "no second saved answer for the first lead")
        self.assertEqual((outlook.where("m1"), outlook.where("m2")), ("leads", "leads"))

    def test_s11_slack_unreachable_is_explained_and_stops_the_run(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        slack = PretendSlack(errors={1: urllib.error.URLError("pretend: down")})
        code, output = self.live(outlook, gemini, "--slack", "test", slack=slack)
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Slack", output)
        self.assertNotIn("URLError", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))

    def test_s11_a_marker_that_could_not_be_saved_stops_with_the_plain_warning(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        outlook.marker_errors = {1: http_error(500)}
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(code, 1)
        self.assertIn("posted to Slack", output)
        self.assertIn("twice", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))

    def test_s12_the_output_says_what_happened_without_the_link_or_the_text(self):
        outlook, gemini = self.two_leads()
        code, output = self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(code, 0)
        self.assertEqual(output.count("posted to Slack (test)"), 2)
        self.assertIn("Posted to Slack (test): 2", output)
        for text in self.slack.texts():
            self.assertNotIn(text, output)
        for secret in (TEST_SLACK_SECRET, LIVE_SLACK_SECRET, "hooks.slack.com"):
            self.assertNotIn(secret, output)
            self.assertNotIn(secret, self.saved_text())
        self.assertNotIn("Slack", self.saved_text())

    def test_s12_the_live_mode_is_named_in_the_output(self):
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        code, output = self.live(outlook, gemini, "--slack", "live")
        self.assertEqual(code, 0)
        self.assertIn("posted to Slack (live)", output)
        self.assertIn("Posted to Slack (live): 1", output)

    def test_s12_only_the_category_sender_and_subject_are_in_the_post(self):
        outlook = PretendOutlook([msg("m1", "Demo please", body="PRIVATE-BODY-WORDS", name="Display Name Marker")])
        answer = lead_answer("demo", name="NAME-MARKER-PERSON", company="COMPANY-MARKER-CO", role="ROLE-MARKER-JOB",
                             summary="SUMMARY-MARKER-TEXT")
        gemini = PretendGemini({"Demo please": json.dumps(answer)})
        self.live(outlook, gemini, "--slack", "test")
        (text,) = self.slack.texts()
        self.assertEqual(text, "Lead - Demo: sam@example.com, Demo please")
        for marker in ("NAME-MARKER", "COMPANY-MARKER", "ROLE-MARKER", "SUMMARY-MARKER", "DRAFT", "PRIVATE-BODY",
                       BODY_MARKER, "Display Name Marker", "A member of our team"):
            self.assertNotIn(marker, text)

    def test_s13_a_dry_run_posts_nothing_marks_nothing_and_changes_nothing(self):
        for mode in ("test", "live"):
            with self.subTest(mode=mode):
                outlook, gemini = self.two_leads()
                code, output = self.run_main(outlook, gemini, ["--slack", mode])
                self.assertEqual(code, 0)
                self.assertEqual(self.slack.attempts, 0)
                self.assertEqual(outlook.changes(), [])
                self.assertEqual(gemini.calls, [])
                self.assertIn(f"Slack posting: {mode}", output)
                self.assertIn("dry run", output.lower())
                self.assertNotIn(TEST_SLACK_SECRET, output)
                self.assertNotIn(LIVE_SLACK_SECRET, output)

    def test_s14_it_waits_between_two_posts_but_not_before_the_first(self):
        outlook, gemini = self.two_leads()
        self.live(outlook, gemini, "--slack", "test")
        pause = outlook_triage.lead_slack.PAUSE_SECONDS
        self.assertEqual(self.sleeps.count(pause), 1)
        self.sleeps.clear()
        outlook = PretendOutlook([msg("m1", "Demo please")])
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        self.state_path.unlink(missing_ok=True)
        self.live(outlook, gemini, "--slack", "test")
        self.assertEqual(self.sleeps.count(pause), 0)

    def test_the_slack_step_keeps_the_triage_send_proof(self):
        outlook, gemini = self.two_leads()
        self.live(outlook, gemini, "--slack", "test")
        for request in outlook.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/send", "/reply", "/forward", "/copy", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, path)
            self.assertNotIn(request.get_method(), ("DELETE", "PUT"))
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])

    # ==== The reply draft step (tests/cases/12-reply-drafts.md, Part B) ==========================

    DRAFTS = ("--drafts", "on")
    EXPECTED_HTML = (
        "<p>AI draft - please review before sending</p>\n"
        "<p>Hello Sam,</p>\n"
        "<p>Thank you for getting in touch about your enquiry. A member of our team will be in touch.</p>\n"
        "<p>Kind regards,<br>Nova Wellness Team</p>"
    )

    def two_draft_leads(self):
        """Two leads whose saved address is the sender's own, so each should get a draft."""
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T08:00:00Z", sender="one@example.com"),
            msg("m2", "Prices please", received="2026-10-20T09:00:00Z", sender="two@example.com"),
        ])
        gemini = PretendGemini({
            "Demo please": lead_reply("demo", email="one@example.com"),
            "Prices please": lead_reply("pricing", email="two@example.com"),
        })
        return outlook, gemini

    def one_draft_lead(self, **outlook_options):
        outlook = PretendOutlook([msg("m1", "Demo please")], **outlook_options)
        gemini = PretendGemini({"Demo please": lead_reply("demo")})
        return outlook, gemini

    def test_d1_drafts_are_off_by_default_and_with_off(self):
        for extra in ((), ("--drafts", "off")):
            with self.subTest(extra=extra):
                self.state_path.unlink(missing_ok=True)
                outlook, gemini = self.one_draft_lead()
                code, output = self.live(outlook, gemini, *extra)
                self.assertEqual(code, 0)
                self.assertEqual(self.events, ["save", "patch", "move"])
                self.assertEqual(outlook.drafts, {})
                self.assertNotIn("eply draft", output)
                for request in outlook.requests:
                    self.assertNotIn("drafts", request.full_url.lower())
                    self.assertNotIn("createreply", request.full_url.lower())

    def test_d1_a_value_other_than_off_or_on_is_refused_before_any_request(self):
        outlook, gemini = self.one_draft_lead()
        errors = io.StringIO()
        with mock.patch.object(sys, "stderr", errors):
            with self.assertRaises(SystemExit):
                self.live(outlook, gemini, "--drafts", "banana")
        self.assertIn("invalid choice", errors.getvalue())
        self.assertEqual(outlook.requests, [])

    def test_d2_the_order_is_save_slack_draft_category_move(self):
        outlook, gemini = self.one_draft_lead()
        code, _ = self.live(outlook, gemini, "--slack", "test", *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(self.events, ["save", "slack:test", "slack_marker", "draft", "draft_marker", "patch", "move"])
        self.assertTrue(outlook.draft_marked("m1"))
        self.assertIn(("moved-m1", DRAFT_MARKER), outlook.markers, "the marker travels with the email")

    def test_d2_without_slack_the_order_is_save_draft_category_move(self):
        outlook, gemini = self.one_draft_lead()
        self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(self.events, ["save", "draft", "draft_marker", "patch", "move"])

    def test_d2_a_lead_still_goes_to_leads_and_not_to_processed(self):
        outlook, gemini = self.one_draft_lead()
        self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(outlook.moves, [("m1", {"destinationId": "leads-id"})])
        self.assertEqual(outlook.where("m1"), "leads")

    def test_d3_one_draft_is_made_with_the_finished_text_to_the_sender_only(self):
        outlook, gemini = self.one_draft_lead()
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.draft_comments, [self.EXPECTED_HTML])
        (draft,) = outlook.drafts.values()
        self.assertEqual([r["emailAddress"]["address"] for r in draft["toRecipients"]], ["sam@example.com"])
        self.assertEqual((draft["ccRecipients"], draft["bccRecipients"]), ([], []))
        self.assertIn("  reply draft made in Drafts", output)
        self.assertIn("Reply drafts made: 1", output)

    def test_d3_no_draft_text_or_name_reaches_the_output_or_a_file(self):
        outlook, gemini = self.one_draft_lead()
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        for private in (CLEAN_DRAFT, "Hello Sam", "Thank you for getting in touch", "AI draft", "Kind regards", "<p>"):
            self.assertNotIn(private, output)
            self.assertNotIn(private, self.saved_text())

    def test_d4_only_leads_get_a_draft(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T07:00:00Z"),
            msg("m2", "Weekly newsletter", received="2026-10-20T08:00:00Z"),
            msg("m3", "Odd one", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({
            "Demo please": lead_reply("demo"),
            "Weekly newsletter": NOT_LEAD,
            "Odd one": json.dumps({"is_lead": False, "category": "Needs review"}),
        })
        code, _ = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.drafts), 1)
        self.assertTrue(outlook.draft_marked("m1"))
        for message_id in ("m2", "m3"):
            self.assertFalse(outlook.draft_marked(message_id))

    def test_d4_a_non_lead_has_no_draft_in_its_changes(self):
        outlook = PretendOutlook([msg("m1", "Weekly newsletter")])
        gemini = PretendGemini({"Weekly newsletter": NOT_LEAD})
        self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(self.events, ["patch"])

    def test_d5_a_lead_that_gets_no_draft_still_carries_on_and_says_why(self):
        cases = {
            "forwarded": (msg("m1", "Demo please"), lead_reply("demo", email="boss@example.com"), "sender"),
            "reply-to elsewhere": (msg("m1", "Demo please", reply_to=["boss@example.com"]), lead_reply("demo"), "Reply-To"),
            "no draft": (msg("m1", "Demo please"), lead_reply("demo", draft_reply=None), "no draft"),
        }
        for name, (message, reply, word) in cases.items():
            with self.subTest(case=name):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook([message])
                gemini = PretendGemini({"Demo please": reply})
                code, output = self.live(outlook, gemini, *self.DRAFTS)
                self.assertEqual(code, 0)
                self.assertEqual(self.events, ["save", "patch", "move"])
                self.assertEqual(outlook.drafts, {})
                self.assertEqual(outlook.where("m1"), "leads")
                self.assertIsNotNone(outlook.saved_answer("m1"))
                self.assertIn("no reply draft was made", output)
                self.assertIn(word, output)
                self.assertIn("Reply drafts not made: 1", output)
                self.assertNotIn("boss@example.com", output)
                self.assertNotIn(CLEAN_DRAFT, output)

    def test_d5_the_final_counts_say_how_many_were_made_and_how_many_were_not(self):
        outlook = PretendOutlook([
            msg("m1", "Demo please", received="2026-10-20T08:00:00Z", sender="one@example.com"),
            msg("m2", "Prices please", received="2026-10-20T09:00:00Z", sender="two@example.com"),
        ])
        gemini = PretendGemini({
            "Demo please": lead_reply("demo", email="one@example.com"),
            "Prices please": lead_reply("pricing", email="elsewhere@example.com"),
        })
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertIn("Reply drafts made: 1", output)
        self.assertIn("Reply drafts not made: 1", output)
        self.assertNotIn("elsewhere@example.com", output)

    def test_d6_if_the_draft_fails_the_run_stops_and_the_email_is_left_untouched(self):
        outlook, gemini = self.two_draft_leads()
        outlook.draft_errors = {"create": http_error(500)}
        code, output = self.live(outlook, gemini, "--slack", "test", *self.DRAFTS)
        self.assertEqual(code, 1)
        self.assertIn("refused", output)
        self.assertIn("Stopped", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))
        self.assertEqual((outlook.where("m1"), outlook.where("m2")), ("inbox", "inbox"))
        self.assertEqual(outlook.categories("m1"), [])
        self.assertFalse(self.state_path.exists() and "m1" in self.state()["emails"])
        self.assertIsNotNone(outlook.saved_answer("m1"), "the saved answer stays")
        self.assertEqual(len(gemini.calls), 1, "nothing is asked or changed for the later email")
        self.assertFalse(outlook.draft_marked("m1"))
        self.assertEqual(len(self.slack.posts), 1, "the lead was already announced, once")

    def test_d6_the_next_run_finishes_the_lead_with_one_draft_and_one_post(self):
        outlook, gemini = self.two_draft_leads()
        outlook.draft_errors = {"create": http_error(500)}
        self.live(outlook, gemini, "--slack", "test", *self.DRAFTS)
        outlook.draft_errors = {}
        code, output = self.live(outlook, gemini, "--slack", "test", *self.DRAFTS, slack=self.slack)
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.drafts), 2, "one draft for each lead, no more")
        self.assertEqual(len(self.slack.posts), 2, "one post for each lead, no more")
        self.assertIn("already posted to Slack earlier", output)
        self.assertEqual((outlook.where("m1"), outlook.where("m2")), ("leads", "leads"))
        self.assertTrue(outlook.draft_marked("m1") and outlook.draft_marked("m2"))

    def test_d6_a_refused_sign_in_is_explained_and_the_email_is_left_alone(self):
        outlook, gemini = self.one_draft_lead()
        outlook.draft_errors = {"create": http_error(403)}
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 1)
        self.assertIn("sign-in may need redoing", output)
        self.assertIn("outlook_signin.py", output)
        self.assertNotIn("403", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))
        self.assertEqual(outlook.drafts, {})

    def test_d6_a_lost_connection_while_making_the_draft_is_explained(self):
        outlook, gemini = self.one_draft_lead()
        outlook.draft_errors = {"create": urllib.error.URLError("pretend: down")}
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach Outlook", output)
        self.assertNotIn("URLError", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))

    def test_d7_a_draft_that_comes_back_wrong_stops_the_run_and_never_makes_a_second_one(self):
        outlook, gemini = self.one_draft_lead(create_to="other@example.com")
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 1)
        self.assertIn("confirm step failed", output)
        self.assertIn("may be in your Drafts folder", output)
        self.assertIn("by hand", output)
        self.assertNotIn("other@example.com", output)
        self.assertEqual((outlook.patches, outlook.moves), ([], []))
        self.assertFalse(outlook.draft_marked("m1"))
        self.assertIsNotNone(outlook.saved_answer("m1"))
        # next run: the draft that is already in Drafts for this conversation counts, so no second one is made
        outlook.create_to = None
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.drafts), 1)
        self.assertEqual(len(outlook.draft_comments), 1, "exactly one create request in total")
        self.assertIn("already made earlier", output)
        self.assertTrue(outlook.draft_marked("m1"))
        self.assertEqual(outlook.where("m1"), "leads")

    def test_d8_a_retry_after_a_failed_category_change_does_not_make_a_second_draft(self):
        outlook, gemini = self.one_draft_lead(patch_errors={1: http_error(403)})
        code, _ = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 1)
        self.assertEqual(len(outlook.drafts), 1)
        self.assertTrue(outlook.draft_marked("m1"))
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.drafts), 1)
        self.assertEqual(len(outlook.draft_comments), 1)
        self.assertIn("already made earlier", output)
        self.assertEqual(outlook.where("m1"), "leads")

    def test_d8_a_move_retry_does_not_touch_drafts_at_all(self):
        outlook, gemini = self.one_draft_lead(move_errors={1: http_error(500)})
        code, _ = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 1)
        code, _ = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        creates = [r for r in outlook.requests if r.full_url.endswith("/createReply")]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(outlook.drafts), 1)
        self.assertEqual(outlook.where("m1"), "leads")

    def test_d8_a_second_run_makes_nothing_more(self):
        outlook, gemini = self.two_draft_leads()
        self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(len(outlook.drafts), 2)
        calls = len(gemini.calls)
        code, _ = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.drafts), 2)
        self.assertEqual(len(outlook.draft_comments), 2)
        self.assertEqual(len(gemini.calls), calls)

    def test_d9_a_draft_already_in_the_conversation_blocks_a_new_one(self):
        outlook, gemini = self.one_draft_lead(existing_drafts=[("mine-1", "conv-m1")])
        code, output = self.live(outlook, gemini, *self.DRAFTS)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.draft_comments, [])
        self.assertTrue(outlook.draft_marked("m1"))
        self.assertIn("already made earlier", output)
        self.assertEqual(outlook.where("m1"), "leads")

    def test_d10_a_dry_run_says_drafts_are_on_and_changes_and_asks_nothing(self):
        outlook, gemini = self.two_draft_leads()
        code, output = self.run_main(outlook, gemini, ["--drafts", "on"])
        self.assertEqual(code, 0)
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(gemini.calls, [])
        self.assertIn("Reply drafts: on", output)
        self.assertIn("dry run", output.lower())
        for request in outlook.requests:
            self.assertNotIn("drafts", request.full_url.lower())
            self.assertNotIn("createreply", request.full_url.lower())

    def test_d11_the_only_changes_the_triage_can_ask_for_are_the_five_allowed_ones(self):
        outlook, gemini = self.two_draft_leads()
        code, _ = self.live(outlook, gemini, "--slack", "test", *self.DRAFTS)
        self.assertEqual(code, 0)
        markers = {EXTENSION_NAME, SLACK_MARKER, DRAFT_MARKER}
        seen = set()
        for request in outlook.changes():
            path = urllib.parse.urlparse(request.full_url).path
            body = json.loads(request.data.decode())
            if request.get_method() == "PATCH":
                self.assertRegex(path, r"^/v1\.0/me/messages/[^/]+$")
                self.assertEqual(set(body), {"categories"})
                seen.add("category")
            elif request.get_method() == "POST" and path.endswith("/extensions"):
                self.assertIn(body["extensionName"], markers)
                seen.add("extension")
            elif request.get_method() == "POST" and path.endswith("/createReply"):
                self.assertEqual(set(body), {"comment"})
                seen.add("draft")
            elif request.get_method() == "POST" and path.endswith("/move"):
                self.assertEqual(body, {"destinationId": "leads-id"})
                seen.add("move")
            else:
                self.fail(f"an unexpected change was asked for: {request.get_method()} {path}")
        self.assertEqual(seen, {"category", "extension", "draft", "move"})
        for request in outlook.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/send", "/reply", "/forward", "/copy", "/permanentdelete", "replyall"):
                self.assertNotIn(word, path)
            self.assertNotIn(request.get_method(), ("DELETE", "PUT"))
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
