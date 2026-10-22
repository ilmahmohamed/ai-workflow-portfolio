"""One-off loader for the Day 8 test: puts the 30 test emails into the Outlook Inbox.

The 30 test emails (tests/cases/lead-emails.md) exist only as text. To run them through the whole pipeline they
must sit in the Inbox with their own senders, subjects and bodies, and real mail cannot have 30 different
senders. So this creates each one directly in the Inbox using Outlook's "create message from MIME", with the
same From, the same body and the subject tagged "[DAY8] ...". Outlook always makes a created message a draft,
so they look like drafts in the Inbox; that is expected, and a pilot (--pilot) proves the triage handles them.

Dry run by default: it only reads (the Inbox, Leads and Leads/Processed, to see what is already loaded) and prints
what it WOULD create. Add --live to create them. An email whose tagged subject is already in the Inbox, Leads or
Leads/Processed is skipped, so running it twice never makes duplicates.

It never sends, replies to, forwards, moves, copies or deletes anything: every request goes through one guard
(see graph) that allows only reading the tagged emails of a folder and creating one message in the Inbox. It is a
test tool and is never scheduled. The sign-in permission is Mail.ReadWrite. No token is printed, no email body is
printed and no file is written.

Run:  python workflows\\02-email-to-attio\\day8_loader.py --pilot
      python workflows\\02-email-to-attio\\day8_loader.py --live --pilot
      python workflows\\02-email-to-attio\\day8_loader.py --live
      python workflows\\02-email-to-attio\\day8_loader.py --only 9,5
Clean up afterwards by hand: delete the emails whose subject starts with [DAY8] (Inbox, Leads, Leads/Processed).
"""

import argparse
import base64
import email.message
import email.policy
import email.utils
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The Outlook sign-in helpers live next door.
sys.path.insert(0, str(HERE.parent / "01-outlook-to-slack"))

import extract_leads  # noqa: E402
from leads_to_attio import find_processed_folder  # noqa: E402
from outlook_signin import SetupProblem, get_client_id, read_env_text  # noqa: E402
from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS, find_leads_folder, get_access_token  # noqa: E402

TAG = "[DAY8]"
PILOT_IDS = (9, 5)             # a pricing lead and an out-of-office reply (not a lead)
LIST_LIMIT = 100
RECIPIENT = "Nova Wellness Leads <leads@nova-wellness.example>"

LIST_PARAMS = {"$filter": f"startswith(subject,'{TAG}')", "$select": "id,subject", "$top": str(LIST_LIMIT)}
FOLDER_MESSAGES = re.compile(r"^/me/mailFolders/[^/]+/messages$")
INBOX_MESSAGES = "/me/mailFolders/inbox/messages"


class LoaderProblem(Exception):
    """A problem we can explain in plain words."""


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the code, never something to retry."""


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- Reading the 30 emails -----------------------------------------------------------------

def read_dataset(path=None):
    """The test emails as a list of dicts: id, from_line, from_address, subject, body."""
    source = Path(path) if path is not None else extract_leads.EMAILS_PATH
    try:
        parsed = extract_leads.parse_emails(source.read_text(encoding="utf-8"))
    except OSError:
        raise LoaderProblem("I couldn't read the test emails file. Nothing was created.")
    if not parsed:
        raise LoaderProblem("The test emails file has no emails in it. Nothing was created.")
    emails = []
    for item in parsed:
        number, text = item["id"], item["text"]
        head, found, body = text.partition("\nBody:")
        from_match = re.search(r"^From:\s*(.+)$", head, re.M)
        subject_match = re.search(r"^Subject:\s*(.+)$", head, re.M)
        address = None
        if from_match:
            inside = re.search(r"<([^>]+)>", from_match.group(1))
            address = (inside.group(1) if inside else from_match.group(1)).strip().lower()
        if not from_match or not address or "@" not in address:
            raise LoaderProblem(f"Email {number} in the test file has no usable From line. Nothing was created.")
        if not subject_match or not subject_match.group(1).strip():
            raise LoaderProblem(f"Email {number} in the test file has no Subject. Nothing was created.")
        if not found or not body.strip():
            raise LoaderProblem(f"Email {number} in the test file has no body. Nothing was created.")
        emails.append({
            "id": number,
            "from_line": from_match.group(1).strip(),
            "from_address": address,
            "subject": subject_match.group(1).strip(),
            "body": body.strip(),
        })
    return emails


def tagged_subject(subject):
    return f"{TAG} {subject}"


def build_mime(item):
    """The MIME message (bytes) that creates one tagged email: same From, tagged subject, same body."""
    message = email.message.EmailMessage(policy=email.policy.SMTP)
    message["From"] = item["from_line"]
    message["To"] = RECIPIENT
    message["Subject"] = tagged_subject(item["subject"])
    message["Date"] = email.utils.formatdate()
    message["Message-ID"] = email.utils.make_msgid(domain="day8.nova-wellness.example")
    message.set_content(item["body"], charset="utf-8")
    return message.as_bytes()


# ---- The guard and the requests --------------------------------------------------------------

def check_allowed(method, path, params, mime):
    """Refuse anything except reading the tagged emails of a folder and creating one message in the Inbox."""
    if method == "GET" and FOLDER_MESSAGES.match(path) and params == LIST_PARAMS and mime is None:
        return
    if method == "POST" and path == INBOX_MESSAGES and not params and isinstance(mime, bytes) and mime:
        try:
            base64.b64decode(mime, validate=True)
        except ValueError:
            raise BlockedRequest("the message is not valid base64")
        return
    raise BlockedRequest(f"{method} {path} is not allowed in this script")


def graph(token, method, path, params=None, mime=None):
    """Talk to Outlook. Only the requests allowed by check_allowed ever leave the computer."""
    check_allowed(method, path, params, mime)
    url = GRAPH_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    headers = {"Authorization": f"Bearer {token}"}
    if mime is not None:
        headers["Content-Type"] = "text/plain"
    request = urllib.request.Request(url, data=mime, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raise _Refused(error.code)
    except (urllib.error.URLError, OSError):
        raise LoaderProblem("I couldn't reach Outlook. Check the internet connection.")


def outlook_problem(refused):
    if refused.code in (401, 403):
        return LoaderProblem("Outlook refused the request. Your sign-in may need redoing: run outlook_signin.py again.")
    return LoaderProblem(f"Outlook refused the request (code {refused.code}).")


def loaded_subjects(token, folder):
    """The tagged subjects already in a folder ("inbox" or a folder id)."""
    path = "/me/mailFolders/" + urllib.parse.quote(folder, safe="") + "/messages"
    try:
        data = graph(token, "GET", path, LIST_PARAMS)
    except _Refused as refused:
        raise outlook_problem(refused)
    return {(row.get("subject") or "").strip() for row in data.get("value", [])}


# ---- Choosing which emails ----------------------------------------------------------------------

def choose(dataset, only_text, pilot):
    """The emails to handle: all of them, the pilot pair, or the numbers given with --only."""
    by_id = {item["id"]: item for item in dataset}
    if pilot:
        numbers = list(PILOT_IDS)
    elif only_text is not None:
        pieces = [piece.strip() for piece in only_text.split(",")]
        if not only_text.strip() or not all(re.fullmatch(r"\d+", piece) for piece in pieces):
            raise LoaderProblem("--only needs email numbers separated by commas, like 9,5.")
        numbers = [int(piece) for piece in pieces]
        unknown = [number for number in numbers if number not in by_id]
        if unknown:
            raise LoaderProblem(f"--only has a number that is not in the test file (the numbers are 1 to {len(dataset)}).")
    else:
        numbers = [item["id"] for item in dataset]
    seen, chosen = set(), []
    for number in numbers:
        if number not in seen:
            seen.add(number)
            chosen.append(by_id[number])
    return chosen


# ---- The run --------------------------------------------------------------------------------------

def run(live, only_text, pilot):
    chosen = choose(read_dataset(), only_text, pilot)
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    token = get_access_token(env_text, client_id)

    leads_id = find_leads_folder(token)
    processed_id = find_processed_folder(token, leads_id)
    existing = set()
    for folder in ("inbox", leads_id, processed_id):
        existing |= loaded_subjects(token, folder)

    print("LIVE: creating the test emails in your Inbox." if live else "Dry run: nothing will be created.")
    created = skipped = 0
    for item in chosen:
        subject = tagged_subject(item["subject"])
        if subject in existing:
            print(f"already loaded: {subject}")
            skipped += 1
            continue
        if not live:
            print(f"would create: {item['from_address']}, {subject}")
            continue
        try:
            answer = graph(token, "POST", INBOX_MESSAGES, mime=base64.b64encode(build_mime(item)))
        except _Refused as refused:
            print(outlook_problem(refused))
            print(f"Stopped. {created} created before this problem. Run it again: the ones already created are skipped.")
            return 1
        except LoaderProblem as problem:
            print(problem)
            print(f"Stopped. {created} created before this problem. Run it again: the ones already created are skipped.")
            return 1
        if not (isinstance(answer, dict) and answer.get("id")):
            print(f"Outlook did not say it created the message. Stopped. {created} created before this problem.")
            return 1
        created += 1
        print(f"created: {item['from_address']}, {subject}")

    print()
    if live:
        print(f"Done: {created} created in your Inbox, {skipped} skipped (loaded before). "
              "They look like drafts there; that is expected.")
    else:
        would = len(chosen) - skipped
        print(f"Dry run finished: {would} would be created, {skipped} skipped (loaded before). "
              "Add --live to create them for real.")
    return 0


def main():
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Put the 30 Day 8 test emails into the Outlook Inbox (one-off).")
    parser.add_argument("--live", action="store_true", help="really create them (default is a dry run)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pilot", action="store_true", help="only emails 9 (a lead) and 5 (not a lead)")
    group.add_argument("--only", metavar="NUMBERS", help="only these email numbers, like 9,5")
    args = parser.parse_args()
    try:
        return run(args.live, args.only, args.pilot)
    except (LoaderProblem, SetupProblem) as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something unexpected went wrong ({type(unexpected).__name__}).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
