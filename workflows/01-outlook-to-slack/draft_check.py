"""One-off check that the Outlook sign-in can create a draft email.

By default this is a dry run: it only looks in your Drafts folder and says what it WOULD do.
With --live it creates exactly ONE draft in Drafts, with obviously fake text and no recipient,
then reads it back to confirm it is really a draft, sitting in Drafts, with nobody to send it to.
The draft is left there for you to delete by hand.

This script can never send anything. Every request goes through one guard (see graph) that only
allows three kinds of request: read the Drafts folder, read one email, and create one draft in
Drafts with just a subject and a plain text body. Anything else is refused before it leaves the
computer. The sign-in permission is Mail.ReadWrite, which is not enough to send in any case.
No token is ever printed.

Run:  python workflows\\01-outlook-to-slack\\draft_check.py
      python workflows\\01-outlook-to-slack\\draft_check.py --live
Needs: MS_CLIENT_ID and MS_TOKEN_CACHE (run outlook_signin.py once first)
       and the msal library.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# Reuse the sign-in helpers, the same way the other scripts do.
from outlook_signin import SetupProblem, get_client_id, read_env_text
from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS, get_access_token

SUBJECT = "FAKE DRAFT - delete me - not for sending"
BODY_TEXT = (
    "This is a fake draft made by a test (draft_check.py) to check that drafts can be created. "
    "It is not a real email and has no recipient. Please delete it."
)
SEARCH_LIMIT = 50

DRAFTS_FOLDER = "/me/mailFolders/drafts"
DRAFTS_MESSAGES = DRAFTS_FOLDER + "/messages"
ONE_MESSAGE = re.compile(r"^/me/messages/[^/]+$")


class OutlookRefused(Exception):
    """Outlook answered with an error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the script, never something to retry."""


def check_allowed(method, path, body):
    """Refuse anything except the three kinds of request this script is for."""
    if method == "GET" and (path in (DRAFTS_FOLDER, DRAFTS_MESSAGES) or ONE_MESSAGE.match(path)):
        return
    if method == "POST" and path == DRAFTS_MESSAGES and is_plain_draft(body):
        return
    raise BlockedRequest(f"{method} {path} is not allowed in this script")


def is_plain_draft(body):
    """True only for a subject plus a plain text body: no recipients, nothing else."""
    if not isinstance(body, dict) or set(body) != {"subject", "body"}:
        return False
    text = body["body"]
    return isinstance(text, dict) and set(text) == {"contentType", "content"} and text["contentType"] == "Text"


def graph(token, method, path, params=None, body=None):
    """Talk to Outlook. Only the requests allowed by check_allowed ever leave the computer."""
    check_allowed(method, path, body)
    url = GRAPH_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    headers = {"Authorization": f"Bearer {token}"}
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
        raise SetupProblem("I couldn't reach Outlook. Check the internet connection.")


def read_from_outlook(token, path, params):
    """A read-only question to Outlook, with plain-words errors."""
    try:
        return graph(token, "GET", path, params)
    except OutlookRefused as refused:
        if refused.code in (401, 403):
            raise SetupProblem(
                "Outlook didn't accept the sign-in. Please run outlook_signin.py again."
            )
        raise SetupProblem(f"Outlook refused the request (code {refused.code}).")


def find_leftovers(token):
    """Drafts whose subject is exactly SUBJECT (extra spaces ignored)."""
    data = read_from_outlook(
        token,
        DRAFTS_MESSAGES,
        {
            "$filter": "startswith(subject,'FAKE DRAFT')",
            "$select": "id,subject",
            "$top": str(SEARCH_LIMIT),
        },
    )
    return [m for m in data.get("value", []) if (m.get("subject") or "").strip() == SUBJECT]


def draft_body():
    return {"subject": SUBJECT, "body": {"contentType": "Text", "content": BODY_TEXT}}


def create_draft(token):
    """The only change this script ever asks for. Returns Outlook's answer."""
    try:
        return graph(token, "POST", DRAFTS_MESSAGES, body=draft_body())
    except OutlookRefused:
        raise SetupProblem(
            "Outlook refused to create the draft. Your sign-in may need redoing: "
            "run outlook_signin.py again. Nothing was created."
        )
    except SetupProblem as problem:
        raise SetupProblem(
            f"{problem} If a draft called \"{SUBJECT}\" is in your Drafts folder, "
            "please delete it by hand."
        )


def draft_is_confirmed(token, created):
    """Read the new draft back: it must be a draft, in Drafts, with no recipient."""
    message_id = created.get("id") if isinstance(created, dict) else None
    if not message_id:
        return False
    try:
        path = "/me/messages/" + urllib.parse.quote(message_id, safe="")
        shown = read_from_outlook(
            token, path, {"$select": "id,subject,isDraft,parentFolderId,toRecipients"}
        )
        folder = read_from_outlook(token, DRAFTS_FOLDER, {"$select": "id"})
    except SetupProblem:
        return False
    return (
        shown.get("isDraft") is True
        and not shown.get("toRecipients")
        and (shown.get("subject") or "").strip() == SUBJECT
        and bool(folder.get("id"))
        and shown.get("parentFolderId") == folder["id"]
    )


def run(live):
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    token = get_access_token(env_text, client_id)

    leftovers = find_leftovers(token)
    if leftovers:
        print(
            f'A fake draft with the subject "{SUBJECT}" is already in your Drafts folder. '
            "Please delete it by hand first. Nothing was created."
        )
        return 1

    if not live:
        print("Dry run: nothing will be created.")
        print(f'Your Drafts folder has no draft with the subject "{SUBJECT}".')
        print("would create 1 draft in your Drafts folder with no recipient. Nothing is sent.")
        print("Add --live to do it for real.")
        return 0

    print(f'LIVE: creating 1 draft in your Drafts folder with the subject "{SUBJECT}".')
    created = create_draft(token)

    if not draft_is_confirmed(token, created):
        print(
            "The confirm step failed, so the check did not pass. "
            f'The draft "{SUBJECT}" may be in your Drafts folder: '
            "please look there and delete it by hand. Nothing was sent."
        )
        return 1
    print(
        "Draft check passed: Outlook created 1 draft in your Drafts folder, with no recipient. "
        "It was NOT sent. "
        f'Please delete the draft "{SUBJECT}" by hand in Outlook.'
    )
    return 0


def main():
    # Stop odd characters from crashing the printout.
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Check that the Outlook sign-in can create a draft (it never sends anything)."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="really create the one fake draft (without this it is a dry run)",
    )
    args = parser.parse_args()

    try:
        return run(args.live)
    except SetupProblem as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something unexpected went wrong ({type(unexpected).__name__}).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
