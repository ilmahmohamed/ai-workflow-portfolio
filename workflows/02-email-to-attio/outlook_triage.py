"""Triage new Outlook Inbox emails with Gemini and write the answer back.

For each new email in the Inbox it asks Gemini ONE question (prompt v3 by default, model from
config.json). The answer holds everything: whether it is a lead, the category, the lead's details
and a short draft reply. Then, in Outlook, it adds one category:
  Lead - Demo, Lead - Pricing, Lead - Other, Not a lead or Needs review.
The category is checked against the request type, never just trusted (see v3_reply.py).
For a real lead it also saves the answer on the email itself (a small hidden extension, see
saved_results.py) before moving the email into the Leads folder, so the Attio step can read it there
and never needs to ask Gemini again. Not a lead and Needs review emails stay in the Inbox and get
nothing saved. Handled emails are remembered in a small state file (triage-state.json, next to this
script, git-ignored) so nothing is handled twice. The state file holds no lead details.

Dry run by default: it only lists the emails it would send to Gemini and changes
nothing. Add --live to write to Outlook. It never sends, replies to, forwards,
copies or deletes anything. The only changes it asks Outlook for are adding a
category, saving the answer on a lead's email, and moving a lead to the Leads folder.
No token or key is ever printed, and no lead detail or draft text is printed or kept in a file.

Run:  python workflows\\02-email-to-attio\\outlook_triage.py
      python workflows\\02-email-to-attio\\outlook_triage.py --only-subject "TRIAGE-TEST"
      python workflows\\02-email-to-attio\\outlook_triage.py --live --only-subject "TRIAGE-TEST"
Needs: MS_CLIENT_ID and MS_TOKEN_CACHE (run outlook_signin.py once first), the msal
       library, and for --live also GEMINI_API_KEY.
"""

import argparse
import datetime
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
import saved_results  # noqa: E402
import v3_reply  # noqa: E402
from extract_leads import ask_gemini, build_message, get_api_key, load_model, load_prompt, parse_reply  # noqa: E402
from outlook_signin import SetupProblem as SignInProblem  # noqa: E402
from outlook_signin import get_client_id, read_env_text  # noqa: E402
from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS, find_leads_folder, get_access_token  # noqa: E402

STATE_PATH = HERE / "triage-state.json"
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

PROBLEMS = (SignInProblem, extract_leads.SetupProblem, saved_results.SavedResultProblem)


class OutlookRefused(Exception):
    """Outlook answered with an error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def now():
    return datetime.datetime.now(datetime.timezone.utc)


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
        "$select": "id,subject,from,receivedDateTime,categories,body,internetMessageId",
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
    """Returns (state, note). Missing or damaged files start safely, never the whole old Inbox."""
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


def save_state(state):
    """Write the whole file to a copy first, then swap it in."""
    temp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE_PATH)


def record(state, message_id, category, stage, urgency, internet_message_id=None):
    state["emails"][message_id] = {
        "outcome": category,
        "stage": stage,
        "urgency": urgency,
        "date": now().date().isoformat(),
    }
    if internet_message_id:
        # The email's own Message-ID header. Unlike the Outlook ID, it does not change when the
        # email is moved, so later steps (the Attio script) can recognise the email in Leads.
        state["emails"][message_id]["internet_message_id"] = internet_message_id
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
        elif entry or has_our_category(message):
            skipped += 1
        elif is_system_email(message):
            system.append(message)
        else:
            eligible.append((message, "new"))
    return eligible, skipped, system


def dry_run(model, prompt_version, eligible, skipped, waiting):
    print("Dry run: nothing will be sent to Gemini and nothing will be changed in Outlook.")
    print(f"Prompt version: {prompt_version}")
    print(f"Model: {model}")
    for message, kind in eligible:
        address, _ = sender_of(message)
        action = "would send to Gemini" if kind == "new" else "would retry moving to Leads"
        print(f"{action}: {address}, {subject_of(message)}")
    print(
        f"Dry run finished: {len(eligible)} email(s) would be handled "
        f"({skipped} skipped, {waiting} still waiting). Add --live to do it for real."
    )
    return 0


def print_system_count(count, already=0):
    suffix = f" ({already} already reported earlier)" if already else ""
    print(f"System or security emails skipped: {count}{suffix}")


def remember_system_emails(state, new_system):
    """Note which system emails have been reported, so later runs do not print them again.

    This is a separate list in the state file, never an entry among the handled emails, and the
    emails themselves stay completely untouched. Old notes are forgotten so the list stays small.
    """
    reported = state.get("system_skipped")
    if not isinstance(reported, dict):
        reported = state["system_skipped"] = {}
    today = now().date()
    changed = False
    for message in new_system:
        reported[message["id"]] = today.isoformat()
        changed = True
    cutoff = (today - datetime.timedelta(days=2 * LOOKBACK_DAYS)).isoformat()
    for message_id in [i for i, date in reported.items() if str(date) < cutoff]:
        del reported[message_id]
        changed = True
    if changed:
        save_state(state)


def live_run(token, key, model, prompt, state, eligible, skipped, waiting, system_count, system_already=0):
    leads_id = find_leads_folder(token)   # stops here, before any change, if there is no Leads folder
    counts = {}
    broken = 0
    handled = 0
    asked = 0
    try:
        for message, kind in eligible:
            address, _ = sender_of(message)
            subject = subject_of(message)
            label = f'"{subject}"'
            if kind == "retry":
                category = state["emails"][message["id"]]["outcome"]
                move_to_leads(token, message, leads_id, label)
                state["emails"][message["id"]]["stage"] = "done"
                save_state(state)
                print(f"{address}, {subject}: moved to Leads (retry)")
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
                add_category(token, message, category)
                record(state, message["id"], category, "categorised" if is_lead else "done", urgency,
                       message.get("internetMessageId"))
                if is_lead:
                    move_to_leads(token, message, leads_id, label)
                    state["emails"][message["id"]]["stage"] = "done"
                    save_state(state)
                print(f"{address}, {subject}: {category}" + (", moved to Leads" if is_lead else ""))
                if is_lead and result["draft_reply"] is None:
                    print("  no usable draft was written for this lead, so a person will need to write the reply")
            counts[category] = counts.get(category, 0) + 1
            handled += 1
    except PROBLEMS as problem:
        print(problem)
        print(f"Stopped. {handled} email(s) were handled before this. The rest were left untouched.")
        return 1

    print()
    print(f"Done: {handled} email(s) handled.")
    for category in OUR_CATEGORIES:
        if counts.get(category):
            print(f"  {category}: {counts[category]}")
    print(f"Skipped (already handled or already tagged): {skipped}")
    print_system_count(system_count, system_already)
    print(f"Broken replies: {broken}")
    if waiting:
        print(f"{waiting} still waiting for the next run.")
    return 0


def run(live, only_subject, prompt_version):
    if only_subject is not None and not only_subject.strip():
        raise SignInProblem(
            "The --only-subject text must not be empty, because empty text would match every email."
        )
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

    # System and security emails are left completely untouched. Each is reported once, the first time
    # it is seen. Those reported on an earlier run are only counted, so the log stays quiet.
    reported_before = state.get("system_skipped")
    reported_before = reported_before if isinstance(reported_before, dict) else {}
    new_system = [m for m in system if m["id"] not in reported_before]
    for message in new_system:
        print(f"Skipped (system or security email): {subject_of(message)}")
    system_already = len(system) - len(new_system)
    if live:
        remember_system_emails(state, new_system)

    if not eligible:
        if only_subject is not None and not system:
            print(f'No new email with "{only_subject.strip()}" in the subject was found. Nothing was changed.')
        else:
            print(f"No new emails to handle. Skipped {skipped} that were already handled or tagged.")
        print_system_count(len(system), system_already)
        return 0

    if not live:
        code = dry_run(model, prompt_version, eligible, skipped, waiting)
        print_system_count(len(system), system_already)
        return code
    return live_run(token, key, model, prompt, state, eligible, skipped, waiting, len(system), system_already)


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
    args = parser.parse_args()

    try:
        return run(args.live, args.only_subject, args.prompt)
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
