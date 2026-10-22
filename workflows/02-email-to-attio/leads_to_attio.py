"""Send the emails in the Outlook Leads folder to Attio.

For each email in the Leads folder it reads the answer the triage saved on that email
(name, email, company, role, request type, urgency and summary; see saved_results.py). It never
asks Gemini and never reads the email's own text. An email with no usable saved answer is marked
"needs a person" and left in Leads. Then, in Attio, it:
  1. finds or creates the person (one person per email address),
  2. finds or creates the company (one company per email domain; free-mail
     domains such as gmail.com get no company),
  3. creates a deal "<company or person>: <request type>" at stage New with an
     owner, linked to the person and company, and adds a note. If the person
     already has a deal at stage New, it adds a note to that deal instead.
When all of that worked, it moves the email to Leads/Processed. If anything fails
the email stays in Leads and the next run carries on without duplicates.

Dry run by default: it reads the saved answers and looks things up in Attio (read only), then
prints exactly what it WOULD do. It writes nothing to Attio, moves no email and
does not touch the state file. Add --live to really do it.

Settings (in .env, never printed): ATTIO_API_KEY, ATTIO_DEAL_OWNER (the deal
owner's email address in Attio), plus the Outlook sign-in. No Gemini key is needed.
Progress is kept in leads-to-attio-state.json next to this script (git-ignored). It holds only
technical progress (ids, status, a short reference), never a lead's details.
It never deletes anything and never sends, replies to, forwards or copies an email. The only change
it asks Outlook for is moving a handled email to Leads/Processed.

Run:  python workflows\\02-email-to-attio\\leads_to_attio.py
      python workflows\\02-email-to-attio\\leads_to_attio.py --live
      python workflows\\02-email-to-attio\\leads_to_attio.py --live --only-subject "ATTIO-TEST"
      python workflows\\02-email-to-attio\\leads_to_attio.py --only-subject "Demo request" --only-from jo@example.com
(--only-subject and --only-from narrow the run; an email must match every filter given.
 --only-from needs the whole sender address, in any capitals.)
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
# The Outlook sign-in helpers live next door.
sys.path.insert(0, str(HERE.parent / "01-outlook-to-slack"))

import saved_results  # noqa: E402
import v3_reply  # noqa: E402
from extract_leads import read_env_value  # noqa: E402
from outlook_signin import SetupProblem as OutlookProblem  # noqa: E402
from outlook_signin import get_client_id, read_env_text  # noqa: E402
from outlook_to_slack import find_leads_folder, get_access_token  # noqa: E402
# The system and security email rule is shared with the triage, not copied.
from outlook_triage import OutlookRefused, graph, is_system_email, parse_time, read_graph, sender_of, subject_of  # noqa: E402

STATE_PATH = HERE / "leads-to-attio-state.json"
ATTIO_BASE = "https://api.attio.com"
ATTIO_TIMEOUT_SECONDS = 30
RETRY_WAIT_SECONDS = 2
MAX_PER_RUN = 10
MAX_PAGES = 10
PAGE_SIZE = 50
MAX_SUMMARY_CHARS = 2000
MAX_NAME_CHARS = 150
STAGE_NAME = "New"
REQUEST_TYPES = ("demo", "pricing", "partnership", "other")
# What the three lead categories mean here (used when the saved answer has no valid request type).
CATEGORY_TO_REQUEST = {"Lead - Demo": "demo", "Lead - Pricing": "pricing", "Lead - Other": "other"}
NO_ANSWER = "no usable answer was saved on the email"

FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "proton.me", "protonmail.com",
    # common country versions
    "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.it", "hotmail.es",
    "outlook.co.uk", "outlook.fr", "outlook.de", "outlook.it", "outlook.es",
    "live.co.uk", "live.fr", "live.nl", "live.de",
    "yahoo.co.uk", "yahoo.fr", "yahoo.de", "yahoo.it", "yahoo.es", "yahoo.ca",
    "yahoo.com.au", "yahoo.co.in", "yahoo.in",
})
# Endings with two parts, where the company name sits one label further left.
TWO_PART_ENDINGS = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz",
    "co.za", "com.br", "co.in", "co.jp", "com.sg", "com.mx",
})
EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AttioProblem(Exception):
    """A problem with Attio or the settings that we can explain in plain words."""


class AttioRejected(AttioProblem):
    """Attio refused a request. Callers may look at it; if nobody does, this plain message is shown."""

    def __init__(self, code, message=""):
        super().__init__(f"Attio refused the request (code {code}). Nothing else was changed for this email.")
        self.code = code
        self.message = message


PROBLEMS = (OutlookProblem, saved_results.SavedResultProblem, AttioProblem)


# ---- Small helpers --------------------------------------------------------------

def say(text=""):
    print(text)


def company_domain(email):
    """The company domain for an address: subdomains are dropped (mail.acme.co.uk -> acme.co.uk)."""
    domain = email.rsplit("@", 1)[-1].lower()
    labels = domain.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in TWO_PART_ENDINGS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else domain


def clean_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def shorten(text, limit, mark):
    """Cut text so that, with the mark added, it is never longer than the limit."""
    return text if len(text) <= limit else text[: limit - len(mark)].rstrip() + mark


def email_reference(message_id):
    """A short code for an email, used in notes so a retry can tell a note already exists."""
    return "ref-" + hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:8]


def first_value(record, slug, key):
    items = (record.get("values") or {}).get(slug) or []
    return items[0].get(key) if items else None


# ---- Attio ------------------------------------------------------------------------

class AttioClient:
    def __init__(self, key):
        self.key = key

    def call(self, method, path, body=None, params=None):
        """One request to Attio, with plain-words problems. Returns the decoded answer."""
        for attempt in (1, 2):
            try:
                response = requests.request(
                    method,
                    ATTIO_BASE + path,
                    headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                    json=body,
                    params=params,
                    timeout=ATTIO_TIMEOUT_SECONDS,
                )
            except requests.exceptions.Timeout:
                raise self.unreachable("the request timed out")
            except requests.exceptions.ConnectionError:
                raise self.unreachable("no connection")
            except requests.exceptions.RequestException:
                raise self.unreachable("a connection problem")

            code = response.status_code
            if code == 429:
                if attempt == 1:
                    time.sleep(RETRY_WAIT_SECONDS)
                    continue
                raise AttioProblem("Attio's request limit was reached, so I stopped. Please try again later.")
            break

        # The hint is always one of our own short phrases, never text from Attio's reply.
        if code == 401:
            raise AttioProblem(
                "Attio did not accept the key (code 401: the key is not valid). Check it and try again."
            )
        if code == 403:
            raise AttioProblem(
                "Attio did not accept the key (code 403: no permission). The key may be missing a "
                "permission this script needs. Check the key's permissions in Attio and try again."
            )
        if code >= 500:
            raise self.unreachable(f"Attio answered with code {code}")
        if code >= 400:
            try:
                message = str(response.json().get("message", ""))
            except (ValueError, AttributeError):
                message = ""
            raise AttioRejected(code, message)
        try:
            return response.json()
        except ValueError:
            return {}

    @staticmethod
    def unreachable(reason):
        return AttioProblem(f"I couldn't reach Attio ({reason}). Check your internet and try again.")

    # -- looking things up (read only) --
    def find_member(self, email):
        data = self.call("GET", "/v2/workspace_members")
        for member in data.get("data", []):
            if (member.get("email_address") or "").strip().lower() == email.strip().lower():
                return (member.get("id") or {}).get("workspace_member_id")
        return None

    def find_stage(self, wanted):
        data = self.call("GET", "/v2/objects/deals/attributes/stage/statuses")
        for status in data.get("data", []):
            title = status.get("title") or ""
            if title.strip().lower() == wanted.lower():
                return title
        return None

    def find_person(self, email):
        data = self.call("POST", "/v2/objects/people/records/query",
                         body={"filter": {"email_addresses": email}, "limit": 5})
        records = data.get("data", [])
        if not records:
            return None
        record = records[0]
        deals = [v.get("target_record_id") for v in (record.get("values") or {}).get("associated_deals") or []]
        return {
            "id": record["id"]["record_id"],
            "name": first_value(record, "name", "full_name"),
            "job_title": first_value(record, "job_title", "value"),
            "company": first_value(record, "company", "target_record_id"),
            "deals": [d for d in deals if d],
        }

    def find_company(self, domain):
        data = self.call("POST", "/v2/objects/companies/records/query",
                         body={"filter": {"domains": {"root_domain": {"$eq": domain}}}, "limit": 5})
        records = data.get("data", [])
        if not records:
            return None
        record = records[0]
        return {"id": record["id"]["record_id"], "name": first_value(record, "name", "value") or domain}

    def get_deal(self, deal_id):
        record = self.call("GET", f"/v2/objects/deals/records/{urllib.parse.quote(deal_id, safe='')}").get("data", {})
        status = first_value(record, "stage", "status")
        stage = status.get("title") if isinstance(status, dict) else status
        return {
            "id": deal_id,
            "name": first_value(record, "name", "value"),
            "stage": stage or "",
            "created_at": record.get("created_at") or "",
        }

    def note_exists(self, deal_id, reference):
        data = self.call("GET", "/v2/notes",
                         params={"parent_object": "deals", "parent_record_id": deal_id, "limit": 50})
        return any(f"[{reference}]" in (note.get("title") or "") for note in data.get("data", []))

    # -- changing things --
    def create_person(self, email, name, role):
        values = {"email_addresses": [email]}
        if name:
            values["name"] = name
        if role:
            values["job_title"] = role
        record = self.call("POST", "/v2/objects/people/records", body={"data": {"values": values}})["data"]
        return record["id"]["record_id"]

    def update_person(self, person_id, values):
        self.call("PATCH", f"/v2/objects/people/records/{urllib.parse.quote(person_id, safe='')}",
                  body={"data": {"values": values}})

    def create_company(self, name, domain):
        record = self.call("POST", "/v2/objects/companies/records",
                           body={"data": {"values": {"name": name, "domains": [domain]}}})["data"]
        return record["id"]["record_id"]

    def create_deal(self, name, stage, owner_id, person_id, company_id):
        values = {
            "name": name,
            "stage": [{"status": stage}],
            "owner": [{"referenced_actor_type": "workspace-member", "referenced_actor_id": owner_id}],
            "associated_people": [{"target_object": "people", "target_record_id": person_id}],
        }
        if company_id:
            values["associated_company"] = [{"target_object": "companies", "target_record_id": company_id}]
        try:
            record = self.call("POST", "/v2/objects/deals/records", body={"data": {"values": values}})["data"]
        except AttioRejected as rejected:
            text = rejected.message.lower()
            if "owner" in text or "workspace member" in text:
                raise AttioProblem(
                    "Attio did not accept the deal owner. Check the ATTIO_DEAL_OWNER setting."
                )
            raise
        return record["id"]["record_id"]

    def add_note(self, deal_id, title, content):
        self.call("POST", "/v2/notes", body={"data": {
            "parent_object": "deals", "parent_record_id": deal_id,
            "title": title, "format": "plaintext", "content": content,
        }})


# ---- Outlook ----------------------------------------------------------------------

def find_processed_folder(token, leads_id):
    data = read_graph(
        token, f"/me/mailFolders/{urllib.parse.quote(leads_id, safe='')}/childFolders",
        {"$top": "100", "$select": "id,displayName"},
    )
    for folder in data.get("value", []):
        if (folder.get("displayName") or "").strip().lower() == "processed":
            return folder["id"]
    raise OutlookProblem(
        "I couldn't find a folder called Processed inside Leads. "
        "Create it in Outlook (inside Leads) and try again."
    )


def list_leads(token, leads_id):
    """The emails directly in the Leads folder, oldest first."""
    params = {
        "$orderby": "receivedDateTime asc",
        "$select": "id,subject,from,receivedDateTime",
        "$top": str(PAGE_SIZE),
    }
    target = f"/me/mailFolders/{urllib.parse.quote(leads_id, safe='')}/messages"
    messages = []
    for _ in range(MAX_PAGES):
        data = read_graph(token, target, params)
        messages.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
        if not next_link or not next_link.startswith("https://graph.microsoft.com/"):
            break
        target, params = next_link, None
    return messages


def move_email(token, message, processed_id):
    try:
        graph(token, "POST", "/me/messages/" + urllib.parse.quote(message["id"], safe="") + "/move",
              body={"destinationId": processed_id})
    except OutlookRefused:
        raise OutlookProblem(
            "The lead was saved in Attio but the email could not be moved to Leads/Processed "
            "(Outlook refused). It stays in Leads, and the next run only tries the move again."
        )
    except OutlookProblem:
        raise OutlookProblem(
            "The lead was saved in Attio but the email could not be moved to Leads/Processed "
            "(Outlook could not be reached). It stays in Leads, and the next run only tries the move again."
        )


# ---- The state file -----------------------------------------------------------------

def fresh_state():
    return {"version": 1, "emails": {}}


def load_state(live):
    """Returns (state, note). A damaged file is set aside (live) and a fresh start is made."""
    if not STATE_PATH.exists():
        return fresh_state(), None
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("emails"), dict):
            raise ValueError("not a state file")
        return data, None
    except (OSError, ValueError):
        if live:
            aside = STATE_PATH.with_name(f"{STATE_PATH.name}.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
            os.replace(STATE_PATH, aside)
            return fresh_state(), (
                f"The state file looked damaged, so I set it aside as {aside.name} and started fresh. "
                "Attio is checked before anything is created, so nothing will be duplicated."
            )
        return fresh_state(), (
            "The state file looks damaged. A live run would set it aside and start fresh, "
            "checking Attio before creating anything."
        )


def save_state(state):
    temp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE_PATH)


# ---- Reading the saved answer -------------------------------------------------------------

def read_lead(answer):
    """Turn the answer saved on the email into (lead, reason). lead is None when a person must look."""
    category = answer.get("category")
    if category == v3_reply.NEEDS_REVIEW:
        return None, "the saved answer says it needs review"
    if answer.get("is_lead") is not True or category not in v3_reply.LEAD_CATEGORIES:
        return None, "the saved answer says it is not a lead"
    email = clean_text(answer.get("email"))
    email = email.lower() if email else None
    if not email or not EMAIL_SHAPE.match(email):
        return None, "no usable email address"
    request_type = clean_text(answer.get("request_type"))
    request_type = request_type.lower() if request_type else None
    if request_type not in REQUEST_TYPES:
        request_type = CATEGORY_TO_REQUEST[category]
    urgency = answer.get("urgency")
    return {
        "name": clean_text(answer.get("name")),
        "email": email,
        "company": clean_text(answer.get("company")),
        "role": clean_text(answer.get("role")),
        "request_type": request_type,
        "urgency": urgency if isinstance(urgency, int) and not isinstance(urgency, bool) else None,
        "summary": clean_text(answer.get("summary")) or "(no summary)",
    }, None


def lead_of(token, message_id):
    """Read the saved answer on an email. Returns (lead, reason); a problem with Outlook is raised."""
    answer = saved_results.load_result(token, message_id)
    if answer is None:
        return None, NO_ANSWER
    return read_lead(answer)


# ---- Doing the work (live) or planning it (dry run) ----------------------------------------

class Worker:
    def __init__(self, client, token, owner_id, stage, processed_id, state, live):
        self.client = client
        self.token = token
        self.owner_id = owner_id
        self.stage = stage
        self.processed_id = processed_id
        self.state = state
        self.live = live
        self.counts = {"people": 0, "companies": 0, "deals": 0, "notes_existing": 0}
        # A dry run keeps track of what it would create, so later emails see it.
        self.plan_people = {}
        self.plan_companies = {}
        self.plan_deals = {}          # deal id -> deal
        self.plan_person_deals = {}   # person id -> [deal ids]
        self.plan_counter = 0

    # -- wording --
    def verb(self, live_text, dry_text):
        return live_text if self.live else dry_text

    def save(self):
        if self.live:
            save_state(self.state)

    def planned_id(self, kind):
        self.plan_counter += 1
        return f"planned-{kind}-{self.plan_counter}"

    # -- the steps --
    def step_person(self, entry, lead):
        """Find or create the person. Returns the person's record (a dict with an id)."""
        email = lead["email"]
        found = self.plan_people.get(email) or self.client.find_person(email)
        if found:
            if not entry.get("person_id"):
                entry["person_id"] = found["id"]
                entry["person_new"] = False
            say(f"  person already exists: {email}" if not self.live else f"  found person: {email}")
            fill = {}
            if lead["name"] and not found["name"]:
                fill["name"] = lead["name"]
            if lead["role"] and not found["job_title"]:
                fill["job_title"] = lead["role"]
            if fill:
                names = ", ".join("role" if key == "job_title" else key for key in fill)
                if self.live:
                    self.client.update_person(found["id"], fill)
                    say(f"  filled in empty fields on the person: {names}")
                else:
                    found = dict(found, **{k: v for k, v in fill.items()})
                    self.plan_people[email] = found
                    say(f"  would fill in empty fields on the person: {names}")
            else:
                say("  nothing to change on the person")
            self.save()
            return found

        label = lead["name"] or email
        try:
            person_id = self.client.create_person(email, lead["name"], lead["role"]) if self.live \
                else self.planned_id("person")
        except AttioRejected:
            again = self.client.find_person(email)      # someone else may have just created it
            if not again:
                raise AttioProblem("Attio refused to create the person. Nothing else was changed for this email.")
            person_id = again["id"]
            entry["person_id"], entry["person_new"] = person_id, False
            say(f"  found person: {email}")
            self.save()
            return again
        entry["person_id"], entry["person_new"] = person_id, True
        self.counts["people"] += 1
        say(f"  {self.verb('created', 'would create')} person: {label} ({email})")
        record = {"id": person_id, "name": lead["name"], "job_title": lead["role"], "company": None, "deals": []}
        if not self.live:
            self.plan_people[email] = record
        self.save()
        return record

    def step_company(self, entry, lead, person):
        """Find or create the company for the email domain. Free-mail gets none."""
        domain = company_domain(lead["email"])
        if domain in FREE_MAIL_DOMAINS:
            entry["company_id"] = None
            say("  no company: free-mail domain")
            return None
        found = self.plan_companies.get(domain) or self.client.find_company(domain)
        if found:
            say(f"  company already exists: {domain}")
            company = found
        else:
            name = shorten(lead["company"] or domain, MAX_NAME_CHARS, "...")
            try:
                company_id = self.client.create_company(name, domain) if self.live else self.planned_id("company")
            except AttioRejected:
                again = self.client.find_company(domain)
                if not again:
                    raise AttioProblem("Attio refused to create the company. Nothing else was changed for this email.")
                say(f"  company already exists: {domain}")
                company = again
            else:
                self.counts["companies"] += 1
                say(f"  {self.verb('created', 'would create')} company: {name} ({domain})")
                company = {"id": company_id, "name": name}
            if not self.live:
                self.plan_companies[domain] = company
        entry["company_id"] = company["id"]
        if entry.get("person_new") and not entry.get("person_linked") and self.live:
            self.client.update_person(entry["person_id"], {"company": [
                {"target_object": "companies", "target_record_id": company["id"]}]})
            entry["person_linked"] = True
        self.save()
        return company

    def new_deal_of(self, person):
        """The person's newest deal at stage New, or None."""
        deal_ids = list(person.get("deals", [])) + self.plan_person_deals.get(person["id"], [])
        newest = None
        for deal_id in deal_ids:
            deal = self.plan_deals.get(deal_id) or self.client.get_deal(deal_id)
            if deal["stage"].strip().lower() == STAGE_NAME.lower():
                if newest is None or deal["created_at"] >= newest["created_at"]:
                    newest = deal
        return newest

    def step_deal(self, entry, lead, person, company):
        """Find the person's New deal, or create one. Returns the deal id."""
        if entry.get("deal_id") and self.live:
            return entry["deal_id"]
        fresh = self.client.find_person(lead["email"]) if self.live and not person["id"].startswith("planned-") else person
        lookup = fresh or person
        existing = self.new_deal_of(lookup)
        if existing:
            entry["deal_id"], entry["deal_created"] = existing["id"], False
            planned_here = existing["id"] in self.plan_deals
            text = (
                "the deal an earlier email would create" if planned_here else "the existing New deal"
            )
            say(f"  {self.verb('adding', 'would add')} a note to {text}: {existing['name']}")
            self.save()
            return existing["id"]
        label = (company or {}).get("name") or lead["name"] or lead["email"]
        name = shorten(f"{label}: {lead['request_type']}", MAX_NAME_CHARS, "...")
        if self.live:
            deal_id = self.client.create_deal(name, self.stage, self.owner_id, person["id"], (company or {}).get("id"))
        else:
            deal_id = self.planned_id("deal")
            self.plan_deals[deal_id] = {"id": deal_id, "name": name, "stage": self.stage, "created_at": "~"}
            self.plan_person_deals.setdefault(person["id"], []).append(deal_id)
        entry["deal_id"], entry["deal_created"] = deal_id, True
        self.counts["deals"] += 1
        say(f"  {self.verb('created', 'would create')} deal: {name} (stage {self.stage}, owner set)")
        self.save()
        return deal_id

    def note_text(self, entry, lead, message):
        reference = entry["ref"]
        subject = subject_of(message)
        received = (message.get("receivedDateTime") or "")[:10] or "unknown"
        lines = [
            f"Summary: {shorten(lead['summary'], MAX_SUMMARY_CHARS, '... (shortened)')}",
            f"Request type: {lead['request_type']}",
            f"Urgency: {lead['urgency'] if lead['urgency'] is not None else 'not stated'}",
            f"Subject: {subject}",
            f"Received: {received}",
        ]
        if entry.get("company_id") is None:
            lines.append(f"Company named in the email: {lead['company'] or 'not stated'}")
        return f"Lead email: {shorten(subject, 100, '...')} [{reference}]", "\n".join(lines)

    def step_note(self, entry, lead, message, deal_id):
        if entry.get("note_done") and self.live:
            return
        title, content = self.note_text(entry, lead, message)
        if self.live:
            if self.client.note_exists(deal_id, entry["ref"]):
                say("  the note is already there")
            else:
                self.client.add_note(deal_id, title, content)
                say("  added a note to the new deal" if entry.get("deal_created") else "  added the note")
            entry["note_done"] = True
        else:
            if entry.get("deal_created"):
                say("  would add a note to the new deal")
        if not entry.get("deal_created"):
            self.counts["notes_existing"] += 1
        self.save()

    def finish(self, message, entry):
        if self.live:
            move_email(self.token, message, self.processed_id)
            entry["status"] = "done"
            save_state(self.state)
            say("  moved the email to Leads/Processed")
        else:
            say("  would move the email to Leads/Processed")

    def handle(self, message, lead, entry):
        person = self.step_person(entry, lead)
        company = self.step_company(entry, lead, person)
        deal_id = self.step_deal(entry, lead, person, company)
        self.step_note(entry, lead, message, deal_id)
        self.finish(message, entry)


# ---- The run -----------------------------------------------------------------------------------

def check_settings():
    attio_key = read_env_value("ATTIO_API_KEY")
    if not attio_key:
        raise AttioProblem("I can't find ATTIO_API_KEY. Add it to your .env file and try again.")
    owner = read_env_value("ATTIO_DEAL_OWNER")
    if not owner:
        raise AttioProblem(
            "I can't find ATTIO_DEAL_OWNER. Add the deal owner's email address (as in Attio) "
            "to your .env file and try again."
        )
    return attio_key, owner


def run(live, only_subject, only_from=None, retry_attention=False):
    if only_subject is not None and not only_subject.strip():
        raise AttioProblem(
            "The --only-subject text must not be empty, because empty text would match every email."
        )
    if only_from is not None and not only_from.strip():
        raise AttioProblem(
            "The --only-from address must not be empty, because empty text would match every email."
        )
    attio_key, owner_email = check_settings()
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    token = get_access_token(env_text, client_id)

    leads_id = find_leads_folder(token)
    processed_id = find_processed_folder(token, leads_id)

    client = AttioClient(attio_key)
    owner_id = client.find_member(owner_email)
    if not owner_id:
        raise AttioProblem("I couldn't find the deal owner in Attio. Check the ATTIO_DEAL_OWNER setting.")
    stage = client.find_stage(STAGE_NAME)
    if not stage:
        raise AttioProblem(
            f"I couldn't find the stage {STAGE_NAME} in the Attio Deals list. Check the stage names in Attio."
        )
    say("Dry run: nothing will be written to Attio or Outlook." if not live else "LIVE: updating Attio and moving emails.")
    say("Deal owner found in Attio: yes")

    state, note = load_state(live)
    if note:
        say(note)

    needle = only_subject.strip().lower() if only_subject is not None else None
    wanted_from = only_from.strip().lower() if only_from is not None else None
    messages = list_leads(token, leads_id)
    messages.sort(key=lambda m: (parse_time(m.get("receivedDateTime")) or datetime.datetime.min.replace(
        tzinfo=datetime.timezone.utc), m.get("id", "")))

    waiting_list, system, left, seen = [], [], 0, set()
    for message in messages:
        message_id = message.get("id")
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        if needle is not None and needle not in (message.get("subject") or "").lower():
            continue
        # The sender must be exactly the address given (the whole address, not a part of it).
        if wanted_from is not None and (sender_of(message)[0] or "").strip().lower() != wanted_from:
            continue
        entry = state["emails"].get(message_id) or {}
        if entry.get("status") == "done":
            say(f"Already handled earlier, left alone: {subject_of(message)}")
            left += 1
        elif entry.get("status") == "attention" and not retry_attention:
            say(f"Still needs a person: {subject_of(message)} ({entry.get('reason', 'see earlier note')})")
            left += 1
        elif is_system_email(message):
            system.append(message)      # judged by sender and subject only
        else:
            waiting_list.append(message)

    # System and security emails are reported and left completely untouched in Leads.
    for message in system:
        say(f"Skipped (system or security email): {subject_of(message)}")

    if not waiting_list:
        if (needle is not None or wanted_from is not None) and not left and not system:
            if needle is not None and wanted_from is not None:
                say(f'No lead with "{only_subject.strip()}" in the subject from "{only_from.strip()}" '
                    "was found. Nothing was changed.")
            elif needle is not None:
                say(f'No lead with "{only_subject.strip()}" in the subject was found. Nothing was changed.')
            else:
                say(f'No lead from "{only_from.strip()}" was found. Nothing was changed.')
        else:
            say("No leads to handle.")
        say(f"System or security emails skipped: {len(system)}")
        return 0

    waiting = max(0, len(waiting_list) - MAX_PER_RUN)
    batch = waiting_list[:MAX_PER_RUN]
    worker = Worker(client, token, owner_id, stage, processed_id, state, live)
    handled = 0
    try:
        for message in batch:
            address, _ = sender_of(message)
            say(f"Email: {address}, {subject_of(message)}")
            message_id = message["id"]
            entry = state["emails"].get(message_id) or {}
            # The saved answer is read from the email every time; it is never copied into the state file.
            lead, reason = lead_of(token, message_id)
            if lead is None:
                say(f"  Needs a person to look at it: {subject_of(message)} ({reason})")
                if live:
                    state["emails"][message_id] = {"status": "attention", "reason": reason}
                    save_state(state)
                left += 1
                continue
            if entry.get("status") == "in_progress":
                entry["ref"] = entry.get("ref") or email_reference(message_id)     # carry on where it stopped
            else:
                entry = {"status": "in_progress", "ref": email_reference(message_id)}
                if live:
                    state["emails"][message_id] = entry
                    save_state(state)
            if not live:
                entry = {"ref": email_reference(message_id)}
            worker.handle(message, lead, entry)
            handled += 1
    except PROBLEMS as problem:
        say(str(problem))
        say(f"Stopped. {handled} lead(s) were finished before this. The email being handled stays in Leads.")
        return 1

    counts = worker.counts
    say()
    if live:
        say(f"Leads handled: {handled}")
        say(f"People created: {counts['people']}")
        say(f"Companies created: {counts['companies']}")
        say(f"Deals created: {counts['deals']}")
        say(f"Notes added to existing deals: {counts['notes_existing']}")
    else:
        say(f"Dry run finished: {handled} lead(s) would be handled. Add --live to do it for real.")
        say(f"People that would be created: {counts['people']}")
        say(f"Companies that would be created: {counts['companies']}")
        say(f"Deals that would be created: {counts['deals']}")
        say(f"Notes that would be added to existing deals: {counts['notes_existing']}")
    say(f"System or security emails skipped: {len(system)}")
    say(f"Left in Leads: {left}")
    if waiting:
        say(f"{waiting} still waiting for the next run.")
    return 0


def main():
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Send the Outlook Leads folder to Attio.")
    parser.add_argument("--live", action="store_true", help="really write to Attio and move emails (default is a dry run)")
    parser.add_argument("--only-subject", metavar="TEXT", help="only handle emails whose subject contains TEXT")
    parser.add_argument("--only-from", metavar="ADDRESS",
                        help="only handle emails from exactly this sender address (use with --only-subject to narrow further)")
    parser.add_argument("--retry-attention", action="store_true",
                        help="read the saved answer again for emails marked as needing a person")
    args = parser.parse_args()
    try:
        return run(args.live, args.only_subject, args.only_from, args.retry_attention)
    except PROBLEMS as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(
            f"Something unexpected went wrong ({type(unexpected).__name__}). "
            "The state file shows which emails were already handled."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
