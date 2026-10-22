"""Triage new Outlook Inbox emails with Gemini and write the answer back.

For each new email in the Inbox it asks Gemini ONE question (prompt v3 by default, model from
config.json). The answer holds everything: whether it is a lead, the category, the lead's details
and a short draft reply. Then, in Outlook, it adds one category:
  Lead - Demo, Lead - Pricing, Lead - Other, Not a lead or Needs review.
The category is checked against the request type, never just trusted (see v3_reply.py).
For a real lead it also saves the answer on the email itself (a small hidden extension, see
saved_results.py) before moving the email into the Leads folder, so the Attio step can read it there
and never needs to ask Gemini again. With --slack test or --slack live it also posts one short message
to Slack for the lead (see lead_slack.py), once, before the category is added and the email is moved.
Slack is off by default. With --drafts on it then makes one reply draft in the Drafts folder for the
lead (see reply_drafts.py), addressed only to the sender, once, before the category is added and the
email is moved; drafts are off by default and a draft is never sent. A lead that should get no draft
(for example a forwarded enquiry) carries on, and the output says plainly why. Not a lead and Needs
review emails stay in the Inbox and get nothing saved. Handled emails are remembered in a small state file (triage-state.json, next to this
script, git-ignored) so nothing is handled twice. The state file holds no lead details: only Outlook IDs, the
category, the stage, a date and each email's short ID (never a sender, name, subject or Message-ID header).

Dry run by default: it only lists the emails it would send to Gemini and changes
nothing. Add --live to write to Outlook. It never sends, replies to everyone, forwards,
copies or deletes anything. The only changes it asks Outlook for are adding a
category, saving the answer (and the small once-only markers) on a lead's email, making
the reply draft (only with --drafts on), and moving a lead to the Leads folder.
No token or key is ever printed, and no lead detail or draft text is printed or kept in a file.
Nothing it prints names a sender, a person, a subject or any email text: an email is named by a short
ID (see short_id), and the rest of the output is counts, categories and plain steps. So the scheduler's
log holds none of that either. (The Slack post, the reply draft and the saved answer do hold the lead's
details, because that is what they are for.)

With --stateless it keeps no state file at all (none is read, none is written): the mailbox is the memory, through
the categories on the emails, the hidden markers on them and the Leads folder. A lead that was categorised but could
not be moved is recognised by its category and just moved to Leads. This is how it runs on GitHub Actions, where each
run starts on a fresh machine. System and security emails are only counted, never listed.

Run:  python workflows\\02-email-to-attio\\outlook_triage.py
      python workflows\\02-email-to-attio\\outlook_triage.py --only-subject "TRIAGE-TEST"
      python workflows\\02-email-to-attio\\outlook_triage.py --live --only-subject "TRIAGE-TEST"
      python workflows\\02-email-to-attio\\outlook_triage.py --live --slack test
      python workflows\\02-email-to-attio\\outlook_triage.py --live --drafts on --only-subject "TRIAGE-TEST"
Needs: MS_CLIENT_ID and MS_TOKEN_CACHE (run outlook_signin.py once first), the msal
       library, and for --live also GEMINI_API_KEY. --slack test needs SLACK_TEST_WEBHOOK_URL and
       --slack live needs SLACK_WEBHOOK_URL (both in .env, never printed).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The Outlook sign-in helpers live next door.
sys.path.insert(0, str(HERE.parent / "01-outlook-to-slack"))

import extract_leads  # noqa: E402
import lead_slack  # noqa: E402
import reply_drafts  # noqa: E402
import saved_results  # noqa: E402
import v3_reply  # noqa: E402
from extract_leads import ask_gemini, build_message, get_api_key, load_model, load_prompt, parse_reply  # noqa: E402
from outlook_signin import SetupProblem as SignInProblem  # noqa: E402
from outlook_signin import get_client_id, read_env_text  # noqa: E402
from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS, find_leads_folder, get_access_token  # noqa: E402

STATE_PATH = HERE / "triage-state.json"
STATELESS = False       # set by each run from --stateless: no state file is read or written, the mailbox is the memory
LOOKBACK_DAYS = 7
MAX_PER_RUN = 25
MAX_PAGES = 10
PAGE_SIZE = 50
MAX_BODY_CHARS = 4000
DEFAULT_PROMPT = "v3"

OUR_CATEGORIES = v3_reply.CATEGORIES

# System and security emails are never sent to Gemini and are left untouched.
# They are spotted from the sender address and the subject only, never the body
# and never the sender's display name.
#   - sender words: looked for only in the part of the address before the @
#   - domains: must match exactly or as a subdomain, never as part of a longer name
#   - subject phrases: must match as whole words, so "Design information" is fine
SYSTEM_SENDER_WORDS = ("noreply", "no-reply", "do-not-reply", "donotreply", "accountprotection", "security")
SYSTEM_DOMAINS = ("microsoft.com", "microsoftonline.com", "anthropic.com", "slack.com", "github.com", "google.com")
SYSTEM_SUBJECT_PHRASES = (
    "verification code", "confirmation code", "one-time", "secure link", "sign in",
    "sign-in", "password", "security alert", "new app connected",
)
SYSTEM_SUBJECT_PATTERNS = tuple(
    re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])") for phrase in SYSTEM_SUBJECT_PHRASES
)
EXEMPT_SUBJECT = "triage-test"   # our own test emails are never treated as system emails

PROBLEMS = (SignInProblem, extract_leads.SetupProblem, saved_results.SavedResultProblem,
            lead_slack.LeadSlackProblem, reply_drafts.ReplyDraftProblem)

# --slack off|test|live: which Slack link (named in .env) is used. The links themselves are never printed.
SLACK_MODES = ("off", "test", "live")
SLACK_SETTINGS = {"test": "SLACK_TEST_WEBHOOK_URL", "live": "SLACK_WEBHOOK_URL"}

# --drafts off|on: whether each lead also gets one reply draft in Outlook's Drafts folder (see reply_drafts.py).
DRAFT_MODES = ("off", "on")


class OutlookRefused(Exception):
    """Outlook answered with an error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def slack_link(mode):
    """The Slack link for the chosen mode, or None when Slack is off. Stops, naming only the setting, if it is missing."""
    if mode == "off":
        return None
    name = SLACK_SETTINGS[mode]
    link = extract_leads.read_env_value(name)
    if not link:
        raise lead_slack.LeadSlackProblem(
            f"I can't find {name}, which --slack {mode} needs. "
            "Add it to your .env file, or run with --slack off. Nothing was changed."
        )
    return link


# ---- Talking to Outlook -----------------------------------------------------

def graph(token, method, target, params=None, body=None, text_body=False):
    """Talk to Outlook. Only GET, PATCH (categories) and POST (move) are ever used."""
    url = target if target.startswith("http") else GRAPH_URL + target
    if params:
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    headers = {"Authorization": f"Bearer {token}"}
    if text_body:
        headers["Prefer"] = 'outlook.body-content-type="text"'
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raise OutlookRefused(error.code)
    except (urllib.error.URLError, OSError):
        raise SignInProblem("I couldn't reach Outlook. Check the internet connection.")


def read_graph(token, target, params=None, text_body=False):
    try:
        return graph(token, "GET", target, params, text_body=text_body)
    except OutlookRefused as refused:
        if refused.code in (401, 403):
            raise SignInProblem("Outlook didn't accept the sign-in. Please run outlook_signin.py again.")
        raise SignInProblem(f"Outlook refused the request (code {refused.code}).")


def list_inbox(token, since):
    """Inbox emails received since the given time, oldest first (several pages if needed)."""
    params = {
        "$filter": "receivedDateTime ge " + since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$orderby": "receivedDateTime asc",
        "$select": "id,subject,from,replyTo,conversationId,receivedDateTime,categories,body,internetMessageId",
        "$top": str(PAGE_SIZE),
    }
    target = "/me/mailFolders/inbox/messages"
    messages = []
    for _ in range(MAX_PAGES):
        data = read_graph(token, target, params, text_body=True)
        messages.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
        # Only ever follow a link that stays on Outlook's own address.
        if not next_link or not next_link.startswith(GRAPH_URL + "/"):
            break
        target, params = next_link, None
    return messages


def message_path(message_id):
    return "/me/messages/" + urllib.parse.quote(message_id, safe="")


def add_category(token, message, category):
    categories = list(message.get("categories") or []) + [category]
    try:
        graph(token, "PATCH", message_path(message["id"]), body={"categories": categories})
    except OutlookRefused:
        raise SignInProblem(
            "Outlook refused the change. Your sign-in may need redoing: "
            "run outlook_signin.py again. That email was left as it was."
        )


def move_to_leads(token, message, leads_id, label):
    try:
        graph(token, "POST", message_path(message["id"]) + "/move", body={"destinationId": leads_id})
    except OutlookRefused:
        raise SignInProblem(
            f"The email {label} was categorised but could not be moved to Leads (Outlook refused). "
            "Next run tries the move again."
        )


# ---- Reading emails and Gemini's answer -------------------------------------

def sender_of(message):
    address = ((message.get("from") or {}).get("emailAddress")) or {}
    return address.get("address") or address.get("name") or "(unknown sender)", address.get("name") or ""


def short_id(message):
    """A short code that names an email in the output without saying anything about it, such as e-1a2b3c.

    It is made from the email's own Message-ID header, which stays the same when the email is moved (Outlook's
    own ID changes on a move), or from Outlook's ID if there is no header. It cannot be turned back into the
    sender or the subject, so it is safe to print and to keep in a log. The triage and the Attio step use the
    same code, so one email is named the same way in both.
    """
    return short_code(message.get("internetMessageId") or message.get("id") or "")


def short_code(basis):
    """The short ID for a Message-ID header (or an Outlook ID): "e-" and six letters or digits."""
    return "e-" + hashlib.sha1(str(basis).encode("utf-8")).hexdigest()[:6]


def one_line(text):
    return " ".join((text or "").split())


def subject_of(message):
    return one_line(message.get("subject")) or "(no subject)"


def email_text(message):
    address, name = sender_of(message)
    from_line = f"{name} <{address}>" if name and name != address else address
    body = ((message.get("body") or {}).get("content")) or ""
    return f"From: {from_line}\nSubject: {subject_of(message)}\nBody:\n{body[:MAX_BODY_CHARS]}"


def has_our_category(message):
    ours = {name.lower() for name in OUR_CATEGORIES}
    return any((c or "").strip().lower() in ours for c in (message.get("categories") or []))


LEAD_CATEGORY_NAMES = {name.lower(): name for name in v3_reply.LEAD_CATEGORIES}


def lead_category_of(message):
    """The lead category already on an email (Lead - Demo, Lead - Pricing or Lead - Other), or None.

    Capitals and extra spaces are ignored. An email in the Inbox that carries one is a lead whose move to Leads
    did not happen, so it only needs moving: the mailbox itself says so, no state file is needed.
    """
    for name in message.get("categories") or []:
        found = LEAD_CATEGORY_NAMES.get(" ".join(str(name or "").split()).lower())
        if found:
            return found
    return None


def is_system_email(message):
    """True for system or security emails, judged by sender address and subject only.

    The body and the sender's display name are never used.
    """
    subject = (message.get("subject") or "").lower()
    if EXEMPT_SUBJECT in subject:
        return False
    sender = ((message.get("from") or {}).get("emailAddress")) or {}
    address = (sender.get("address") or "").lower()
    local_part, at_sign, domain = address.rpartition("@")
    if not at_sign:
        local_part, domain = "", ""   # no usable address: only the subject can decide
    if any(word in local_part for word in SYSTEM_SENDER_WORDS):
        return True
    if any(domain == d or domain.endswith("." + d) for d in SYSTEM_DOMAINS):
        return True
    return any(pattern.search(subject) for pattern in SYSTEM_SUBJECT_PATTERNS)


def parse_time(text):
    try:
        return datetime.datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None


# ---- The state file ---------------------------------------------------------

def fresh_state():
    return {"version": 1, "emails": {}}


def start_note():
    return f"the last {LOOKBACK_DAYS} days, at most {MAX_PER_RUN} per run"


def load_state(live):
    """Returns (state, note). Missing or damaged files start safely, never the whole old Inbox.

    With --stateless no file is read at all: the run starts empty and says nothing about it.
    """
    if STATELESS:
        return fresh_state(), None
    if not STATE_PATH.exists():
        return fresh_state(), f"Starting fresh: there is no state file yet. Looking only at emails from {start_note()}."
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("emails"), dict):
            raise ValueError("not a state file")
        return data, None
    except (OSError, ValueError):
        if live:
            aside = STATE_PATH.with_name(f"{STATE_PATH.name}.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
            os.replace(STATE_PATH, aside)
            note = (
                f"The state file looked damaged, so I set it aside as {aside.name} "
                f"and started fresh. Looking only at emails from {start_note()}."
            )
        else:
            note = (
                "The state file looks damaged. A live run would set it aside and start fresh, "
                f"looking only at emails from {start_note()}."
            )
        return fresh_state(), note


def tidy_state(state):
    """Drop what an older state file holds that is no longer kept.

    A Message-ID header is replaced by the short ID made from it (the header can contain the sender's mail domain;
    the short ID is the same one the output uses, so an old record keeps the name it always had). The list of
    reported system emails is dropped (they are only counted now). Nothing else about a record changes.
    """
    state.pop("system_skipped", None)
    for entry in (state.get("emails") or {}).values():
        if isinstance(entry, dict) and "internet_message_id" in entry:
            header = entry.pop("internet_message_id")
            if header and "short_id" not in entry:
                entry["short_id"] = short_code(header)


def save_state(state):
    """Write the whole file to a copy first, then swap it in. With --stateless nothing is written."""
    if STATELESS:
        return
    tidy_state(state)
    temp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE_PATH)


def record(state, message_id, category, stage, urgency, short):
    state["emails"][message_id] = {
        "outcome": category,
        "stage": stage,
        "urgency": urgency,
        "date": now().date().isoformat(),
        # The email's short ID, made from its own Message-ID header (which is not kept: it can name the sender's
        # mail domain). The same ID is printed, so a log line can be matched to its record.
        "short_id": short,
    }
    save_state(state)


# ---- The run ----------------------------------------------------------------

def choose_emails(messages, state, since, only_subject):
    """Returns (eligible [(message, kind)], skipped_count, system_emails), oldest first."""
    needle = only_subject.strip().lower() if only_subject is not None else None
    dated = []
    for message in messages:
        received = parse_time(message.get("receivedDateTime"))
        if received is None or received < since:
            continue
        dated.append((received, message))
    dated.sort(key=lambda pair: (pair[0], pair[1].get("id", "")))

    eligible, skipped, system, seen = [], 0, [], set()
    for _, message in dated:
        message_id = message.get("id")
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        if needle is not None and needle not in (message.get("subject") or "").lower():
            continue
        entry = state["emails"].get(message_id)
        if entry and entry.get("stage") == "categorised":
            eligible.append((message, "retry"))
        elif entry:
            skipped += 1
        elif lead_category_of(message):
            eligible.append((message, "retry"))      # a lead still in the Inbox: its move failed, just move it
        elif has_our_category(message):
            skipped += 1
        elif is_system_email(message):
            system.append(message)
        else:
            eligible.append((message, "new"))
    return eligible, skipped, system


def dry_run(model, prompt_version, eligible, skipped, waiting, slack_mode="off", drafts_mode="off"):
    print("Dry run: nothing will be sent to Gemini and nothing will be changed in Outlook.")
    print(f"Prompt version: {prompt_version}")
    print(f"Model: {model}")
    if slack_mode != "off":
        print(f"Slack posting: {slack_mode} (nothing is posted in a dry run)")
    if drafts_mode == "on":
        print("Reply drafts: on (nothing is made in a dry run)")
    for message, kind in eligible:
        action = "would send to Gemini" if kind == "new" else "would retry moving to Leads"
        print(f"{action}: {short_id(message)}")
    print(
        f"Dry run finished: {len(eligible)} email(s) would be handled "
        f"({skipped} skipped, {waiting} still waiting). Add --live to do it for real."
    )
    return 0


def print_system_count(count):
    """System and security emails are left completely untouched and only counted, never listed."""
    print(f"System or security emails skipped: {count}")


def live_run(token, key, model, prompt, state, eligible, skipped, waiting, system_count,
             slack_url=None, slack_mode="off", drafts_mode="off"):
    leads_id = find_leads_folder(token)   # stops here, before any change, if there is no Leads folder
    counts = {}
    broken = 0
    handled = 0
    asked = 0
    posted = 0
    drafts_made = 0
    drafts_skipped = 0
    current = None
    try:
        for message, kind in eligible:
            current = label = short_id(message)     # emails are named by a short ID only, never by sender or subject
            if kind == "retry":
                entry = state["emails"].get(message["id"])
                category = entry["outcome"] if entry else lead_category_of(message)
                move_to_leads(token, message, leads_id, label)
                if entry:
                    entry["stage"] = "done"
                    save_state(state)
                else:
                    record(state, message["id"], category, "done", None, label)
                print(f"{label}: moved to Leads (retry)")
            else:
                if asked:
                    time.sleep(extract_leads.PAUSE_SECONDS)
                asked += 1
                raw = ask_gemini(model, key, build_message(prompt, email_text(message)))
                parsed = parse_reply(raw)
                result, _problems = v3_reply.read_v3(parsed)
                category, urgency = result["category"], result["urgency"]
                broken += 1 if parsed is None else 0
                is_lead = category in v3_reply.LEAD_CATEGORIES
                if is_lead:
                    # Saved first, while the email's id is known: if this fails nothing else was changed.
                    saved_results.save_result(token, message["id"], result)
                    if slack_url:
                        # Then announced, once (a marker on the email stops a second post), before the email
                        # is categorised or moved. If Slack is down the run stops and the email is left as it is.
                        if posted:
                            time.sleep(lead_slack.PAUSE_SECONDS)
                        if lead_slack.announce_lead(token, message, category, slack_url) == "posted":
                            posted += 1
                            print(f"  posted to Slack ({slack_mode})")
                        else:
                            print("  already posted to Slack earlier")
                    if drafts_mode == "on":
                        # Then one reply draft in Drafts (a marker on the email and a look in Drafts stop a second
                        # one), still before the email is categorised or moved. If it fails the run stops and the
                        # email is left as it is. A lead that should get no draft carries on.
                        status, reason = reply_drafts.make_reply_draft(token, message, result)
                        if status == "created":
                            drafts_made += 1
                            print("  reply draft made in Drafts")
                        elif status == "exists":
                            print("  reply draft already made earlier")
                        else:
                            drafts_skipped += 1
                            print(f"  no reply draft was made: {reason}.")
                add_category(token, message, category)
                record(state, message["id"], category, "categorised" if is_lead else "done", urgency, label)
                if is_lead:
                    move_to_leads(token, message, leads_id, label)
                    state["emails"][message["id"]]["stage"] = "done"
                    save_state(state)
                print(f"{label}: {category}" + (", moved to Leads" if is_lead else ""))
                if is_lead and result["draft_reply"] is None:
                    print("  no usable draft was written for this lead, so a person will need to write the reply")
            counts[category] = counts.get(category, 0) + 1
            handled += 1
    except PROBLEMS as problem:
        print(problem)
        if current:
            print(f"The email being handled was {current}.")
        print(f"Stopped. {handled} email(s) were handled before this. The rest were left untouched.")
        return 1

    print()
    print(f"Done: {handled} email(s) handled.")
    for category in OUR_CATEGORIES:
        if counts.get(category):
            print(f"  {category}: {counts[category]}")
    print(f"Skipped (already handled or already tagged): {skipped}")
    print_system_count(system_count)
    print(f"Broken replies: {broken}")
    if slack_url:
        print(f"Posted to Slack ({slack_mode}): {posted}")
    if drafts_mode == "on":
        print(f"Reply drafts made: {drafts_made}")
        if drafts_skipped:
            print(f"Reply drafts not made: {drafts_skipped} (a person writes those replies by hand)")
    if waiting:
        print(f"{waiting} still waiting for the next run.")
    return 0


def run(live, only_subject, prompt_version, slack_mode="off", drafts_mode="off", stateless=False):
    global STATELESS
    STATELESS = bool(stateless)       # set on every run, so one run never leaks its setting into the next
    if only_subject is not None and not only_subject.strip():
        raise SignInProblem(
            "The --only-subject text must not be empty, because empty text would match every email."
        )
    slack_url = slack_link(slack_mode)     # stops here, before anything is read or changed, if it is missing
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    model = load_model()
    prompt = load_prompt(prompt_version)
    key = get_api_key() if live else None
    token = get_access_token(env_text, client_id)

    state, note = load_state(live)
    if note:
        print(note)

    since = now() - datetime.timedelta(days=LOOKBACK_DAYS)
    eligible, skipped, system = choose_emails(list_inbox(token, since), state, since, only_subject)
    waiting = max(0, len(eligible) - MAX_PER_RUN)
    eligible = eligible[:MAX_PER_RUN]

    # System and security emails are left completely untouched, and only counted.
    if not eligible:
        if only_subject is not None and not system:
            print("No new email matching the --only-subject filter was found. Nothing was changed.")
        else:
            print(f"No new emails to handle. Skipped {skipped} that were already handled or tagged.")
        print_system_count(len(system))
        return 0

    if not live:
        code = dry_run(model, prompt_version, eligible, skipped, waiting, slack_mode, drafts_mode)
        print_system_count(len(system))
        return code
    return live_run(token, key, model, prompt, state, eligible, skipped, waiting, len(system),
                    slack_url, slack_mode, drafts_mode)


def main():
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Triage new Outlook Inbox emails with Gemini.")
    parser.add_argument("--live", action="store_true", help="really write to Outlook (default is a dry run)")
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=f"prompt version to use (default {DEFAULT_PROMPT}; it must be a v3-style prompt that returns a category and a draft reply)",
    )
    parser.add_argument(
        "--only-subject",
        metavar="TEXT",
        help="only handle emails whose subject contains TEXT (never widens the search)",
    )
    parser.add_argument(
        "--slack",
        choices=SLACK_MODES,
        default="off",
        help="post each lead to Slack once: off (default), test (the test channel) or live (the real #leads channel)",
    )
    parser.add_argument(
        "--drafts",
        choices=DRAFT_MODES,
        default="off",
        help="make one reply draft in Drafts for each lead (never sent): off (default) or on",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="keep no state file at all: the mailbox (categories, markers, folders) is the memory (for GitHub Actions)",
    )
    args = parser.parse_args()

    try:
        return run(args.live, args.only_subject, args.prompt, args.slack, args.drafts, args.stateless)
    except PROBLEMS as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        memory = "The mailbox" if STATELESS else "The state file"
        print(
            f"Something unexpected went wrong ({type(unexpected).__name__}). "
            f"{memory} shows which emails were already handled."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
