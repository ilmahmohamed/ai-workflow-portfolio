"""One-off check: make ONE reply draft in Drafts from the answer saved on one chosen Day 8 test email.

Before the reply draft is wired into the triage, the first real draft is made here so it can be looked at in the
Drafts folder. It calls the same code as the real step (reply_drafts.py), so the draft is the same: addressed only
to the person who wrote the email, the line "AI draft - please review before sending", then the text Gemini saved
on the email, then the fixed sign-off. It is read back and checked right after it is made.

Dry run by default: it only reads (the tagged test emails, the answer saved on the chosen one, the marker and
Drafts) and says what it WOULD do. Add --live to make the one draft. Nothing is ever sent.

Only emails whose subject starts with the tag [DAY8] are ever looked at (in the Inbox, Leads and Leads/Processed),
so a real email can never be chosen. Exactly one email must match --only-subject (and --only-from, if given);
if none or several match it stops and changes nothing.

It never sends, replies to everyone, forwards, moves, copies or deletes anything. Its own requests go through one
guard (see graph) that allows only reading the tagged emails of a folder; everything else goes through the guards
of reply_drafts.py and saved_results.py. The sign-in permission is Mail.ReadWrite. No token, draft text or lead
name is printed, and no file is written. It is a test tool: it is never scheduled and the triage does not use it.

Run:  python workflows\\02-email-to-attio\\reply_draft_check.py --only-subject "[DAY8] Demo request"
      python workflows\\02-email-to-attio\\reply_draft_check.py --only-subject "[DAY8] Demo request" --live
Afterwards, look at the draft in your Drafts folder and delete it by hand.
"""

import argparse
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

import reply_drafts  # noqa: E402
import saved_results  # noqa: E402
import v3_reply  # noqa: E402
from leads_to_attio import find_processed_folder  # noqa: E402
from outlook_signin import SetupProblem, get_client_id, read_env_text  # noqa: E402
from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS, find_leads_folder, get_access_token  # noqa: E402

TAG = "[DAY8]"
LIST_LIMIT = 100
LIST_PARAMS = {
    "$filter": f"startswith(subject,'{TAG}')",
    "$select": "id,subject,from,replyTo,conversationId",
    "$top": str(LIST_LIMIT),
}
FOLDER_MESSAGES = re.compile(r"^/me/mailFolders/[^/]+/messages$")


class CheckProblem(Exception):
    """A problem we can explain in plain words."""


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the code, never something to retry."""


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- The guard and the one question it allows ------------------------------------------------

def check_allowed(method, path, params):
    """Refuse anything except reading the tagged emails of a folder (fixed filter, fields and count)."""
    if method == "GET" and FOLDER_MESSAGES.match(path) and params == LIST_PARAMS:
        return
    raise BlockedRequest(f"{method} {path} is not allowed in this script")


def graph(token, method, path, params=None):
    """Talk to Outlook. Only the requests allowed by check_allowed ever leave the computer."""
    check_allowed(method, path, params)
    url = GRAPH_URL + path + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raise _Refused(error.code)
    except (urllib.error.URLError, OSError):
        raise CheckProblem("I couldn't reach Outlook. Check the internet connection.")


def outlook_problem(refused):
    if refused.code in (401, 403):
        return CheckProblem("Outlook refused the request. Your sign-in may need redoing: run outlook_signin.py again.")
    return CheckProblem(f"Outlook refused the request (code {refused.code}).")


def tagged_emails(token, folder):
    """The tagged emails of a folder ("inbox" or a folder id), as Outlook lists them."""
    path = "/me/mailFolders/" + urllib.parse.quote(folder, safe="") + "/messages"
    try:
        data = graph(token, "GET", path, LIST_PARAMS)
    except _Refused as refused:
        raise outlook_problem(refused)
    rows = data.get("value") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise CheckProblem("Outlook gave an answer I did not understand when I listed the test emails.")
    return rows


# ---- Choosing the one email ----------------------------------------------------------------------

def _one_line(text):
    return " ".join(str(text or "").split())


def _address_of(message):
    return str(((message.get("from") or {}).get("emailAddress") or {}).get("address") or "").strip().lower()


def find_matches(token, folders, needle, wanted_from):
    """(folder label, email) for every tagged email whose subject has the text (and sender, if given)."""
    found = []
    for label, folder in folders:
        for message in tagged_emails(token, folder):
            subject = _one_line(message.get("subject"))
            if not subject.startswith(TAG) or needle.lower() not in subject.lower():
                continue
            if wanted_from is not None and _address_of(message) != wanted_from:
                continue
            found.append((label, message))
    return found


# ---- The run ------------------------------------------------------------------------------------------

def run(live, only_subject, only_from):
    needle = (only_subject or "").strip()
    if not needle:
        raise CheckProblem("The --only-subject text must not be empty, because empty text would match every email.")
    wanted_from = None
    if only_from is not None:
        wanted_from = only_from.strip().lower()
        if not wanted_from:
            raise CheckProblem("The --only-from address must not be empty, because empty text would match every email.")

    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    token = get_access_token(env_text, client_id)

    leads_id = find_leads_folder(token)
    processed_id = find_processed_folder(token, leads_id)
    matches = find_matches(
        token, (("Inbox", "inbox"), ("Leads", leads_id), ("Leads/Processed", processed_id)), needle, wanted_from
    )
    from_part = f' from "{wanted_from}"' if wanted_from else ""
    if not matches:
        raise CheckProblem(
            f'No {TAG} email with "{needle}" in the subject{from_part} was found in the Inbox, Leads or '
            "Leads/Processed. Nothing was changed."
        )
    if len(matches) > 1:
        raise CheckProblem(
            f'I found more than one {TAG} email that matches "{needle}"{from_part} ({len(matches)} of them). '
            "Use more of the subject, or add --only-from with the sender's address, so exactly one matches. "
            "Nothing was changed."
        )
    label, message = matches[0]

    answer = saved_results.load_result(token, message["id"])
    if answer is None:
        raise CheckProblem(
            "No usable answer is saved on this email yet. Run the triage on it first "
            f'(outlook_triage.py --only-subject "{needle}"). Nothing was changed.'
        )

    print("LIVE: making one reply draft." if live else "Dry run: nothing will be created.")
    print(f"Email: {_address_of(message)}, {_one_line(message.get('subject'))} (in {label})")
    print(f"Saved answer: {answer.get('category')}")

    reason = reply_drafts.reply_skip_reason(message, answer)
    if reason:
        print(f"{'No reply draft was made' if live else 'No reply draft would be made'}: {reason}.")
        return 1 if live else 0

    if live:
        status, _ = reply_drafts.make_reply_draft(token, message, answer)
        if status == "exists":
            print("A reply draft was already made for this email (it has the marker, or Drafts holds a draft in "
                  "this conversation). Nothing was created.")
            return 0
        print("Reply draft created. It was read back and checked: a draft, in your Drafts folder, one recipient "
              "(the sender), no cc or bcc, starting with the AI line.")
        print("Nothing was sent. Please look at it in your Drafts folder, then delete it by hand.")
        return 0

    if reply_drafts.draft_already_made(token, message):
        print("A reply draft was already made for this email (it has the marker, or Drafts holds a draft in this "
              "conversation). Nothing would be created.")
        return 0
    words = len(str(answer["draft_reply"]).split())
    sign_off = v3_reply.SIGN_OFF.split("\n")
    print("would create 1 reply draft in your Drafts folder, addressed only to the sender, made of these, each on its own line:")
    print(f'  1) the line "{v3_reply.AI_DRAFT_LINE}"')
    print("  2) the greeting")
    print(f"  3) the body (the text Gemini saved on the email: {words} words in all with the greeting, not shown here)")
    print(f'  4) the sign-off "{sign_off[0]}" and "{sign_off[1]}"')
    print("would then read it back to check it, and mark the email so a second draft is never made.")
    print("Nothing is sent and nothing is changed in a dry run. Add --live to make the draft for real.")
    return 0


def main():
    # Stop odd characters from crashing the printout.
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Make one reply draft from the answer saved on one Day 8 test email (it never sends anything)."
    )
    parser.add_argument("--only-subject", required=True, metavar="TEXT",
                        help=f"text from the subject of the one {TAG} test email")
    parser.add_argument("--only-from", metavar="ADDRESS", help="the sender's address, to tell two emails apart")
    parser.add_argument("--live", action="store_true", help="really make the draft (default is a dry run)")
    args = parser.parse_args()
    try:
        return run(args.live, args.only_subject, args.only_from)
    except (CheckProblem, SetupProblem, reply_drafts.ReplyDraftProblem, saved_results.SavedResultProblem) as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something unexpected went wrong ({type(unexpected).__name__}).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
