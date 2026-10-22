"""Pretend-data checks for workflows/02-email-to-attio/leads_to_attio.py.

These follow tests/cases/05-attio-sync.md (which is not changed).
Everything here is fake and self-contained:
  - a pretend Outlook, a pretend Gemini and a pretend Attio answer in place of the real ones.
    The Attio step never calls Gemini any more: it reads the answer the triage saved on each
    email. So the pretend Gemini only holds the answers that were "saved" on the emails, and
    any request to it fails the test,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened),
  - the state file, config and prompts all live in a throwaway folder.

Run:  python tests\\test_leads_to_attio.py
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

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "01-outlook-to-slack"))
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "02-email-to-attio"))

import extract_leads  # noqa: E402
import leads_to_attio  # noqa: E402
import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402
import outlook_triage  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()
GRAPH = "https://graph.microsoft.com/v1.0"

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_GEMINI_KEY = "FAKE-GEMINI-KEY-SECRET"
FAKE_ATTIO_KEY = "FAKE-ATTIO-KEY-SECRET"
FAKE_OWNER = "owner.fake@example.com"
OWNER_ID = "member-1"
FAKE_ENV = {
    "MS_CLIENT_ID": "fake-client-id",
    "MS_TOKEN_CACHE": FAKE_CACHE,
    "GEMINI_API_KEY": FAKE_GEMINI_KEY,
    "ATTIO_API_KEY": FAKE_ATTIO_KEY,
    "ATTIO_DEAL_OWNER": FAKE_OWNER,
}
SECRETS = (FAKE_TOKEN, FAKE_CACHE, FAKE_GEMINI_KEY, FAKE_ATTIO_KEY, FAKE_OWNER)
BODY_MARKER = "PRIVATE-EMAIL-BODY-MARKER"


# ---- pretend emails and Gemini answers ------------------------------------------

def lead_msg(message_id, subject, sender="nora@brightwell.example", name="Nora Fischer",
             body="Hello there", received="2026-10-20T09:00:00Z", internet_id=None):
    return {
        "id": message_id,
        "subject": subject,
        "from": {"emailAddress": {"address": sender, "name": name}},
        "receivedDateTime": received,
        "internetMessageId": internet_id or f"<{message_id}@test.example>",
        "body": {"contentType": "text", "content": f"{body} {BODY_MARKER}"},
    }


def info(**changes):
    """What Gemini says about a lead. Pass changes to differ from the default."""
    base = {
        "is_lead": True,
        "name": "Nora Fischer",
        "email": "nora@brightwell.example",
        "company": "Brightwell Gyms",
        "role": "Operations Manager",
        "request_type": "demo",
        "urgency": 2,
        "summary": "Wants a demo of the booking software.",
    }
    base.update(changes)
    return json.dumps(base)


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


class FakeHTTP:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.headers = {}

    def json(self):
        return self._body


class Raise:
    def __init__(self, error):
        self.error = error


class Status:
    def __init__(self, code, message="pretend error"):
        self.code = code
        self.message = message


# ---- a pretend Outlook (Leads folder with a Processed sub-folder) -------------------

class PretendOutlook:
    def __init__(self, leads=(), inbox=(), processed=(), has_leads_folder=True,
                 has_processed_folder=True, move_errors=None, unreachable=False, read_error=None):
        self.leads = {m["id"]: copy.deepcopy(m) for m in leads}
        self.inbox = {m["id"]: copy.deepcopy(m) for m in inbox}
        self.processed = {m["id"]: copy.deepcopy(m) for m in processed}
        self.has_leads_folder = has_leads_folder
        self.has_processed_folder = has_processed_folder
        self.move_errors = move_errors or {}
        self.unreachable = unreachable
        self.read_error = read_error        # raised when a saved answer is read
        self.extensions = {}                # message id -> the extension saved on that email
        self.requests = []
        self.moves = []
        self.unexpected = []

    def add_lead(self, message):
        self.leads[message["id"]] = copy.deepcopy(message)

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        if self.unreachable:
            raise urllib.error.URLError("pretend: no internet")
        method = request.get_method()
        parsed = urllib.parse.urlparse(request.full_url)
        path = parsed.path.replace("/v1.0", "", 1)

        if method == "GET" and path == "/me/mailFolders":
            folders = [{"id": "inbox-id", "displayName": "Inbox"}]
            if self.has_leads_folder:
                folders.append({"id": "leads-id", "displayName": "Leads"})
            return FakeResponse(json.dumps({"value": folders}).encode())
        if method == "GET" and path == "/me/mailFolders/inbox/childFolders":
            return FakeResponse(json.dumps({"value": []}).encode())
        if method == "GET" and path == "/me/mailFolders/leads-id/childFolders" and self.has_leads_folder:
            folders = [{"id": "processed-id", "displayName": "Processed"}] if self.has_processed_folder else []
            return FakeResponse(json.dumps({"value": folders}).encode())
        if method == "GET" and path == "/me/mailFolders/leads-id/messages" and self.has_leads_folder:
            values = [copy.deepcopy(m) for m in self.leads.values()]
            return FakeResponse(json.dumps({"value": values}).encode())

        found = re.fullmatch(r"/me/messages/([^/]+)/extensions/([^/]+)", path)
        if method == "GET" and found:
            # Strict, like the real Outlook: only the documented form (one extension, read by its
            # name, no query string) is answered.
            if parsed.query:
                raise http_error(400)
            if self.read_error is not None:
                raise self.read_error
            saved = self.extensions.get(urllib.parse.unquote(found.group(1)))
            if saved is not None and saved["extensionName"] == urllib.parse.unquote(found.group(2)):
                return FakeResponse(json.dumps(saved).encode())
            raise http_error(404)

        found = re.fullmatch(r"/me/messages/([^/]+)/move", path)
        if method == "POST" and found:
            message_id = urllib.parse.unquote(found.group(1))
            body = json.loads(request.data.decode())
            self.moves.append((message_id, body))
            error = self.move_errors.get(len(self.moves))
            if error is not None:
                raise error
            if body.get("destinationId") == "processed-id" and message_id in self.leads:
                moved = self.leads.pop(message_id)
                moved["id"] = "moved-" + message_id
                self.processed[moved["id"]] = moved
                if message_id in self.extensions:
                    self.extensions[moved["id"]] = self.extensions.pop(message_id)
                return FakeResponse(json.dumps({"id": moved["id"]}).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]

    def where(self, message_id):
        if message_id in self.leads:
            return "leads"
        if "moved-" + message_id in self.processed or message_id in self.processed:
            return "processed"
        return "gone"


# ---- a pretend Gemini --------------------------------------------------------------------

REQUEST_CATEGORY = {"demo": "Lead - Demo", "pricing": "Lead - Pricing",
                    "partnership": "Lead - Other", "other": "Lead - Other"}
EXTENSION_NAME = "com.novawellness.leadresult"


def saved_extension(data_text, version=1):
    """What Outlook holds on an email once an answer was saved on it."""
    return {
        "@odata.type": "#microsoft.graph.openTypeExtension",
        "extensionName": EXTENSION_NAME,
        "id": "Microsoft.OutlookServices.OpenTypeExtension." + EXTENSION_NAME,
        "version": version,
        "data": data_text,
    }


def saved_from_reply(reply):
    """The extension the triage would have saved for a pretend answer (a text like info() makes).

    A reply that is not a usable answer (broken text, or not true/false for is_lead) is saved
    as it is, so the Attio step meets a damaged answer.
    """
    if not isinstance(reply, str):
        return None
    try:
        answer = json.loads(reply)
    except ValueError:
        return saved_extension(reply)
    if not isinstance(answer, dict) or not isinstance(answer.get("is_lead"), bool):
        return saved_extension(reply)
    if answer["is_lead"] is False:
        return saved_extension(json.dumps({"is_lead": False, "category": "Not a lead"}))
    request_type = answer.get("request_type")
    saved = {"is_lead": True, "category": REQUEST_CATEGORY.get(request_type, "Needs review")}
    for field in ("name", "email", "company", "role", "request_type", "urgency", "summary"):
        saved[field] = answer.get(field)
    saved["draft_reply"] = None
    return saved_extension(json.dumps(saved))


class PretendGemini:
    """The Attio step never calls Gemini. This holds the answers the triage "saved" on the emails
    (found by a word in the email, like the subject), and fails the test if Gemini is called."""

    def __init__(self, replies=None):
        self.replies = {k: list(v) if isinstance(v, list) else [v] for k, v in (replies or {}).items()}
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "text": ""})
        raise AssertionError("the Attio step must never call Gemini")

    def answer_for(self, message):
        """The pretend saved answer for an email, or None when nothing was saved on it."""
        address = ((message.get("from") or {}).get("emailAddress")) or {}
        text = " ".join([message.get("subject") or "", address.get("address") or "",
                         address.get("name") or "", ((message.get("body") or {}).get("content")) or ""])
        for marker, queue in self.replies.items():
            if marker in text:
                return queue[0]
        return None


# ---- a pretend Attio (people, companies, deals, notes) -------------------------------------
# Shaped after Attio's real REST API (docs.attio.com): values may be written as plain strings
# or as small objects, and a person's record lists its deals under "associated_deals".

def text_of(value, *keys):
    """The text inside an Attio value that may be a string, an object, or a list of either."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        return None
    return value


def leaf(value):
    """The innermost value of a filter like {"x": {"$eq": "y"}} or the plain string "y"."""
    while isinstance(value, dict):
        value = next(iter(value.values()), None)
    return value


class PretendAttio:
    """A small pretend Attio. Records every request and can be made to fail once."""

    def __init__(self, stages=("New", "In progress", "Won", "Lost"), members=None):
        self.people = {}
        self.companies = {}
        self.deals = {}
        self.notes = []
        self.members = {FAKE_OWNER: OWNER_ID} if members is None else dict(members)
        self.stages = list(stages)
        self.failures = []
        self.reject_owner_on_deal = False
        self.requests = []
        self.unexpected = []
        self._counter = 0

    # -- setting up pretend data ------------------------------------------------------
    def next_id(self, prefix):
        self._counter += 1
        return f"{prefix}{self._counter}"

    def add_company(self, name, domain):
        cid = self.next_id("c")
        self.companies[cid] = {"name": name, "domain": domain}
        return cid

    def add_person(self, email, name=None, job_title=None, company=None):
        pid = self.next_id("p")
        self.people[pid] = {"name": name, "email": email.lower(), "job_title": job_title, "company": company}
        return pid

    def add_deal(self, name, stage, person=None, company=None):
        did = self.next_id("d")
        self.deals[did] = {"name": name, "stage": stage, "owner": OWNER_ID,
                           "people": [person] if person else [], "company": company, "order": self._counter}
        return did

    def fail(self, method, path_end, outcome):
        """Make the next matching request fail once."""
        self.failures.append((method, path_end, outcome))

    # -- looking at what is there -----------------------------------------------------
    def person_ids(self, email):
        return [pid for pid, p in self.people.items() if p["email"] == email.lower()]

    def company_ids(self, domain):
        return [cid for cid, c in self.companies.items() if c["domain"] == domain.lower()]

    TWO_PART_ENDINGS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz"}

    @classmethod
    def root_of(cls, domain):
        """The root domain of a domain, the way Attio works it out (mail.acme.co.uk -> acme.co.uk)."""
        labels = domain.lower().split(".")
        if len(labels) >= 3 and ".".join(labels[-2:]) in cls.TWO_PART_ENDINGS:
            return ".".join(labels[-3:])
        return ".".join(labels[-2:]) if len(labels) >= 2 else domain.lower()

    def companies_by_root(self, root):
        """Every company whose domain has this root domain, the exact domain and subdomains alike (in the order added)."""
        return [cid for cid, c in self.companies.items() if self.root_of(c["domain"]) == root.lower()]

    def deals_of(self, person_id):
        return [d for d in self.deals.values() if person_id in d["people"]]

    def notes_of(self, deal_id):
        return [n for n in self.notes if n["parent_record_id"] == deal_id]

    def writes(self):
        """Every request that creates or changes something (queries are reads)."""
        return [r for r in self.requests
                if r["method"] in ("PATCH", "PUT", "DELETE")
                or (r["method"] == "POST" and not r["path"].endswith("/query"))]

    def reads(self):
        return [r for r in self.requests if r not in self.writes()]

    def counts(self):
        return (len(self.people), len(self.companies), len(self.deals), len(self.notes))

    # -- the pretend API ---------------------------------------------------------------
    def request(self, method, url, headers=None, json=None, params=None, timeout=None, **kwargs):
        headers = headers or {}
        path = urllib.parse.urlparse(url).path
        self.requests.append({"method": method, "path": path, "body": copy.deepcopy(json), "params": params})

        if headers.get("Authorization") != f"Bearer {FAKE_ATTIO_KEY}":
            return FakeHTTP(401, {"status_code": 401, "message": "Invalid or expired token"})
        for i, (m, end, outcome) in enumerate(self.failures):
            if m == method and path.endswith(end):
                self.failures.pop(i)
                if isinstance(outcome, Raise):
                    raise outcome.error
                return FakeHTTP(outcome.code, {"message": outcome.message})
        try:
            return self.route(method, path, json or {}, params or {})
        except KeyError:
            self.unexpected.append(f"{method} {path}")
            return FakeHTTP(404, {"message": "not found"})

    def person_record(self, pid):
        p = self.people[pid]
        values = {"email_addresses": [{"email_address": p["email"]}]}
        values["name"] = [{"full_name": p["name"]}] if p["name"] else []
        values["job_title"] = [{"value": p["job_title"]}] if p["job_title"] else []
        values["company"] = ([{"target_object": "companies", "target_record_id": p["company"]}]
                             if p["company"] else [])
        values["associated_deals"] = [{"target_object": "deals", "target_record_id": did}
                                      for did, d in self.deals.items() if pid in d["people"]]
        return {"id": {"record_id": pid}, "values": values}

    def company_record(self, cid):
        c = self.companies[cid]
        return {"id": {"record_id": cid},
                "values": {"name": [{"value": c["name"]}],
                           "domains": [{"domain": c["domain"], "root_domain": self.root_of(c["domain"])}]}}

    def deal_record(self, did):
        d = self.deals[did]
        return {"id": {"record_id": did}, "created_at": f"{d['order']:012d}",
                "values": {
                    "name": [{"value": d["name"]}],
                    "stage": [{"status": {"title": d["stage"]}}],
                    "owner": [{"referenced_actor_type": "workspace-member", "referenced_actor_id": d["owner"]}],
                    "associated_people": [{"target_object": "people", "target_record_id": p} for p in d["people"]],
                    "associated_company": ([{"target_object": "companies", "target_record_id": d["company"]}]
                                           if d["company"] else [])}}

    def route(self, method, path, body, params):
        if method == "GET" and path == "/v2/workspace_members":
            return FakeHTTP(200, {"data": [{"id": {"workspace_member_id": mid}, "email_address": email}
                                           for email, mid in self.members.items()]})
        if method == "GET" and path == "/v2/objects/deals/attributes/stage/statuses":
            return FakeHTTP(200, {"data": [{"id": {"status_id": s}, "title": s} for s in self.stages]})

        if method == "POST" and path == "/v2/objects/people/records/query":
            wanted = leaf(body.get("filter", {}).get("email_addresses"))
            return FakeHTTP(200, {"data": [self.person_record(pid) for pid in self.person_ids(wanted or "")]})
        if method == "POST" and path == "/v2/objects/people/records":
            values = body["data"]["values"]
            email = text_of(values["email_addresses"], "email_address").lower()
            if self.person_ids(email):
                return FakeHTTP(400, {"message": "uniqueness constraint violated for email_addresses"})
            name = text_of(values.get("name"), "full_name")
            title = text_of(values.get("job_title"), "value")
            company = text_of(values.get("company"), "target_record_id")
            pid = self.add_person(email, name, title, company)
            return FakeHTTP(200, {"data": self.person_record(pid)})
        found = re.fullmatch(r"/v2/objects/people/records/([^/]+)", path)
        if method == "PATCH" and found:
            pid = found.group(1)
            person = self.people[pid]
            values = body["data"]["values"]
            if "name" in values:
                person["name"] = text_of(values["name"], "full_name")
            if "job_title" in values:
                person["job_title"] = text_of(values["job_title"], "value")
            if "company" in values:
                person["company"] = text_of(values["company"], "target_record_id")
            return FakeHTTP(200, {"data": self.person_record(pid)})

        if method == "POST" and path == "/v2/objects/companies/records/query":
            wanted = leaf(body.get("filter", {}).get("domains"))
            return FakeHTTP(200, {"data": [self.company_record(cid) for cid in self.companies_by_root(wanted or "")]})
        if method == "POST" and path == "/v2/objects/companies/records":
            values = body["data"]["values"]
            domain = text_of(values["domains"], "domain").lower()
            if self.company_ids(domain):
                return FakeHTTP(400, {"message": "uniqueness constraint violated for domains"})
            cid = self.add_company(text_of(values["name"], "value"), domain)
            return FakeHTTP(200, {"data": self.company_record(cid)})

        found = re.fullmatch(r"/v2/objects/deals/records/([^/]+)", path)
        if method == "GET" and found:
            return FakeHTTP(200, {"data": self.deal_record(found.group(1))})
        if method == "POST" and path == "/v2/objects/deals/records":
            values = body["data"]["values"]
            stage = text_of(values["stage"], "status")
            if stage not in self.stages:
                return FakeHTTP(400, {"message": "unknown stage"})
            owner = text_of(values.get("owner"), "referenced_actor_id")
            if not owner:
                return FakeHTTP(400, {"message": "owner is required"})
            if self.reject_owner_on_deal or owner not in self.members.values():
                return FakeHTTP(400, {"message": "Cannot find workspace member for owner"})
            did = self.add_deal(
                text_of(values["name"], "value"), stage,
                person=text_of(values.get("associated_people"), "target_record_id"),
                company=text_of(values.get("associated_company"), "target_record_id"),
            )
            self.deals[did]["owner"] = owner
            return FakeHTTP(200, {"data": self.deal_record(did)})

        if method == "POST" and path == "/v2/notes":
            data = body["data"]
            if data["parent_record_id"] not in self.deals:
                return FakeHTTP(404, {"message": "parent record not found"})
            self.notes.append({"parent_object": data["parent_object"], "parent_record_id": data["parent_record_id"],
                               "title": data["title"], "content": data["content"]})
            return FakeHTTP(200, {"data": {"id": {"note_id": self.next_id("n")}, "title": data["title"]}})
        if method == "GET" and path == "/v2/notes":
            parent = params.get("parent_record_id")
            notes = [n for n in self.notes if n["parent_record_id"] == parent]
            return FakeHTTP(200, {"data": [{"id": {"note_id": f"n-{i}"}, "title": n["title"],
                                            "content_plaintext": n["content"]} for i, n in enumerate(notes)]})
        raise KeyError(path)


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


def stat_numbers(output, labels):
    """Pull numbers out of report lines like 'People created: 2'."""
    numbers = []
    for label in labels:
        found = re.search(re.escape(label) + r"\s*(\d+)", output)
        numbers.append(int(found.group(1)) if found else None)
    return numbers


FREE_MAIL_DOMAINS = [
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "proton.me", "protonmail.com", "hotmail.co.uk", "yahoo.co.uk",
]


class LeadsToAttioTests(unittest.TestCase):
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
        (self.prompts_dir / "v2.md").write_text("PROMPT-V2: read the email carefully.", encoding="utf-8")
        self.state_path = self.tmp / "leads-to-attio-state.json"
        self.triage_state_path = self.tmp / "triage-state.json"      # the triage's own file (read only here)

        for module, name, value in (
            (outlook_signin, "ENV_PATH", self.env_path),
            (outlook_to_slack, "ENV_PATH", self.env_path),
            (extract_leads, "ENV_PATH", self.env_path),
            (extract_leads, "CONFIG_PATH", self.config_path),
            (extract_leads, "PROMPTS_DIR", self.prompts_dir),
            (leads_to_attio, "STATE_PATH", self.state_path),
            (outlook_triage, "STATE_PATH", self.triage_state_path),
        ):
            patcher = mock.patch.object(module, name, value)
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

    # ---- helpers ---------------------------------------------------------------------

    def run_main(self, outlook, attio, gemini, argv=(), remove=(), env_changes=None, token_error=None):
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        if token_error is not None:
            token_patch = mock.patch.object(leads_to_attio, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(leads_to_attio, "get_access_token", return_value=FAKE_TOKEN)
        env = dict(FAKE_ENV)
        env.update(env_changes or {})
        for message in list(outlook.leads.values()):
            if message["id"] not in outlook.extensions:
                saved = saved_from_reply(gemini.answer_for(message))
                if saved is not None:
                    outlook.extensions[message["id"]] = saved
        env_before = self.env_path.read_bytes()
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["leads_to_attio.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", outlook.urlopen), \
                mock.patch("requests.post", gemini.post), \
                mock.patch("requests.request", attio.request), \
                token_patch:
            for name in remove:
                os.environ.pop(name, None)
            code = leads_to_attio.main()
        output = buffer.getvalue().decode("utf-8")
        saved = self.saved_text()
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a key, token or the owner was printed on screen")
            self.assertNotIn(secret, saved, "a key, token or the owner was saved to a file")
            for request in outlook.requests:
                self.assertNotIn(secret, request.full_url)
            for call in gemini.calls:
                self.assertNotIn(secret, call["url"])
            for request in attio.requests:
                self.assertNotIn(secret, request["path"])
                self.assertNotIn(secret, json.dumps(request["body"]))
        self.assertNotIn(BODY_MARKER, saved, "email text was saved to a file")
        self.assertEqual(gemini.calls, [], "the Attio step called Gemini")
        self.assertEqual(outlook.unexpected, [], "an unexpected request went to Outlook")
        self.assertEqual(attio.unexpected, [], "an unexpected request went to Attio")
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

    def live(self, outlook, attio, gemini, *extra, **kwargs):
        return self.run_main(outlook, attio, gemini, ["--live", *extra], **kwargs)

    def dry(self, outlook, attio, gemini, *extra, **kwargs):
        return self.run_main(outlook, attio, gemini, list(extra), **kwargs)

    def one_lead(self, subject="Demo please", reply=None, **message_kwargs):
        outlook = PretendOutlook(leads=[lead_msg("m1", subject, **message_kwargs)])
        gemini = PretendGemini({subject: reply if reply is not None else info()})
        return outlook, PretendAttio(), gemini

    # ---- Case 1: a new person at a new company -------------------------------------------

    def test_case1_new_person_new_company_deal_note_and_move(self):
        outlook, attio, gemini = self.one_lead()
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (pid,) = attio.person_ids("nora@brightwell.example")
        person = attio.people[pid]
        self.assertEqual(person["name"], "Nora Fischer")
        self.assertEqual(person["job_title"], "Operations Manager")
        (cid,) = attio.company_ids("brightwell.example")
        self.assertEqual(attio.companies[cid]["name"], "Brightwell Gyms")
        (deal,) = attio.deals_of(pid)
        self.assertEqual(deal["name"], "Brightwell Gyms: demo")
        self.assertEqual(deal["stage"], "New")
        self.assertEqual(deal["owner"], OWNER_ID)
        self.assertEqual(deal["company"], cid)
        (note,) = attio.notes
        self.assertIn("Wants a demo of the booking software.", note["content"])
        self.assertIn("Request type: demo", note["content"])
        self.assertIn("Urgency: 2", note["content"])
        self.assertIn("Demo please", note["content"])
        self.assertEqual(outlook.where("m1"), "processed")
        self.assertIn("m1", json.dumps(self.state()))

    # ---- Case 2: existing person, deal at New ------------------------------------------------

    def preload_nora(self, attio, deals=("New",)):
        cid = attio.add_company("Brightwell Gyms", "brightwell.example")
        pid = attio.add_person("nora@brightwell.example", "Nora Fischer", "Operations Manager", cid)
        made = [attio.add_deal(f"Old deal {i}", stage, pid, cid) for i, stage in enumerate(deals)]
        return pid, cid, made

    def test_case2_a_new_email_adds_a_note_to_the_existing_new_deal(self):
        outlook, attio, gemini = self.one_lead()
        pid, cid, (deal_id,) = self.preload_nora(attio)
        before = (len(attio.people), len(attio.companies), len(attio.deals))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual((len(attio.people), len(attio.companies), len(attio.deals)), before)
        (note,) = attio.notes_of(deal_id)
        self.assertIn("Wants a demo of the booking software.", note["content"])
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case2_with_two_new_deals_the_newest_gets_the_note(self):
        outlook, attio, gemini = self.one_lead()
        pid, cid, (older, newer) = self.preload_nora(attio, deals=("New", "New"))
        self.live(outlook, attio, gemini)
        self.assertEqual(len(attio.notes_of(older)), 0)
        self.assertEqual(len(attio.notes_of(newer)), 1)

    # ---- Case 3: existing person, deals at other stages ------------------------------------------

    def test_case3_other_stages_get_a_new_deal_and_stay_untouched(self):
        outlook, attio, gemini = self.one_lead()
        pid, cid, (won, lost) = self.preload_nora(attio, deals=("Won", "Lost"))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(attio.people), 1)
        self.assertEqual(len(attio.companies), 1)
        self.assertEqual(len(attio.deals), 3)
        self.assertEqual(attio.deals[won]["stage"], "Won")
        self.assertEqual(attio.deals[lost]["stage"], "Lost")
        new_deals = [d for d in attio.deals_of(pid) if d["stage"] == "New"]
        self.assertEqual(len(new_deals), 1)
        self.assertEqual(new_deals[0]["company"], cid)
        self.assertEqual(len(attio.notes), 1)

    # ---- Case 4: two people from the same company ---------------------------------------------------

    def test_case4_two_people_one_company_two_deals(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "From Nora", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "From Omar", sender="omar@brightwell.example", name="Omar Haddad",
                     received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({
            "From Nora": info(),
            "From Omar": info(name="Omar Haddad", email="omar@brightwell.example"),
        })
        attio = PretendAttio()
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(attio.people), 2)
        self.assertEqual(len(attio.companies), 1)
        self.assertEqual(len(attio.deals), 2)
        (cid,) = attio.companies
        self.assertTrue(all(d["company"] == cid for d in attio.deals.values()))

    # ---- Case 5: a free-mail sender ------------------------------------------------------------------

    def test_case5_free_mail_gets_no_company_and_the_name_goes_in_the_note(self):
        reply = info(name="Rhys Baker", email="rhys.baker@gmail.com", company="Baker Yoga Studio",
                     request_type="pricing")
        outlook, attio, gemini = self.one_lead("Prices please", reply, sender="rhys.baker@gmail.com", name="Rhys Baker")
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.companies, {})
        (pid,) = attio.person_ids("rhys.baker@gmail.com")
        (deal,) = attio.deals_of(pid)
        self.assertEqual(deal["name"], "Rhys Baker: pricing")
        self.assertIsNone(deal["company"])
        (note,) = attio.notes
        self.assertIn("Company named in the email: Baker Yoga Studio", note["content"])

    def test_case5_free_mail_without_a_company_says_not_stated(self):
        reply = info(name="Rhys Baker", email="rhys.baker@gmail.com", company=None)
        outlook, attio, gemini = self.one_lead("Prices please", reply, sender="rhys.baker@gmail.com")
        self.live(outlook, attio, gemini)
        (note,) = attio.notes
        self.assertIn("Company named in the email: not stated", note["content"])

    # ---- Case 6: the free-mail list -------------------------------------------------------------------

    def test_case6_listed_free_mail_domains_get_no_company(self):
        for domain in FREE_MAIL_DOMAINS:
            for address in (f"sam@{domain}", f"SAM@{domain.upper()}"):
                with self.subTest(address=address):
                    self.state_path.unlink(missing_ok=True)
                    outlook, attio, gemini = self.one_lead("Hello", info(name="Sam Lee", email=address, company=None))
                    code, _ = self.live(outlook, attio, gemini)
                    self.assertEqual(code, 0)
                    self.assertEqual(attio.companies, {})

    def test_case6_look_alike_business_domains_get_a_company(self):
        for domain in ("gmail-fans.example", "notoutlook.example"):
            with self.subTest(domain=domain):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead("Hello", info(name="Sam Lee", email=f"sam@{domain}", company=None))
                self.live(outlook, attio, gemini)
                self.assertEqual(len(attio.company_ids(domain)), 1)

    # ---- Case 7: a forwarded email ----------------------------------------------------------------------

    def test_case7_a_forwarded_email_is_about_the_original_enquirer(self):
        reply = info(name="Rui Santos", email="rui@santos.example", company="Santos Pilates")
        outlook, attio, gemini = self.one_lead(
            "Fwd: interested in your software", reply, sender="colleague@brightwell.example", name="A Colleague")
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(attio.person_ids("rui@santos.example")), 1)
        self.assertEqual(attio.person_ids("colleague@brightwell.example"), [])
        self.assertEqual(len(attio.company_ids("santos.example")), 1)
        self.assertEqual(attio.company_ids("brightwell.example"), [])

    # ---- Case 8: a Dutch email --------------------------------------------------------------------------------

    def test_case8_a_dutch_email_is_handled_with_an_english_note(self):
        reply = info(name="Fleur Bakker", email="fleur@aan-zee.example", company="Studio Aan Zee",
                     summary="Asks for an online demo next week.")
        outlook, attio, gemini = self.one_lead("Demo aanvragen", reply, sender="fleur@aan-zee.example")
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (note,) = attio.notes
        self.assertIn("Asks for an online demo next week.", note["content"])
        (pid,) = attio.person_ids("fleur@aan-zee.example")
        self.assertEqual(attio.people[pid]["name"], "Fleur Bakker")
        self.assertEqual(outlook.where("m1"), "processed")

    # ---- Case 9: the same email twice ---------------------------------------------------------------------------

    def test_case9_the_same_email_listed_twice_is_handled_once(self):
        outlook, attio, gemini = self.one_lead()
        original = outlook.urlopen

        def twice(request, timeout=None):
            response = original(request, timeout)
            if request.get_method() == "GET" and request.full_url.split("?")[0].endswith("/leads-id/messages"):
                one = json.loads(response.read().decode())["value"]
                return FakeResponse(json.dumps({"value": one + one}).encode())
            return response

        outlook.urlopen = twice
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))

    def test_case9_an_email_already_done_but_back_in_leads_is_left_alone(self):
        outlook, attio, gemini = self.one_lead()
        self.live(outlook, attio, gemini)
        outlook.add_lead(lead_msg("m1", "Demo please"))          # moved back by hand, same id as before
        calls, counts = len(gemini.calls), attio.counts()
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), calls)
        self.assertEqual(attio.counts(), counts)
        self.assertIn("Already handled earlier", output)
        self.assertEqual(outlook.where("m1"), "leads")

    # ---- Case 10: two emails from the same new person in one run -----------------------------------------------------

    def test_case10_the_second_email_adds_a_note_instead_of_a_second_deal(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "Second email", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"First email": info(), "Second email": info(summary="Following up.")})
        attio = PretendAttio()
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 2))
        self.assertEqual(outlook.where("m1"), "processed")
        self.assertEqual(outlook.where("m2"), "processed")

    # ---- Case 11: a missing name -------------------------------------------------------------------------------------

    def test_case11_a_missing_name_is_not_invented(self):
        outlook, attio, gemini = self.one_lead("Hello", info(name=None))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (pid,) = attio.person_ids("nora@brightwell.example")
        self.assertIsNone(attio.people[pid]["name"])
        (deal,) = attio.deals_of(pid)
        self.assertEqual(deal["name"], "Brightwell Gyms: demo")

    def test_case11_no_name_and_no_company_uses_the_domain_or_the_address(self):
        outlook, attio, gemini = self.one_lead("Hello", info(name=None, company=None))
        self.live(outlook, attio, gemini)
        (pid,) = attio.person_ids("nora@brightwell.example")
        self.assertEqual(attio.deals_of(pid)[0]["name"], "brightwell.example: demo")

        outlook, attio, gemini = self.one_lead("Hello", info(name=None, company=None, email="sam@gmail.com"))
        self.state_path.unlink(missing_ok=True)
        self.live(outlook, attio, gemini)
        (pid,) = attio.person_ids("sam@gmail.com")
        self.assertEqual(attio.deals_of(pid)[0]["name"], "sam@gmail.com: demo")

    # ---- Case 12: a missing company at a business domain ---------------------------------------------------------------

    def test_case12_a_company_is_made_from_the_domain_when_none_is_named(self):
        outlook, attio, gemini = self.one_lead(
            "Hello", info(name="Jo Smith", email="jo@maple-physio.example", company=None))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (cid,) = attio.company_ids("maple-physio.example")
        self.assertEqual(attio.companies[cid]["name"], "maple-physio.example")
        (pid,) = attio.person_ids("jo@maple-physio.example")
        self.assertEqual(attio.deals_of(pid)[0]["name"], "maple-physio.example: demo")

    # ---- Case 13: an email address in capital letters --------------------------------------------------------------------

    def test_case13_capital_letters_do_not_cause_duplicates(self):
        outlook, attio, gemini = self.one_lead("Hello", info(email="Nora@BRIGHTWELL.EXAMPLE"))
        self.preload_nora(attio, deals=())
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(attio.people), 1)
        self.assertEqual(len(attio.companies), 1)
        self.assertEqual(len(attio.deals), 1)

    def test_case13_new_addresses_are_stored_in_small_letters(self):
        outlook, attio, gemini = self.one_lead("Hello", info(email="Nora@BRIGHTWELL.EXAMPLE"))
        self.live(outlook, attio, gemini)
        self.assertEqual([p["email"] for p in attio.people.values()], ["nora@brightwell.example"])

    # ---- Case 14: names with accents and apostrophes ------------------------------------------------------------------------

    def test_case14_names_are_kept_exactly_as_written(self):
        for name in ("Zoë Müller", "Seán O'Brien", "Renée D'Souza-Łukasz"):
            with self.subTest(name=name):
                self.state_path.unlink(missing_ok=True)
                reply = info(name=name, email="x@gmail.com", company=None, summary=f"{name} wants a demo.")
                outlook, attio, gemini = self.one_lead("Hello", reply, sender="x@gmail.com")
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                (pid,) = attio.person_ids("x@gmail.com")
                self.assertEqual(attio.people[pid]["name"], name)
                self.assertEqual(attio.deals_of(pid)[0]["name"], f"{name}: demo")
                self.assertIn(f"{name} wants a demo.", attio.notes[0]["content"])

    # ---- Case 15: subdomain addresses -------------------------------------------------------------------------------------------

    def test_case15_subdomains_belong_to_the_main_company(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "From Ana", sender="ana@mail.maple-physio.example", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "From Ben", sender="ben@maple-physio.example", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({
            "From Ana": info(name="Ana", email="ana@mail.maple-physio.example", company="Maple Physio"),
            "From Ben": info(name="Ben", email="ben@maple-physio.example", company="Maple Physio"),
        })
        attio = PretendAttio()
        self.live(outlook, attio, gemini)
        self.assertEqual([c["domain"] for c in attio.companies.values()], ["maple-physio.example"])

    def test_case15_two_part_endings_keep_both_parts(self):
        outlook, attio, gemini = self.one_lead(
            "Hello", info(name="Cai", email="cai@mail.acme.co.uk", company="Acme"))
        self.live(outlook, attio, gemini)
        self.assertEqual([c["domain"] for c in attio.companies.values()], ["acme.co.uk"])

    # ---- Case 63: a company lookup prefers the record whose domain is exactly the root domain -------------------
    # (found in the Day 8 run: Attio itself made a second company for freemail.example.com, and the lookup, which
    #  matches subdomains too, then took the first match and split the leads across the two companies)

    def two_companies(self, attio, subdomain_first):
        """A company at a subdomain and one at the exact root domain, in the order asked for. Returns their ids."""
        if subdomain_first:
            sub = attio.add_company("Sub Brightwell", "freemail.brightwell.example")
            exact = attio.add_company("Brightwell Gyms", "brightwell.example")
        else:
            exact = attio.add_company("Brightwell Gyms", "brightwell.example")
            sub = attio.add_company("Sub Brightwell", "freemail.brightwell.example")
        return sub, exact

    def test_case63_the_exact_root_domain_company_wins_whichever_comes_first(self):
        for subdomain_first in (True, False):
            with self.subTest(subdomain_first=subdomain_first):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead()
                sub, exact = self.two_companies(attio, subdomain_first)
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(len(attio.companies), 2, "no company may be created")
                (pid,) = attio.person_ids("nora@brightwell.example")
                (deal,) = attio.deals_of(pid)
                self.assertEqual(deal["company"], exact)
                self.assertEqual(deal["name"], "Brightwell Gyms: demo")
                self.assertEqual(attio.people[pid]["company"], exact)

    def test_case63_a_sender_on_a_subdomain_still_gets_the_exact_root_domain_company(self):
        for subdomain_first in (True, False):
            with self.subTest(subdomain_first=subdomain_first):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead(
                    "Demo please", info(name="Lena Fischer", email="lena@freemail.brightwell.example"),
                    sender="lena@freemail.brightwell.example", name="Lena Fischer")
                sub, exact = self.two_companies(attio, subdomain_first)
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(len(attio.companies), 2)
                (pid,) = attio.person_ids("lena@freemail.brightwell.example")
                (deal,) = attio.deals_of(pid)
                self.assertEqual(deal["company"], exact)
                self.assertEqual(deal["name"], "Brightwell Gyms: demo")

    def test_case63_every_lead_in_a_run_uses_the_same_exact_company(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "From Nora", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "From Lena", sender="lena@freemail.brightwell.example", name="Lena Fischer",
                     received="2026-10-20T09:00:00Z"),
            lead_msg("m3", "From Omar", sender="omar@brightwell.example", name="Omar Haddad",
                     received="2026-10-20T10:00:00Z"),
        ])
        gemini = PretendGemini({
            "From Nora": info(),
            "From Lena": info(name="Lena Fischer", email="lena@freemail.brightwell.example"),
            "From Omar": info(name="Omar Haddad", email="omar@brightwell.example"),
        })
        attio = PretendAttio()
        sub, exact = self.two_companies(attio, subdomain_first=True)
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(attio.companies), 2)
        self.assertEqual(sorted({d["company"] for d in attio.deals.values()}), [exact])
        self.assertEqual(len(attio.deals), 3)

    def test_case63_a_subdomain_company_is_only_the_fallback_when_there_is_no_exact_one(self):
        outlook, attio, gemini = self.one_lead()
        only = attio.add_company("Sub Brightwell", "mail.brightwell.example")
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(attio.companies), 1, "the subdomain company is used, and no company is created")
        (pid,) = attio.person_ids("nora@brightwell.example")
        (deal,) = attio.deals_of(pid)
        self.assertEqual(deal["company"], only)
        self.assertEqual(deal["name"], "Sub Brightwell: demo")

    def test_case63_with_no_company_at_all_one_is_still_made_for_the_root_domain(self):
        outlook, attio, gemini = self.one_lead(
            "Demo please", info(name="Lena Fischer", email="lena@freemail.brightwell.example"),
            sender="lena@freemail.brightwell.example", name="Lena Fischer")
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual([c["domain"] for c in attio.companies.values()], ["brightwell.example"])

    def test_case63_a_dry_run_plans_the_exact_company_too(self):
        outlook, attio, gemini = self.one_lead()
        self.two_companies(attio, subdomain_first=True)
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("company already exists: brightwell.example", output)
        self.assertIn("would create deal: Brightwell Gyms: demo", output)
        self.assertEqual(attio.writes(), [])

    def test_case63_two_part_endings_keep_both_parts_and_still_prefer_the_exact_domain(self):
        outlook, attio, gemini = self.one_lead(
            "Demo please", info(name="Nora Fischer", email="nora@mail.acme.co.uk"),
            sender="nora@mail.acme.co.uk", name="Nora Fischer")
        sub = attio.add_company("Mail Acme", "mail.acme.co.uk")
        exact = attio.add_company("Acme Ltd", "acme.co.uk")
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (pid,) = attio.person_ids("nora@mail.acme.co.uk")
        (deal,) = attio.deals_of(pid)
        self.assertEqual(deal["company"], exact)
        self.assertEqual(deal["name"], "Acme Ltd: demo")

    # ---- Case 16: Attio cannot be reached ---------------------------------------------------------------------------------------------

    def attio_trouble(self, outcome, hint):
        outlook, attio, gemini = self.one_lead()
        attio.fail("POST", "/v2/objects/people/records/query", outcome)
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("I couldn't reach Attio", output)
        self.assertIn(hint, output)
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(attio.writes(), [])
        self.assertNotIn("Traceback", output)

    def test_case16_a_timeout_names_the_reason(self):
        self.attio_trouble(Raise(requests.exceptions.Timeout("slow")), "(the request timed out)")

    def test_case16_no_connection_names_the_reason(self):
        self.attio_trouble(Raise(requests.exceptions.ConnectionError("down")), "(no connection)")

    def test_case16_a_server_error_shows_the_code(self):
        self.attio_trouble(Status(503, f"overloaded {FAKE_ATTIO_KEY} {BODY_MARKER}"), "(Attio answered with code 503)")

    def test_case16_earlier_emails_stay_finished(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "Second email", sender="omar@other.example", name="Omar", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"First email": info(),
                                "Second email": info(name="Omar", email="omar@other.example", company="Other Co")})
        attio = PretendAttio()
        original = attio.request

        def flaky(method, url, **kw):
            # Attio becomes unreachable when the second person is looked up.
            if (method == "POST" and url.endswith("/v2/objects/people/records/query")
                    and (kw.get("json") or {}).get("filter", {}).get("email_addresses") == "omar@other.example"):
                raise requests.exceptions.ConnectionError("down")
            return original(method, url, **kw)

        attio.request = flaky
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertEqual(outlook.where("m1"), "processed")
        self.assertEqual(outlook.where("m2"), "leads")

    # ---- Case 17: Attio rejects the key ----------------------------------------------------------------------------------------------------

    def test_case17_a_rejected_attio_key_stops_and_is_never_shown(self):
        outlook, attio, gemini = self.one_lead()
        code, output = self.live(outlook, attio, gemini, env_changes={"ATTIO_API_KEY": "WRONG-ATTIO-KEY-SECRET"})
        self.assertEqual(code, 1)
        self.assertIn("Attio did not accept the key", output)
        self.assertNotIn("WRONG-ATTIO-KEY-SECRET", output)
        self.assertNotIn("WRONG-ATTIO-KEY-SECRET", self.saved_text())
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(attio.counts(), (0, 0, 0, 0))

    def test_case17_a_401_says_the_key_is_not_valid(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                attio.fail("GET", "/v2/workspace_members", Status(401, f"LEAKY-ATTIO-TEXT {FAKE_ATTIO_KEY}"))
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 1)
                self.assertIn("Attio did not accept the key (code 401: the key is not valid)", output)
                self.assertNotIn("LEAKY-ATTIO-TEXT", output)
                self.assertEqual(attio.writes(), [])
                self.assertEqual(gemini.calls, [])

    def test_case17_a_403_says_there_is_no_permission(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                attio.fail("GET", "/v2/workspace_members", Status(403, f"LEAKY-ATTIO-TEXT {FAKE_ATTIO_KEY}"))
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 1)
                self.assertIn("Attio did not accept the key (code 403: no permission)", output)
                self.assertIn("permissions", output)
                self.assertNotIn("LEAKY-ATTIO-TEXT", output)
                self.assertEqual(attio.writes(), [])
                self.assertEqual(gemini.calls, [])

    def test_case17_a_permission_missing_later_in_the_run_is_named_too(self):
        outlook, attio, gemini = self.one_lead()
        attio.fail("POST", "/v2/notes", Status(403, "forbidden"))
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("(code 403: no permission)", output)
        self.assertEqual(outlook.where("m1"), "leads")

    def test_case17_a_missing_attio_key_stops_before_anything(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                code, output = self.run_main(outlook, attio, gemini, argv, remove=["ATTIO_API_KEY"])
                self.assertEqual(code, 1)
                self.assertIn("I can't find ATTIO_API_KEY", output)
                self.assertEqual(gemini.calls, [])
                self.assertEqual(attio.requests, [])
                self.assertEqual(outlook.changes(), [])

    # ---- Case 18: Attio says "too many requests" -------------------------------------------------------------------------------------------------

    def test_case18_one_limit_hit_waits_and_tries_again(self):
        outlook, attio, gemini = self.one_lead()
        attio.fail("POST", "/v2/objects/people/records/query", Status(429, "slow down"))
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn(leads_to_attio.RETRY_WAIT_SECONDS, self.sleeps)
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case18_still_limited_after_the_retry_stops_cleanly(self):
        outlook, attio, gemini = self.one_lead()
        attio.fail("POST", "/v2/objects/people/records/query", Status(429, "slow down"))
        attio.fail("POST", "/v2/objects/people/records/query", Status(429, "slow down"))
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("request limit was reached", output)
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(attio.writes(), [])

    # ---- Cases 19 to 22: failures part way, then a retry ------------------------------------------------------------------------------------------------

    def fail_then_retry(self, method, path_end, outcome=None):
        outlook, attio, gemini = self.one_lead()
        attio.fail(method, path_end, outcome or Status(503, "overloaded"))
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertEqual(outlook.where("m1"), "leads")
        return outlook, attio, gemini, output

    def test_case19_person_created_then_company_fails_then_the_retry_finishes(self):
        outlook, attio, gemini, output = self.fail_then_retry("POST", "/v2/objects/companies/records")
        self.assertEqual(attio.counts(), (1, 0, 0, 0))
        calls = len(gemini.calls)
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(len(gemini.calls), calls, "Gemini must not be asked again on the retry")
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case20_company_created_then_deal_fails_then_the_retry_finishes(self):
        outlook, attio, gemini, output = self.fail_then_retry("POST", "/v2/objects/deals/records")
        self.assertEqual(attio.counts(), (1, 1, 0, 0))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))

    def test_case21_deal_created_then_note_fails_then_no_second_deal(self):
        outlook, attio, gemini, output = self.fail_then_retry("POST", "/v2/notes")
        self.assertEqual(attio.counts(), (1, 1, 1, 0))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case22_a_failed_move_is_retried_without_repeating_attio_work(self):
        outlook, attio, gemini = self.one_lead()
        outlook.move_errors = {1: http_error(403)}
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("saved in Attio", output)
        self.assertIn("could not be moved", output)
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(attio.counts(), (1, 1, 1, 1))

        outlook.move_errors = {}
        calls, writes = len(gemini.calls), len(attio.writes())
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(len(attio.writes()), writes, "no new Attio writes on the retry")
        self.assertEqual(len(gemini.calls), calls, "Gemini must not be asked again")
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case22_a_move_that_cannot_reach_outlook_is_retried_too(self):
        outlook, attio, gemini = self.one_lead()
        outlook.move_errors = {1: urllib.error.URLError("down")}
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertEqual(outlook.where("m1"), "leads")
        outlook.move_errors = {}
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))

    # ---- Case 23: the state file is lost part way ----------------------------------------------------------------------------------------------------------

    def test_case23_a_lost_state_file_does_not_cause_duplicates(self):
        outlook, attio, gemini = self.one_lead()
        outlook.move_errors = {1: http_error(403)}
        self.live(outlook, attio, gemini)
        self.state_path.unlink()
        outlook.move_errors = {}
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case23_a_damaged_state_file_is_set_aside_and_causes_no_duplicates(self):
        outlook, attio, gemini = self.one_lead()
        outlook.move_errors = {1: http_error(403)}
        self.live(outlook, attio, gemini)
        self.state_path.write_text("{ not valid json", encoding="utf-8")
        outlook.move_errors = {}
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("damaged", output)
        aside = [p for p in self.tmp.iterdir() if p.name.startswith("leads-to-attio-state.json.damaged")]
        self.assertEqual(len(aside), 1)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))

    # ---- Case 24: a dry run reads and looks things up, but changes nothing ----------------------------------------------------------------------------------

    def test_case24_a_dry_run_looks_things_up_and_changes_nothing(self):
        outlook, attio, gemini = self.one_lead()
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertEqual(len(gemini.calls), 0)
        self.assertGreater(len(attio.reads()), 0)
        self.assertEqual(attio.writes(), [])
        self.assertEqual(attio.counts(), (0, 0, 0, 0))
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertFalse(self.state_path.exists())

    def test_default_is_a_dry_run(self):
        outlook, attio, gemini = self.one_lead()
        code, output = self.dry(outlook, attio, gemini)
        self.assertIn("Dry run", output)
        self.assertEqual(attio.counts(), (0, 0, 0, 0))

    # ---- Case 25: a live run really acts ----------------------------------------------------------------------------------------------------------------------

    def test_case25_the_final_report_counts_what_happened(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "Second email", received="2026-10-20T09:00:00Z"),
            lead_msg("m3", "Third email", sender="ben@maple.example", name="Ben", received="2026-10-20T10:00:00Z"),
        ])
        gemini = PretendGemini({
            "First email": info(), "Second email": info(),
            "Third email": info(name="Ben", email="ben@maple.example", company="Maple"),
        })
        attio = PretendAttio()
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(stat_numbers(output, ["Leads handled:", "People created:", "Companies created:",
                                               "Deals created:", "Notes added to existing deals:",
                                               "Left in Leads:"]), [3, 2, 2, 2, 1, 0])
        self.assertEqual(attio.counts(), (2, 2, 2, 3))

    # ---- Case 26: the maximum emails per run ------------------------------------------------------------------------------------------------------------------

    def test_case26_only_the_maximum_are_handled_oldest_first(self):
        messages = [lead_msg(f"m{i}", f"Email number {i}", sender=f"p{i}@co{i}.example", name=f"P{i}",
                             received=f"2026-10-20T0{i}:00:00Z") for i in range(5)]
        outlook = PretendOutlook(leads=messages)
        gemini = PretendGemini({f"Email number {i}": info(name=f"P{i}", email=f"p{i}@co{i}.example",
                                                           company=f"Co{i}") for i in range(5)})
        attio = PretendAttio()
        with mock.patch.object(leads_to_attio, "MAX_PER_RUN", 2):
            code, output = self.live(outlook, attio, gemini)
            self.assertEqual(code, 0)
            self.assertEqual([outlook.where(f"m{i}") for i in range(5)],
                             ["processed", "processed", "leads", "leads", "leads"])
            self.assertIn("3 still waiting", output)
            self.live(outlook, attio, gemini)
        self.assertEqual([outlook.where(f"m{i}") for i in range(5)],
                         ["processed", "processed", "processed", "processed", "leads"])

    # ---- Cases 27 and 28: missing folders ------------------------------------------------------------------------------------------------------------------------------

    def test_case27_a_missing_processed_folder_stops_before_anything(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                outlook.has_processed_folder = False
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 1)
                self.assertIn("couldn't find a folder called Processed", output)
                self.assertEqual(gemini.calls, [])
                self.assertEqual(attio.writes(), [])
                self.assertEqual(outlook.changes(), [])

    def test_case28_a_missing_leads_folder_stops_before_anything(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                outlook.has_leads_folder = False
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 1)
                self.assertIn("couldn't find a folder called Leads", output)
                self.assertEqual(gemini.calls, [])
                self.assertEqual(attio.writes(), [])

    # ---- Case 29: a very long summary --------------------------------------------------------------------------------------------------------------------------------------

    def test_case29_long_text_is_cut_and_nothing_fails(self):
        long_summary = "Very long summary. " * 1000
        long_company = "Enormous Company Name " * 25
        outlook, attio, gemini = self.one_lead("Hello", info(summary=long_summary, company=long_company))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (note,) = attio.notes
        self.assertIn("(shortened)", note["content"])
        self.assertLess(len(note["content"]), len(long_summary))
        self.assertLessEqual(len(next(iter(attio.deals.values()))["name"]), 150)
        self.assertEqual(outlook.where("m1"), "processed")

    # ---- Case 30: an empty or unusable email address -------------------------------------------------------------------------------------------------------------------------

    def test_case30_unusable_addresses_create_nothing_and_are_not_retried_every_run(self):
        for bad in (None, "", "n/a", "unknown", "sam at example", "sam @example.example", "no-at-sign.example"):
            with self.subTest(bad=bad):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead("Hello", info(email=bad), sender="colleague@brightwell.example")
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertIn("Needs a person", output)
                self.assertIn("Hello", output)
                self.assertEqual(attio.counts(), (0, 0, 0, 0))
                self.assertEqual(attio.person_ids("colleague@brightwell.example"), [])
                self.assertEqual(outlook.where("m1"), "leads")
                calls = len(gemini.calls)
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(len(gemini.calls), calls, "Gemini must not be asked again")
                self.assertIn("Still needs a person", output)

    # ---- Cases 31 and 32: the owner ----------------------------------------------------------------------------------------------------------------------------------------------

    def test_case31_a_missing_or_empty_owner_stops_before_anything(self):
        for argv in (["--live"], []):
            for how in ("missing", "empty"):
                with self.subTest(argv=argv, how=how):
                    outlook, attio, gemini = self.one_lead()
                    if how == "missing":
                        code, output = self.run_main(outlook, attio, gemini, argv, remove=["ATTIO_DEAL_OWNER"])
                    else:
                        code, output = self.run_main(outlook, attio, gemini, argv, env_changes={"ATTIO_DEAL_OWNER": ""})
                    self.assertEqual(code, 1)
                    self.assertIn("I can't find ATTIO_DEAL_OWNER", output)
                    self.assertEqual(gemini.calls, [])
                    self.assertEqual(attio.writes(), [])
                    self.assertEqual(outlook.changes(), [])

    def test_case32_an_owner_attio_does_not_know_stops_at_the_start(self):
        outlook, attio, gemini = self.one_lead()
        attio.members = {"someone.else@example.com": "member-9"}
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("deal owner", output)
        self.assertEqual(attio.counts(), (0, 0, 0, 0))
        self.assertEqual(gemini.calls, [])
        self.assertEqual(outlook.where("m1"), "leads")

    def test_case32_an_owner_rejected_when_the_deal_is_made_keeps_earlier_records(self):
        outlook, attio, gemini = self.one_lead()
        attio.reject_owner_on_deal = True
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("Attio did not accept the deal owner", output)
        self.assertEqual(attio.counts(), (1, 1, 0, 0))
        self.assertEqual(outlook.where("m1"), "leads")
        attio.reject_owner_on_deal = False
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))

    # ---- Case 33: the owner and the keys are never shown or committed ------------------------------------------------------------------------------------------------------------------

    def test_case33_secrets_stay_out_of_output_and_files_on_success_and_failure(self):
        for setup in ("success", "attio_down", "move_fails"):
            with self.subTest(setup=setup):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead()
                if setup == "attio_down":
                    attio.fail("POST", "/v2/objects/people/records/query", Status(503, FAKE_ATTIO_KEY + FAKE_OWNER))
                if setup == "move_fails":
                    outlook.move_errors = {1: http_error(403)}
                self.live(outlook, attio, gemini)      # run_main checks every secret is absent

    def test_case33_the_committed_config_holds_no_owner_or_key(self):
        text = (PROJECT_ROOT / "workflows" / "02-email-to-attio" / "config.json").read_text(encoding="utf-8").lower()
        for word in ("owner", "attio", "key", "token", "@"):
            self.assertNotIn(word, text)

    def test_case33_the_state_file_is_ignored_by_git(self):
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("leads-to-attio-state.json", ignore)

    def test_case33_the_owner_is_only_asked_for_in_the_environment_or_env_file(self):
        outlook, attio, gemini = self.one_lead()
        self.run_main(outlook, attio, gemini, ["--live"], remove=["ATTIO_DEAL_OWNER"])  # missing -> stops
        self.env_path.write_text(f"ATTIO_DEAL_OWNER={FAKE_OWNER}\n", encoding="utf-8")
        outlook, attio, gemini = self.one_lead()
        code, _ = self.run_main(outlook, attio, gemini, ["--live"], remove=["ATTIO_DEAL_OWNER"])
        self.assertEqual(code, 0)

    # ---- Case 34: Gemini says it is not a lead, or cannot be trusted --------------------------------------------------------------------------------------------------------------------------

    def test_case34_not_a_lead_or_unusable_answers_create_nothing_and_are_not_retried(self):
        answers = {
            "not a lead": json.dumps({"is_lead": False}),
            "broken": "Sorry, I can only chat about yoga.",
            "incomplete": json.dumps({"is_lead": True, "name": "Sam"}),
        }
        for subject, reply in answers.items():
            with self.subTest(subject=subject):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead(subject, reply)
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertIn("Needs a person", output)
                self.assertEqual(attio.counts(), (0, 0, 0, 0))
                self.assertEqual(outlook.where("m1"), "leads")
                calls = len(gemini.calls)
                self.live(outlook, attio, gemini)
                self.assertEqual(len(gemini.calls), calls)

    # ---- Case 35 (replaced): the Attio step does not need Gemini at all -----------------------------------------
    # (tests/cases/11-single-call-pipeline.md, Part B: Cases A1 and A5)

    def test_case35_no_gemini_key_prompt_or_model_is_needed(self):
        outlook, attio, gemini = self.one_lead()
        self.config_path.unlink()
        for argv_live in (False, True):
            with self.subTest(live=argv_live):
                if argv_live:
                    code, output = self.live(outlook, attio, gemini, remove=["GEMINI_API_KEY"])
                else:
                    code, output = self.dry(outlook, attio, gemini, remove=["GEMINI_API_KEY"])
                self.assertEqual(code, 0)
                self.assertNotIn("GEMINI", output)
                self.assertNotIn("Prompt version", output)
                self.assertNotIn("model", output.lower())
        self.assertEqual(outlook.where("m1"), "processed")
        self.assertEqual(gemini.calls, [])

    def test_case35_the_old_prompt_option_is_gone(self):
        outlook, attio, gemini = self.one_lead()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                self.live(outlook, attio, gemini, "--prompt", "v2")
        self.assertEqual(outlook.changes(), [])

    def test_case35_a_problem_reading_a_saved_answer_is_not_mistaken_for_needs_a_person(self):
        cases = [
            (http_error(401), "sign-in may need redoing"),
            (http_error(403), "sign-in may need redoing"),
            (http_error(500), "refused"),
            (urllib.error.URLError("pretend: down"), "couldn't reach Outlook"),
        ]
        for error, words in cases:
            with self.subTest(error=str(error)[:30]):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead()
                outlook.read_error = error
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(code, 1)
                self.assertIn(words, output)
                self.assertNotIn("Needs a person", output)
                self.assertEqual(attio.writes(), [])
                self.assertEqual(outlook.where("m1"), "leads")
                if self.state_path.exists():
                    self.assertNotIn("attention", self.state_path.read_text(encoding="utf-8"))

    # ---- Case 36: an existing person only has empty fields filled ----------------------------------------------------------------------------------------------------------------------------------------

    def test_case36_existing_values_are_never_overwritten(self):
        outlook, attio, gemini = self.one_lead("Hello", info(name="N. Fischer", role="Boss"))
        other = attio.add_company("Other Co", "other.example")
        pid = attio.add_person("nora@brightwell.example", "Nora Fischer", "Operations Manager", other)
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        person = attio.people[pid]
        self.assertEqual(person["name"], "Nora Fischer")
        self.assertEqual(person["job_title"], "Operations Manager")
        self.assertEqual(person["company"], other)
        self.assertEqual(len(attio.people), 1)

    def test_case36_empty_fields_are_filled(self):
        outlook, attio, gemini = self.one_lead("Hello", info(name="Nora Fischer", role="Operations Manager"))
        pid = attio.add_person("nora@brightwell.example", None, None, None)
        self.live(outlook, attio, gemini)
        self.assertEqual(attio.people[pid]["name"], "Nora Fischer")
        self.assertEqual(attio.people[pid]["job_title"], "Operations Manager")

    # ---- Case 37: only the Leads folder itself is read ----------------------------------------------------------------------------------------------------------------------------------------------------

    def test_case37_inbox_and_processed_emails_are_never_read(self):
        outlook = PretendOutlook(
            leads=[lead_msg("m1", "Demo please")],
            inbox=[lead_msg("i1", "Inbox email")],
            processed=[lead_msg("p1", "Processed email")],
        )
        gemini = PretendGemini({"Demo please": info()})
        attio = PretendAttio()
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 0)
        for request in outlook.requests:
            self.assertNotIn("inbox/messages", request.full_url)
            self.assertNotIn("processed-id/messages", request.full_url)

    # ---- Case 38: the stage New does not exist -----------------------------------------------------------------------------------------------------------------------------------------------------------------

    def test_case38_a_missing_stage_stops_at_the_start(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                attio.stages = ["Won", "Lost"]
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 1)
                self.assertIn("couldn't find the stage New", output)
                self.assertEqual(attio.writes(), [])
                self.assertEqual(outlook.changes(), [])
                self.assertEqual(gemini.calls, [])

    # ---- Case 39: nothing is ever deleted, sent or replied to ---------------------------------------------------------------------------------------------------------------------------------------------------------

    def test_case39_only_safe_requests_are_ever_sent(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "Second email", sender="omar@other.example", name="Omar", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"First email": info(),
                                "Second email": info(name="Omar", email="omar@other.example", company="Other Co")})
        attio = PretendAttio()
        self.live(outlook, attio, gemini)
        self.assertTrue({r["method"] for r in attio.requests} <= {"GET", "POST", "PATCH"})
        for request in attio.requests:
            self.assertNotIn("merge", request["path"])
        for request in outlook.changes():
            self.assertEqual(request.get_method(), "POST")
            self.assertTrue(request.full_url.split("?")[0].endswith("/move"))
            for word in ("/send", "/reply", "/forward", "/copy", "/permanentdelete"):
                self.assertNotIn(word, request.full_url.lower())

    # ---- Case 40: --only-subject --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    # ---- Cases 54 to 56: --only-from (an email must match every filter given) -------------------------------------------

    def only_from_world(self):
        outlook = PretendOutlook(leads=[
            lead_msg("n1", "Demo request", sender="jo@nautspace.example", name="Jo Example",
                     received="2026-10-20T07:00:00Z"),
            lead_msg("o1", "Demo request for our team", sender="pat.other@gmail.com", name="Pat",
                     received="2026-10-20T08:00:00Z"),
            lead_msg("n2", "Something else", sender="jo@nautspace.example", name="Jo Example",
                     received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({
            "Demo request": info(name="Jo Example", email="jo@nautspace.example", company="Nautspace Ltd"),
            "Something else": info(name="Jo Example", email="jo@nautspace.example", company="Nautspace Ltd",
                                   summary="Something else."),
        })
        return outlook, PretendAttio(), gemini

    def test_case54_both_filters_must_match(self):
        outlook, attio, gemini = self.only_from_world()
        code, output = self.live(outlook, attio, gemini, "--only-subject", "Demo request",
                                 "--only-from", "jo@nautspace.example")
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 0)
        self.assertEqual([outlook.where(i) for i in ("n1", "o1", "n2")], ["processed", "leads", "leads"])
        self.assertEqual(list(self.state()["emails"]), ["n1"])
        self.assertEqual(attio.person_ids("pat.other@gmail.com"), [])
        self.assertEqual(len(attio.person_ids("jo@nautspace.example")), 1)

    def test_case54_only_from_alone_handles_every_email_from_that_sender(self):
        for address in ("jo@nautspace.example", "  JO@Nautspace.example "):
            with self.subTest(address=address):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.only_from_world()
                code, output = self.live(outlook, attio, gemini, "--only-from", address)
                self.assertEqual(code, 0)
                self.assertEqual(len(gemini.calls), 0)
                self.assertEqual([outlook.where(i) for i in ("n1", "o1", "n2")], ["processed", "leads", "processed"])
                self.assertEqual(sorted(self.state()["emails"]), ["n1", "n2"])

    def test_case55_a_partial_address_matches_nothing(self):
        for text in ("nautspace.example", "jo"):
            with self.subTest(text=text):
                outlook, attio, gemini = self.only_from_world()
                code, output = self.live(outlook, attio, gemini, "--only-from", text)
                self.assertEqual(code, 0)
                self.assertIn(f'No lead from "{text}" was found. Nothing was changed.', output)
                self.assertEqual(gemini.calls, [])
                self.assertEqual(attio.writes(), [])
                self.assertEqual([outlook.where(i) for i in ("n1", "o1", "n2")], ["leads", "leads", "leads"])

    def test_case55_the_message_names_both_filters_when_both_are_used(self):
        outlook, attio, gemini = self.only_from_world()
        code, output = self.live(outlook, attio, gemini, "--only-subject", "Demo request",
                                 "--only-from", "nobody@nautspace.example")
        self.assertEqual(code, 0)
        self.assertIn('No lead with "Demo request" in the subject from "nobody@nautspace.example" was found.', output)
        self.assertEqual(gemini.calls, [])

    def test_case55_an_empty_address_is_refused_before_anything_is_read(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                outlook, attio, gemini = self.only_from_world()
                code, output = self.live(outlook, attio, gemini, "--only-from", text)
                self.assertEqual(code, 1)
                self.assertIn("must not be empty", output)
                self.assertEqual(outlook.requests, [])
                self.assertEqual(attio.requests, [])
                self.assertEqual(gemini.calls, [])

    def test_case56_a_dry_run_with_both_filters_shows_exactly_one_email(self):
        outlook, attio, gemini = self.only_from_world()
        code, output = self.dry(outlook, attio, gemini, "--only-subject", "Demo request",
                                "--only-from", "jo@nautspace.example")
        self.assertEqual(code, 0)
        self.assertEqual(output.count("Email: "), 1)
        self.assertIn("Email: jo@nautspace.example, Demo request", output)
        self.assertIn("1 lead(s) would be handled", output)
        self.assertEqual(len(gemini.calls), 0)
        self.assertEqual(attio.writes(), [])
        self.assertEqual(outlook.changes(), [])
        self.assertFalse(self.state_path.exists())

    def test_case56_the_option_never_widens_the_search(self):
        outlook, attio, gemini = self.only_from_world()
        self.live(outlook, attio, gemini, "--only-subject", "Demo request", "--only-from", "jo@nautspace.example")
        outlook.add_lead(lead_msg("n1", "Demo request", sender="jo@nautspace.example"))   # back in Leads, same id
        outlook.add_lead(lead_msg("s1", "Your verification code", sender="jo@nautspace.example"))
        calls = len(gemini.calls)
        code, output = self.live(outlook, attio, gemini, "--only-from", "jo@nautspace.example")
        self.assertEqual(code, 0)
        self.assertIn("Already handled earlier, left alone: Demo request", output)
        self.assertIn("Skipped (system or security email): Your verification code", output)
        self.assertNotIn("Needs a person", output)
        self.assertEqual(outlook.where("s1"), "leads")
        self.assertEqual(len(gemini.calls), calls)

    def only_subject_world(self):
        outlook = PretendOutlook(leads=[
            lead_msg("t1", "ATTIO-TEST one", received="2026-10-20T07:00:00Z"),
            lead_msg("t2", "re: attio-test two", sender="omar@other.example", name="Omar", received="2026-10-20T08:00:00Z"),
            lead_msg("x1", "Real customer", sender="ben@maple.example", name="Ben", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({
            "ATTIO-TEST one": info(),
            "attio-test two": info(name="Omar", email="omar@other.example", company="Other Co"),
            "Real customer": info(name="Ben", email="ben@maple.example", company="Maple"),
        })
        return outlook, PretendAttio(), gemini

    def test_case40_only_matching_emails_are_handled(self):
        outlook, attio, gemini = self.only_subject_world()
        code, _ = self.live(outlook, attio, gemini, "--only-subject", "ATTIO-TEST")
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 0)
        self.assertEqual([outlook.where(i) for i in ("t1", "t2", "x1")], ["processed", "processed", "leads"])
        self.assertEqual(attio.person_ids("ben@maple.example"), [])
        self.assertNotIn("x1", json.dumps(self.state()))

    def test_case40_a_dry_run_shows_only_the_matching_emails(self):
        outlook, attio, gemini = self.only_subject_world()
        code, output = self.dry(outlook, attio, gemini, "--only-subject", "ATTIO-TEST")
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 0)
        self.assertNotIn("Real customer", output)
        self.assertEqual(attio.writes(), [])

    def test_case40_empty_text_is_refused_before_anything_is_read(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                outlook, attio, gemini = self.only_subject_world()
                code, output = self.live(outlook, attio, gemini, "--only-subject", text)
                self.assertEqual(code, 1)
                self.assertIn("must not be empty", output)
                self.assertEqual(outlook.requests, [])
                self.assertEqual(gemini.calls, [])
                self.assertEqual(attio.requests, [])

    # ---- Cases 41 to 44: the dry run --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def mixed_world(self):
        attio = PretendAttio()
        cid = attio.add_company("Brightwell Gyms", "brightwell.example")
        existing = attio.add_person("nora@brightwell.example", "Nora Fischer", "Operations Manager", cid)
        attio.add_deal("Old New deal", "New", existing, cid)
        won_person = attio.add_person("won@oldco.example", "Wendy Won", "CEO", None)
        attio.add_deal("Old won deal", "Won", won_person, None)
        half = attio.add_person("half@halfco.example", "Hal Half", None, None)
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "Brand new lead", sender="ana@fresh.example", name="Ana", received="2026-10-20T07:00:00Z"),
            lead_msg("m2", "Existing with new deal", received="2026-10-20T08:00:00Z"),
            lead_msg("m3", "Existing with won deal", sender="won@oldco.example", name="Wendy Won", received="2026-10-20T09:00:00Z"),
            lead_msg("m4", "Free mail lead", sender="rhys@gmail.com", name="Rhys", received="2026-10-20T10:00:00Z"),
            lead_msg("m5", "Person exists, company new", sender="half@halfco.example", name="Hal Half", received="2026-10-20T11:00:00Z"),
        ])
        gemini = PretendGemini({
            "Brand new lead": info(name="Ana", email="ana@fresh.example", company="Fresh Co"),
            "Existing with new deal": info(),
            "Existing with won deal": info(name="Wendy Won", email="won@oldco.example", company="Old Co"),
            "Free mail lead": info(name="Rhys", email="rhys@gmail.com", company="Rhys Studio"),
            "Person exists, company new": info(name="Hal Half", email="half@halfco.example", company="Half Co"),
        })
        return outlook, attio, gemini

    def test_case41_a_dry_run_prints_exactly_what_it_would_do(self):
        outlook, attio, gemini = self.mixed_world()
        before = copy.deepcopy((attio.people, attio.companies, attio.deals, attio.notes))
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        for phrase in ("would create person", "would create company", "would create deal",
                       "would add a note to the existing New deal", "person already exists",
                       "company already exists", "no company: free-mail domain",
                       "would move the email to Leads/Processed", "nothing to change on the person",
                       "would fill in empty fields on the person"):
            self.assertIn(phrase, output)
        self.assertEqual(before, (attio.people, attio.companies, attio.deals, attio.notes))
        self.assertEqual(attio.writes(), [])
        self.assertEqual(outlook.changes(), [])

    def test_case41_what_it_prints_matches_what_a_live_run_does(self):
        outlook, attio, gemini = self.mixed_world()
        _, dry_output = self.dry(outlook, attio, gemini)
        self.state_path.unlink(missing_ok=True)
        code, live_output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        labels_dry = ["People that would be created:", "Companies that would be created:",
                      "Deals that would be created:", "Notes that would be added to existing deals:"]
        labels_live = ["People created:", "Companies created:", "Deals created:",
                       "Notes added to existing deals:"]
        self.assertEqual(stat_numbers(dry_output, labels_dry), stat_numbers(live_output, labels_live))

    # ---- Case 42 ----

    def test_case42_a_dry_run_keeps_track_of_what_it_would_create(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "Second email", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"First email": info(), "Second email": info(summary="Following up.")})
        attio = PretendAttio()
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("would add a note to the deal an earlier email would create", output)
        self.assertEqual(output.count("would create deal"), 1)
        numbers = stat_numbers(output, ["People that would be created:", "Companies that would be created:",
                                        "Deals that would be created:"])
        self.assertEqual(numbers, [1, 1, 1])
        self.assertEqual(attio.counts(), (0, 0, 0, 0))

    def test_case42_the_same_email_listed_twice_is_shown_once(self):
        outlook, attio, gemini = self.one_lead()
        original = outlook.urlopen

        def twice(request, timeout=None):
            response = original(request, timeout)
            if request.get_method() == "GET" and request.full_url.split("?")[0].endswith("/leads-id/messages"):
                one = json.loads(response.read().decode())["value"]
                return FakeResponse(json.dumps({"value": one + one}).encode())
            return response

        outlook.urlopen = twice
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(len(gemini.calls), 0)
        self.assertEqual(output.count("would create deal"), 1)

    # ---- Case 43 ----

    def test_case43_a_dry_run_needs_the_attio_key_and_the_owner(self):
        # (no Gemini key is needed any more: see test_case35_no_gemini_key_prompt_or_model_is_needed)
        for name, expected in (("ATTIO_API_KEY", "I can't find ATTIO_API_KEY"),
                               ("ATTIO_DEAL_OWNER", "I can't find ATTIO_DEAL_OWNER")):
            with self.subTest(setting=name):
                outlook, attio, gemini = self.one_lead()
                code, output = self.dry(outlook, attio, gemini, remove=[name])
                self.assertEqual(code, 1)
                self.assertIn(expected, output)
                self.assertEqual(outlook.requests, [])
                self.assertEqual(attio.writes(), [])

    def test_case43_a_dry_run_stops_cleanly_when_attio_is_not_happy(self):
        outlook, attio, gemini = self.one_lead()
        code, output = self.dry(outlook, attio, gemini, env_changes={"ATTIO_API_KEY": "WRONG-ATTIO-KEY-SECRET"})
        self.assertEqual(code, 1)
        self.assertIn("Attio did not accept the key", output)
        self.assertNotIn("WRONG-ATTIO-KEY-SECRET", output)

        outlook, attio, gemini = self.one_lead()
        attio.fail("GET", "/v2/workspace_members", Raise(requests.exceptions.Timeout("slow")))
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("(the request timed out)", output)

        outlook, attio, gemini = self.one_lead()
        attio.members = {"someone.else@example.com": "member-9"}
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertIn("deal owner", output)
        self.assertEqual(attio.writes(), [])

    def test_case43_a_dry_run_says_yes_or_no_about_the_owner_and_nothing_more(self):
        outlook, attio, gemini = self.one_lead()
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Deal owner found in Attio: yes", output)
        self.assertNotIn(OWNER_ID, output)

    # ---- Case 44 ----

    def test_case44_a_dry_run_respects_the_state_file_and_the_maximum(self):
        messages = [lead_msg(f"m{i}", f"Email number {i}", sender=f"p{i}@co{i}.example", name=f"P{i}",
                             received=f"2026-10-20T0{i}:00:00Z") for i in range(5)]
        outlook = PretendOutlook(leads=messages)
        gemini = PretendGemini({f"Email number {i}": info(name=f"P{i}", email=f"p{i}@co{i}.example",
                                                           company=f"Co{i}") for i in range(5)})
        attio = PretendAttio()
        with mock.patch.object(leads_to_attio, "MAX_PER_RUN", 1):
            self.live(outlook, attio, gemini)                        # m0 done
            outlook.add_lead(lead_msg("m0", "Email number 0", sender="p0@co0.example", name="P0",
                                      received="2026-10-20T00:00:00Z"))   # moved back by hand
            before = self.state_path.read_bytes()
            calls = len(gemini.calls)
            code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Already handled earlier", output)
        self.assertEqual(output.count("would create deal"), 1)
        self.assertIn("3 still waiting", output)
        self.assertEqual(len(gemini.calls), calls)
        self.assertEqual(self.state_path.read_bytes(), before, "a dry run must not change the state file")

    # ---- Case 45: Outlook sign-in expired or Outlook unreachable ------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def test_case45_an_expired_sign_in_stops_cleanly(self):
        error = outlook_signin.SetupProblem("Your saved sign-in no longer works (reason: x). Please run outlook_signin.py again.")
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                code, output = self.run_main(outlook, attio, gemini, argv, token_error=error)
                self.assertEqual(code, 1)
                self.assertIn("outlook_signin.py", output)
                self.assertEqual(outlook.requests, [])
                self.assertEqual(attio.requests, [])
                self.assertEqual(gemini.calls, [])
                self.assertFalse(self.state_path.exists())

    def test_case45_outlook_unreachable_leaves_every_email_where_it_is(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                outlook, attio, gemini = self.one_lead()
                outlook.unreachable = True
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 1)
                self.assertIn("I couldn't reach Outlook", output)
                self.assertNotIn("Traceback", output)
                self.assertEqual(outlook.where("m1"), "leads")
                self.assertEqual(attio.writes(), [])
                self.assertEqual(gemini.calls, [])
                self.assertFalse(self.state_path.exists())

    def test_case45_outlook_going_away_part_way_stops_and_the_next_run_carries_on(self):
        outlook = PretendOutlook(leads=[
            lead_msg("m1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("m2", "Second email", sender="omar@other.example", name="Omar", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini({"First email": info(),
                                "Second email": info(name="Omar", email="omar@other.example", company="Other Co")})
        attio = PretendAttio()
        outlook.move_errors = {2: urllib.error.URLError("down")}      # the second move fails
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        self.assertEqual(outlook.where("m1"), "processed")
        self.assertEqual(outlook.where("m2"), "leads")
        outlook.move_errors = {}
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(outlook.where("m2"), "processed")
        self.assertEqual(attio.counts(), (2, 2, 2, 2))

    # ---- Case 46: a replay of everything, then check for duplicates -------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def test_case46_replay_all_situations_and_find_no_duplicates(self):
        attio = PretendAttio()
        cid = attio.add_company("Brightwell Gyms", "brightwell.example")
        existing = attio.add_person("exists@brightwell.example", "Eve Exists", "Manager", cid)
        attio.add_deal("Eve's open deal", "New", existing, cid)
        won_person = attio.add_person("won@oldco.example", "Wendy Won", "CEO", None)
        attio.add_deal("Wendy's old deal", "Won", won_person, None)
        attio.add_person("capital@brightwell.example", "Cap Italic", None, cid)

        batch = [
            ("r01", "Replay new person", "ana@fresh.example", info(name="Ana", email="ana@fresh.example", company="Fresh Co")),
            ("r02", "Replay same company", "bob@fresh.example", info(name="Bob", email="bob@fresh.example", company="Fresh Co")),
            ("r03", "Replay free mail", "rhys@gmail.com", info(name="Rhys", email="rhys@gmail.com", company="Rhys Studio")),
            ("r04", "Replay forwarded", "colleague@fresh.example", info(name="Rui", email="rui@santos.example", company="Santos Pilates")),
            ("r05", "Replay Dutch", "fleur@aan-zee.example", info(name="Fleur", email="fleur@aan-zee.example", company="Studio Aan Zee", summary="Asks for a demo.")),
            ("r06", "Replay accents", "zoe@muller.example", info(name="Zoë Müller", email="zoe@muller.example", company="Müller & Söhne")),
            ("r07", "Replay subdomain", "ana2@mail.maple.example", info(name="Ana Two", email="ana2@mail.maple.example", company="Maple")),
            ("r08", "Replay capitals", "capital@brightwell.example", info(name="Cap Italic", email="CAPITAL@Brightwell.Example", company="Brightwell Gyms")),
            ("r09", "Replay missing name", "noname@nameless.example", info(name=None, email="noname@nameless.example", company="Nameless Ltd")),
            ("r10", "Replay missing company", "jo@nocompany.example", info(name="Jo", email="jo@nocompany.example", company=None)),
            ("r11", "Replay existing new deal", "exists@brightwell.example", info(name="Eve Exists", email="exists@brightwell.example")),
            ("r12", "Replay existing won deal", "won@oldco.example", info(name="Wendy Won", email="won@oldco.example", company="Old Co")),
            ("r13", "Replay second from Ana", "ana@fresh.example", info(name="Ana", email="ana@fresh.example", company="Fresh Co", summary="Following up.")),
        ]
        outlook = PretendOutlook()
        replies = {}
        for i, (mid, subject, sender, reply) in enumerate(batch):
            outlook.add_lead(lead_msg(mid, subject, sender=sender, name="Sender",
                                      received=f"2026-10-20T{8 + i // 6:02d}:{(i * 7) % 60:02d}:00Z"))
            replies[subject] = reply
        gemini = PretendGemini(replies)

        with mock.patch.object(leads_to_attio, "MAX_PER_RUN", 50):
            # the same email listed twice
            original = outlook.urlopen

            def twice(request, timeout=None):
                response = original(request, timeout)
                if request.get_method() == "GET" and request.full_url.split("?")[0].endswith("/leads-id/messages"):
                    values = json.loads(response.read().decode())["value"]
                    return FakeResponse(json.dumps({"value": values + values[:2]}).encode())
                return response

            outlook.urlopen = twice
            code, _ = self.live(outlook, attio, gemini)
            self.assertEqual(code, 0)
            outlook.urlopen = original
            self.assertEqual([m for m in outlook.leads], [])
            self.live(outlook, attio, gemini)                       # a calm re-run changes nothing

            failures = [
                ("f1", "Replay company fails", "co@failco.example", "POST", "/v2/objects/companies/records"),
                ("f2", "Replay deal fails", "deal@failco2.example", "POST", "/v2/objects/deals/records"),
                ("f3", "Replay note fails", "note@failco3.example", "POST", "/v2/notes"),
            ]
            for mid, subject, sender, method, path_end in failures:
                replies_now = info(name=subject, email=sender, company=subject + " Ltd")
                gemini.replies[subject] = [replies_now]
                outlook.add_lead(lead_msg(mid, subject, sender=sender, name=subject))
                attio.fail(method, path_end, Status(503, "overloaded"))
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 1)
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)

            # the move fails, then a retry; and a move that fails followed by a lost state file
            for mid, subject, sender, lose_state in (("f4", "Replay move fails", "move@failco4.example", False),
                                                     ("f5", "Replay lost state", "lost@failco5.example", True)):
                gemini.replies[subject] = [info(name=subject, email=sender, company=subject + " Ltd")]
                outlook.add_lead(lead_msg(mid, subject, sender=sender, name=subject))
                outlook.move_errors = {len(outlook.moves) + 1: http_error(403)}
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 1)
                outlook.move_errors = {}
                if lose_state:
                    self.state_path.unlink()
                code, _ = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)

        # every email is finished and moved
        self.assertEqual(list(outlook.leads), [])
        # nothing exists twice
        emails = [p["email"] for p in attio.people.values()]
        self.assertEqual(len(emails), len(set(emails)), "a person exists twice")
        domains = [c["domain"] for c in attio.companies.values()]
        self.assertEqual(len(domains), len(set(domains)), "a company exists twice")
        deal_keys = [(tuple(d["people"]), d["name"], d["stage"]) for d in attio.deals.values()]
        self.assertEqual(len(deal_keys), len(set(deal_keys)), "a deal exists twice")
        note_keys = [(n["parent_record_id"], n["title"]) for n in attio.notes]
        self.assertEqual(len(note_keys), len(set(note_keys)), "a note exists twice")
        # and the work really got done
        self.assertEqual(len(attio.person_ids("ana@fresh.example")), 1)
        self.assertEqual(len(attio.person_ids("rui@santos.example")), 1)
        self.assertEqual(attio.person_ids("colleague@fresh.example"), [])
        self.assertEqual(len(attio.company_ids("maple.example")), 1)

    # ---- Default behaviour ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    # ---- Cases 47 to 53: system and security emails (the same rule as the triage) ----------------------------------------

    SYSTEM_SENDERS = ["noreply@example.com", "do-not-reply@something.com", "Security-Team@example.com",
                      "info@account.microsoft.com", "info@mail.google.com", "alerts@GITHUB.COM"]
    SYSTEM_SUBJECTS = ["Sign in to your account", "Your verification code", "Reset your password.",
                       "A security alert for you", "Your one-time code"]

    def assert_system_skipped(self, message, argv=("--live",)):
        """Run on one email that must be skipped, and check nothing happened to it."""
        self.state_path.unlink(missing_ok=True)
        outlook = PretendOutlook(leads=[message])
        attio = PretendAttio()
        gemini = PretendGemini()
        code, output = self.run_main(outlook, attio, gemini, list(argv))
        self.assertEqual(code, 0)
        self.assertEqual(gemini.calls, [], "a system email was sent to Gemini")
        self.assertEqual(attio.writes(), [])
        self.assertEqual(attio.counts(), (0, 0, 0, 0))
        self.assertEqual(outlook.where(message["id"]), "leads")
        self.assertEqual(outlook.changes(), [])
        self.assertFalse(self.state_path.exists())
        self.assertIn(f"Skipped (system or security email): {message['subject']}", output)
        self.assertIn("System or security emails skipped: 1", output)
        self.assertNotIn("Needs a person", output)
        return output

    def test_case47_system_senders_are_skipped(self):
        for sender in self.SYSTEM_SENDERS:
            with self.subTest(sender=sender):
                self.assert_system_skipped(lead_msg("m1", "Hello from us", sender=sender))

    def test_case47_security_subjects_are_skipped(self):
        for subject in self.SYSTEM_SUBJECTS:
            with self.subTest(subject=subject):
                self.assert_system_skipped(lead_msg("m1", subject))

    def test_case48_a_skipped_email_is_untouched_in_a_dry_run_too(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                self.assert_system_skipped(lead_msg("m1", "Sign in to your account"), argv=argv)

    def test_case48_a_skipped_email_is_skipped_again_on_the_next_run(self):
        message = lead_msg("m1", "Your verification code", sender="noreply@example.com")
        outlook = PretendOutlook(leads=[message])
        attio, gemini = PretendAttio(), PretendGemini()
        for _ in range(2):
            code, output = self.live(outlook, attio, gemini)
            self.assertEqual(code, 0)
            self.assertIn("Skipped (system or security email): Your verification code", output)
        self.assertEqual(gemini.calls, [])
        self.assertFalse(self.state_path.exists())

    def test_case49_each_skipped_email_is_reported_and_counted(self):
        for argv in (["--live"], []):
            with self.subTest(argv=argv):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook(leads=[
                    lead_msg("s1", "Your verification code", received="2026-10-20T07:00:00Z"),
                    lead_msg("s2", "Hello", sender="noreply@example.com", received="2026-10-20T08:00:00Z"),
                    lead_msg("n1", "Genuine enquiry", received="2026-10-20T09:00:00Z"),
                ])
                gemini = PretendGemini({"Genuine enquiry": info()})
                attio = PretendAttio()
                code, output = self.run_main(outlook, attio, gemini, argv)
                self.assertEqual(code, 0)
                self.assertIn("Skipped (system or security email): Your verification code", output)
                self.assertIn("Skipped (system or security email): Hello", output)
                self.assertIn("System or security emails skipped: 2", output)
                self.assertEqual(len(gemini.calls), 0)
                self.assertNotIn("Email: noreply@example.com", output)
                self.assertNotIn("Needs a person", output)
                self.assertEqual(outlook.where("s1"), "leads")
                self.assertEqual(outlook.where("s2"), "leads")

    def test_case50_real_leads_that_only_look_similar_are_not_skipped(self):
        cases = [
            lead_msg("m1", "Design information"),
            lead_msg("m1", "Pricing question", sender="hello@acme.example", name="Acme Security Ltd"),
            lead_msg("m1", "Hello from us", sender="info@notmicrosoft.com"),
        ]
        for message in cases:
            with self.subTest(subject=message["subject"], sender=message["from"]["emailAddress"]["address"]):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook(leads=[message])
                gemini = PretendGemini({message["subject"]: info()})
                attio = PretendAttio()
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(len(gemini.calls), 0)
                self.assertEqual(outlook.where("m1"), "processed")
                self.assertIn("System or security emails skipped: 0", output)
                self.assertNotIn("Skipped (system or security email)", output)

    def test_case51_triage_test_emails_are_exempt(self):
        for sender, subject in (("noreply@example.com", "TRIAGE-TEST hello"),
                                ("alerts@microsoft.com", "triage-test Security alert"),
                                ("nora@brightwell.example", "TRIAGE-TEST password reset")):
            with self.subTest(sender=sender, subject=subject):
                self.state_path.unlink(missing_ok=True)
                outlook = PretendOutlook(leads=[lead_msg("m1", subject, sender=sender)])
                gemini = PretendGemini({subject: info()})
                attio = PretendAttio()
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertEqual(len(gemini.calls), 0)
                self.assertIn("System or security emails skipped: 0", output)

    def test_case52_skipped_emails_do_not_use_up_the_maximum(self):
        skipped = [lead_msg(f"s{i}", f"Your password {i}", received=f"2026-10-20T0{i}:00:00Z") for i in range(3)]
        normal = [lead_msg(f"n{i}", f"Genuine enquiry {i}", sender=f"p{i}@co{i}.example", name=f"P{i}",
                           received=f"2026-10-20T1{i}:00:00Z") for i in range(2)]
        outlook = PretendOutlook(leads=skipped + normal)
        gemini = PretendGemini({f"Genuine enquiry {i}": info(name=f"P{i}", email=f"p{i}@co{i}.example",
                                                              company=f"Co{i}") for i in range(2)})
        attio = PretendAttio()
        with mock.patch.object(leads_to_attio, "MAX_PER_RUN", 2):
            code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual([outlook.where("n0"), outlook.where("n1")], ["processed", "processed"])
        self.assertNotIn("still waiting", output)
        self.assertIn("System or security emails skipped: 3", output)

    def test_case52_only_subject_still_applies_first(self):
        outlook = PretendOutlook(leads=[
            lead_msg("a", "Your invoice", sender="noreply@example.com", received="2026-10-20T07:00:00Z"),
            lead_msg("b", "Security alert", sender="noreply@example.com", received="2026-10-20T08:00:00Z"),
        ])
        gemini, attio = PretendGemini(), PretendAttio()
        code, output = self.live(outlook, attio, gemini, "--only-subject", "invoice")
        self.assertEqual(code, 0)
        self.assertIn("Skipped (system or security email): Your invoice", output)
        self.assertNotIn("Security alert", output)
        self.assertIn("System or security emails skipped: 1", output)
        self.assertEqual(gemini.calls, [])

    def test_case53_emails_handled_earlier_are_not_announced_again(self):
        outlook, attio, gemini = self.one_lead("Genuine enquiry", info())
        self.live(outlook, attio, gemini)
        outlook.add_lead(lead_msg("m1", "Genuine enquiry", sender="noreply@example.com"))   # back in Leads, same id
        calls = len(gemini.calls)
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Already handled earlier", output)
        self.assertNotIn("Skipped (system or security email)", output)
        self.assertIn("System or security emails skipped: 0", output)
        self.assertEqual(len(gemini.calls), calls)

    # ---- Cases 57 to 61 (replaced): the saved answer is what the Attio step uses --------------------------------
    # (tests/cases/11-single-call-pipeline.md, Part B: Cases A1 to A3, A6 to A8)

    def test_case57_the_saved_answer_is_read_from_the_email_and_gemini_is_never_called(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(gemini.calls, [])
        reads = [r for r in outlook.requests if "/extensions/" in r.full_url]
        self.assertEqual(len(reads), 1)
        self.assertTrue(reads[0].full_url.endswith("/me/messages/m1/extensions/" + EXTENSION_NAME))
        self.assertNotIn("?", reads[0].full_url)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case57_a_dry_run_uses_the_saved_answer_too(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        code, output = self.dry(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("would create deal", output)
        self.assertEqual(attio.writes(), [])
        self.assertEqual(gemini.calls, [])

    def test_case58_no_saved_answer_means_needs_a_person_and_is_not_asked_about_again(self):
        outlook = PretendOutlook(leads=[lead_msg("m1", "Old lead")])
        attio, gemini = PretendAttio(), PretendGemini()
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Needs a person", output)
        self.assertIn("no usable answer was saved on the email", output)
        self.assertEqual(attio.counts(), (0, 0, 0, 0))
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(self.state()["emails"]["m1"]["status"], "attention")
        reads_before = len([r for r in outlook.requests if "/extensions/" in r.full_url])
        code, output = self.live(outlook, attio, gemini)
        self.assertIn("Still needs a person", output)
        reads_after = len([r for r in outlook.requests if "/extensions/" in r.full_url])
        self.assertEqual(reads_after, reads_before, "a normal run does not look at it again")
        self.assertEqual(gemini.calls, [])

    def test_case58_unusable_saved_answers_need_a_person_with_the_reason_named(self):
        cases = [
            ("not a lead", info(is_lead=False), "the saved answer says it is not a lead"),
            ("needs review", info(request_type="complaint"), "the saved answer says it needs review"),
            ("damaged", "this is not json", "no usable answer was saved on the email"),
            ("no address", info(email="n/a"), "no usable email address"),
        ]
        for name, reply, reason in cases:
            with self.subTest(case=name):
                self.state_path.unlink(missing_ok=True)
                outlook, attio, gemini = self.one_lead("Demo please", reply)
                code, output = self.live(outlook, attio, gemini)
                self.assertEqual(code, 0)
                self.assertIn("Needs a person", output)
                self.assertIn(reason, output)
                self.assertEqual(attio.counts(), (0, 0, 0, 0))
                self.assertEqual(outlook.where("m1"), "leads")

    def test_case58_a_saved_answer_for_another_version_or_with_odd_fields_is_not_used(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        outlook.extensions["m1"] = saved_extension(json.dumps({
            "is_lead": True, "category": "Lead - Demo", "name": "Nora Fischer",
            "email": "nora@brightwell.example", "request_type": "demo"}), version=2)
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertIn("Needs a person", output)
        self.assertEqual(attio.counts(), (0, 0, 0, 0))

    def test_case59_the_triage_state_file_is_no_longer_read(self):
        self.assertFalse(hasattr(leads_to_attio, "load_triage_leads"))
        # a triage file that says "not a lead" changes nothing: the saved answer decides
        self.triage_state_path.write_text(json.dumps({"version": 1, "emails": {"x": {
            "outcome": "Not a lead", "stage": "done", "internet_message_id": "<m1@test.example>"}}}), encoding="utf-8")
        before = self.triage_state_path.read_bytes()
        outlook, attio, gemini = self.one_lead("Demo please", info())
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(self.triage_state_path.read_bytes(), before)

    def test_case59_the_leads_list_asks_only_for_what_is_needed_and_never_the_body(self):
        outlook, attio, gemini = self.one_lead()
        self.dry(outlook, attio, gemini)
        listing = [r for r in outlook.requests if r.full_url.split("?")[0].endswith("/leads-id/messages")]
        self.assertTrue(listing)
        for request in listing:
            select = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["$select"][0]
            fields = select.split(",")
            self.assertNotIn("body", fields)
            self.assertNotIn("bodyPreview", fields)
            self.assertIn("id", fields)
            self.assertIn("subject", fields)
            self.assertIn("from", fields)
            self.assertNotIn("outlook.body-content-type", " ".join(request.headers.values()))

    def test_case60_the_request_type_comes_from_the_saved_answer(self):
        outlook, attio, gemini = self.one_lead("Demo please", info(request_type="pricing"))
        self.live(outlook, attio, gemini)
        (deal,) = attio.deals.values()
        self.assertEqual(deal["name"], "Brightwell Gyms: pricing")

    def test_case60_a_missing_request_type_falls_back_to_the_saved_category(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        outlook.extensions["m1"] = saved_extension(json.dumps({
            "is_lead": True, "category": "Lead - Pricing", "name": "Nora Fischer",
            "email": "nora@brightwell.example", "company": "Brightwell Gyms", "role": None,
            "request_type": None, "urgency": 1, "summary": "Asks about prices.", "draft_reply": None}))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        (deal,) = attio.deals.values()
        self.assertEqual(deal["name"], "Brightwell Gyms: pricing")

    def test_case61_the_state_file_holds_no_lead_details(self):
        reply = info(name="NAME-MARKER-PERSON", email="marker.person@brightwell.example",
                     company="COMPANY-MARKER-CO", role="ROLE-MARKER-JOB", summary="SUMMARY-MARKER-TEXT")
        outlook, attio, gemini = self.one_lead("Demo please", reply)
        code, output = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        kept = self.saved_text()
        for marker in ("NAME-MARKER-PERSON", "marker.person@brightwell.example", "COMPANY-MARKER-CO",
                       "ROLE-MARKER-JOB", "SUMMARY-MARKER-TEXT"):
            self.assertNotIn(marker, kept)
        self.assertIn("SUMMARY-MARKER-TEXT", json.dumps(attio.notes))      # it did go to Attio

    def test_case61_a_failure_part_way_keeps_no_lead_details_either_and_the_retry_works(self):
        reply = info(name="NAME-MARKER-PERSON", summary="SUMMARY-MARKER-TEXT")
        outlook, attio, gemini = self.one_lead("Demo please", reply)
        attio.fail("POST", "/v2/objects/companies/records", Status(503, "overloaded"))
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 1)
        for marker in ("NAME-MARKER-PERSON", "SUMMARY-MARKER-TEXT"):
            self.assertNotIn(marker, self.saved_text())
        code, _ = self.live(outlook, attio, gemini)
        self.assertEqual(code, 0)
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(outlook.where("m1"), "processed")

    def test_case61_the_saved_answer_is_only_read_never_written_or_changed(self):
        outlook, attio, gemini = self.one_lead()
        self.live(outlook, attio, gemini)
        for request in outlook.changes():
            self.assertTrue(request.full_url.endswith("/move"), "the only change is the move to Processed")
        self.assertEqual(len(outlook.moves), 1)
        self.assertEqual(outlook.moves[0][1], {"destinationId": "processed-id"})

    def test_case61_only_reads_and_the_move_are_ever_sent_to_outlook(self):
        outlook, attio, gemini = self.one_lead()
        self.live(outlook, attio, gemini)
        for request in outlook.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/send", "/reply", "/forward", "/copy", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, path)
            self.assertIn(request.get_method(), ("GET", "POST"))
            if request.get_method() == "POST":
                self.assertTrue(path.endswith("/move"))

    # ---- Case 62 (replaced): --retry-attention looks at the saved answer again, without Gemini --------------------
    # (tests/cases/11-single-call-pipeline.md, Part B: Case A4)

    def mark_needs_a_person(self, outlook, attio, gemini):
        """Run once with no saved answer on the email, so it is marked as needing a person."""
        gemini.replies = {}
        outlook.extensions.clear()
        self.live(outlook, attio, gemini)
        self.assertEqual(self.state()["emails"]["m1"]["status"], "attention")

    def test_case62_a_normal_run_leaves_it_and_retry_attention_reads_the_saved_answer_again(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        self.mark_needs_a_person(outlook, attio, gemini)
        outlook.extensions["m1"] = saved_from_reply(info())        # the answer is on the email now

        code, output = self.live(outlook, attio, gemini)
        self.assertIn("Still needs a person", output)
        self.assertEqual(attio.counts(), (0, 0, 0, 0))

        code, output = self.live(outlook, attio, gemini, "--retry-attention")
        self.assertEqual(code, 0)
        self.assertNotIn("Gemini", output)
        self.assertEqual(outlook.where("m1"), "processed")
        self.assertEqual(attio.counts(), (1, 1, 1, 1))
        self.assertEqual(self.state()["emails"]["m1"]["status"], "done")
        self.assertEqual(gemini.calls, [])

    def test_case62_a_failed_retry_keeps_the_mark_with_the_new_reason(self):
        outlook, attio, gemini = self.one_lead("Demo please", info(is_lead=False))
        self.live(outlook, attio, gemini)
        self.assertIn("not a lead", self.state()["emails"]["m1"]["reason"])
        outlook.extensions["m1"] = saved_extension("this is not json")
        code, output = self.live(outlook, attio, gemini, "--retry-attention")
        self.assertEqual(code, 0)
        self.assertEqual(outlook.where("m1"), "leads")
        self.assertEqual(self.state()["emails"]["m1"]["status"], "attention")
        self.assertIn("usable", self.state()["emails"]["m1"]["reason"])

    def test_case62_it_never_touches_emails_that_are_already_done(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        self.live(outlook, attio, gemini)
        outlook.add_lead(lead_msg("m1", "Demo please"))               # back in Leads, same id
        counts = attio.counts()
        code, output = self.live(outlook, attio, gemini, "--retry-attention")
        self.assertEqual(code, 0)
        self.assertIn("Already handled earlier", output)
        self.assertEqual(attio.counts(), counts)

    def test_case62_it_works_with_the_filters(self):
        outlook = PretendOutlook(leads=[
            lead_msg("a1", "First email", received="2026-10-20T08:00:00Z"),
            lead_msg("a2", "Second email", sender="omar@other.example", name="Omar", received="2026-10-20T09:00:00Z"),
        ])
        gemini = PretendGemini()
        attio = PretendAttio()
        self.live(outlook, attio, gemini)                      # nothing saved: both marked "needs a person"
        outlook.extensions["a1"] = saved_from_reply(info())
        outlook.extensions["a2"] = saved_from_reply(info(name="Omar", email="omar@other.example", company="Other Co"))
        code, output = self.live(outlook, attio, gemini, "--retry-attention", "--only-subject", "First")
        self.assertEqual(code, 0)
        self.assertEqual([outlook.where("a1"), outlook.where("a2")], ["processed", "leads"])
        self.assertEqual(self.state()["emails"]["a2"]["status"], "attention")

    def test_case62_a_dry_run_retries_without_changing_any_state(self):
        outlook, attio, gemini = self.one_lead("Demo please", info())
        self.mark_needs_a_person(outlook, attio, gemini)
        outlook.extensions["m1"] = saved_from_reply(info())
        before = self.state_path.read_bytes()
        code, output = self.dry(outlook, attio, gemini, "--retry-attention")
        self.assertEqual(code, 0)
        self.assertIn("would create deal", output)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(attio.counts(), (0, 0, 0, 0))
        self.assertEqual(outlook.where("m1"), "leads")


if __name__ == "__main__":
    unittest.main(verbosity=2)
