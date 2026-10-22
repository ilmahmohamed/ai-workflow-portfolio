"""One-off check: does data saved on an email stay on it after the email is moved?

The plan is to save Gemini's whole answer on the email itself (a small "open extension" attached
to the message) and then move the email into Leads. Microsoft says a move "creates a new copy of
the message" and does not say whether the saved data comes along. This check finds out.

By default this is a dry run: it only looks in your Drafts folder and says what it WOULD do.
With --live it does this to ONE fake draft (a draft is used, not an Inbox email, because the Inbox
is triaged every 2 minutes):
  1. creates the fake draft in Drafts (fake text, no recipient),
  2. saves a pretend answer on it and reads it back,
  3. moves it to Deleted Items,
  4. reads the saved answer back from its new place.
Nothing is ever sent or deleted. The fake draft ends up in Deleted Items.

This script can never send anything. Every request goes through one guard (see graph) that only
allows: read Drafts, read our one extension (by its fixed name, no query string) on one email,
create one fake draft in Drafts, add one extension (fixed name and shape) to one email, and move
one email to Deleted Items. Anything else is refused before it leaves the computer. The sign-in permission is Mail.ReadWrite, which is
not enough to send in any case. No token is ever printed.

Run:  python workflows\\01-outlook-to-slack\\extension_check.py
      python workflows\\01-outlook-to-slack\\extension_check.py --live
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

SUBJECT = "FAKE EXTENSION CHECK - delete me - not for sending"
BODY_TEXT = (
    "This is a fake draft made by a test (extension_check.py). "
    "It is not a real email and has no recipient. Please delete it."
)
SEARCH_LIMIT = 50

EXTENSION_NAME = "com.novawellness.leadresult"
EXTENSION_VERSION = 1
EXTENSION_TYPE = "microsoft.graph.openTypeExtension"
MOVE_TARGET = "deleteditems"

DRAFTS_MESSAGES = "/me/mailFolders/drafts/messages"
# The one way an extension is read: by its fixed name, with no query string (Microsoft's
# documented form; asking for all of an email's extensions at once was refused with a 400).
ONE_EXTENSION = re.compile(r"^/me/messages/[^/]+/extensions/" + re.escape(EXTENSION_NAME) + r"$")
ONE_MESSAGE_EXTENSIONS = re.compile(r"^/me/messages/[^/]+/extensions$")
ONE_MESSAGE_MOVE = re.compile(r"^/me/messages/[^/]+/move$")


class OutlookRefused(Exception):
    """Outlook answered with an error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the script, never something to retry."""


def is_plain_draft(body):
    """True only for a subject plus a plain text body: no recipients, nothing else."""
    if not isinstance(body, dict) or set(body) != {"subject", "body"}:
        return False
    text = body["body"]
    return isinstance(text, dict) and set(text) == {"contentType", "content"} and text["contentType"] == "Text"


def is_extension_body(body):
    """True only for our one extension, with exactly the fixed shape."""
    return (
        isinstance(body, dict)
        and set(body) == {"@odata.type", "extensionName", "version", "data"}
        and body["@odata.type"] == EXTENSION_TYPE
        and body["extensionName"] == EXTENSION_NAME
        and type(body["version"]) is int
        and body["version"] == EXTENSION_VERSION
        and isinstance(body["data"], str)
    )


def check_allowed(method, path, params, body):
    """Refuse anything except the kinds of request this script is for."""
    if method == "GET" and path == DRAFTS_MESSAGES:
        return
    if method == "GET" and ONE_EXTENSION.match(path) and not params:
        return
    if method == "POST":
        if path == DRAFTS_MESSAGES and is_plain_draft(body):
            return
        if ONE_MESSAGE_EXTENSIONS.match(path) and is_extension_body(body):
            return
        if ONE_MESSAGE_MOVE.match(path) and body == {"destinationId": MOVE_TARGET}:
            return
    raise BlockedRequest(f"{method} {path} is not allowed in this script")


def graph(token, method, path, params=None, body=None):
    """Talk to Outlook. Only the requests allowed by check_allowed ever leave the computer."""
    check_allowed(method, path, params, body)
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


def do_step(name, call):
    """Run one change request, turning a refusal into a plain message that names the step."""
    try:
        return call()
    except OutlookRefused:
        raise SetupProblem(
            f"Outlook refused to {name}. Your sign-in may need redoing: "
            "run outlook_signin.py again."
        )


def find_leftovers(token):
    """Drafts whose subject is exactly SUBJECT (extra spaces ignored)."""
    data = read_from_outlook(
        token,
        DRAFTS_MESSAGES,
        {
            "$filter": "startswith(subject,'FAKE EXTENSION CHECK')",
            "$select": "id,subject",
            "$top": str(SEARCH_LIMIT),
        },
    )
    return [m for m in data.get("value", []) if (m.get("subject") or "").strip() == SUBJECT]


def draft_body():
    return {"subject": SUBJECT, "body": {"contentType": "Text", "content": BODY_TEXT}}


def sample_data():
    """A pretend answer, about the size of a real one. Every detail is fake."""
    answer = {
        "is_lead": True,
        "category": "Lead - Demo",
        "name": "Fake Person",
        "email": "fake.person@example.com",
        "company": None,
        "role": "Studio owner",
        "request_type": "demo",
        "urgency": 2,
        "summary": "Pide una demostración para su estudio de yoga. Café incluido.",
        "draft_reply": (
            "Hello Fake, thank you for getting in touch about a demo. "
            "A member of our team will follow up with you. " * 6
        ).strip(),
    }
    return json.dumps(answer, ensure_ascii=False)


def extension_body(data_text):
    return {
        "@odata.type": EXTENSION_TYPE,
        "extensionName": EXTENSION_NAME,
        "version": EXTENSION_VERSION,
        "data": data_text,
    }


def read_saved(token, message_id):
    """The saved data text on this email, or None if our extension is not there.

    "Not found" (404) means nothing is saved. Any other problem stops the check with a plain
    message, so a failed read is never mistaken for "nothing saved".
    """
    path = "/me/messages/" + urllib.parse.quote(message_id, safe="") + "/extensions/" + EXTENSION_NAME
    try:
        shown = graph(token, "GET", path)
    except OutlookRefused as refused:
        if refused.code == 404:
            return None
        if refused.code in (401, 403):
            raise SetupProblem(
                "Outlook didn't accept the sign-in. Please run outlook_signin.py again."
            )
        raise SetupProblem(f"Outlook refused the request (code {refused.code}).")
    if (
        isinstance(shown, dict)
        and shown.get("extensionName") == EXTENSION_NAME
        and shown.get("version") == EXTENSION_VERSION
        and isinstance(shown.get("data"), str)
    ):
        return shown["data"]
    return None


def where_note(stage):
    """Where the fake draft may be, so it can be removed by hand."""
    if stage == "creating":
        return "Nothing was created."
    if stage == "created":
        return f'The fake draft "{SUBJECT}" may be in your Drafts folder: please delete it by hand.'
    if stage == "moving":
        return (
            f'The fake draft "{SUBJECT}" may be in your Drafts or Deleted Items folder: '
            "please delete it by hand."
        )
    return f'The fake draft "{SUBJECT}" is in your Deleted Items folder.'


def run(live):
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    token = get_access_token(env_text, client_id)

    if find_leftovers(token):
        print(
            f'A fake draft with the subject "{SUBJECT}" is already in your Drafts folder. '
            "Please delete it by hand first. Nothing was created."
        )
        return 1

    if not live:
        print("Dry run: nothing will be created.")
        print(f'Your Drafts folder has no draft with the subject "{SUBJECT}".')
        print("would: 1) create 1 fake draft in your Drafts folder with no recipient,")
        print("       2) save a pretend answer on it and read it back,")
        print("       3) move it to Deleted Items,")
        print("       4) read the saved answer back from its new place.")
        print("Nothing is sent and nothing is deleted. Add --live to do it for real.")
        return 0

    print(f'LIVE: creating 1 fake draft with the subject "{SUBJECT}".')
    stage = "creating"
    saved = sample_data()
    try:
        created = do_step("create the draft", lambda: graph(token, "POST", DRAFTS_MESSAGES, body=draft_body()))
        stage = "created"
        draft_id = created.get("id") if isinstance(created, dict) else None
        if not draft_id:
            raise SetupProblem("Outlook did not say which draft it created, so the check did not pass.")
        path = "/me/messages/" + urllib.parse.quote(draft_id, safe="")

        do_step(
            "save the data on the draft",
            lambda: graph(token, "POST", path + "/extensions", body=extension_body(saved)),
        )
        if read_saved(token, draft_id) != saved:
            print(
                "The saved data came back different from what was saved (or was missing), "
                f"so the check did not pass. {where_note(stage)}"
            )
            return 1

        stage = "moving"
        moved = do_step(
            "move the draft to Deleted Items",
            lambda: graph(token, "POST", path + "/move", body={"destinationId": MOVE_TARGET}),
        )
        new_id = moved.get("id") if isinstance(moved, dict) else None
        if not new_id:
            raise SetupProblem("Outlook did not say where the moved draft went, so the check did not pass.")
        stage = "moved"

        after = read_saved(token, new_id)
    except SetupProblem as problem:
        print(f"{problem} {where_note(stage)}")
        return 1

    if after is None:
        print(
            "The saved data did NOT survive the move: the email came back without it. "
            "So another place to keep Gemini's answer is needed. "
            f"{where_note(stage)} Nothing was sent."
        )
        return 1
    if after != saved:
        print(
            "The saved data came back different from what was saved after the move, "
            f"so the check did not pass. {where_note(stage)}"
        )
        return 1
    print(
        "Extension check passed: the saved data stayed on the email after it was moved. "
        f"{where_note(stage)} Nothing was sent."
    )
    return 0


def main():
    # Stop odd characters from crashing the printout.
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Check that data saved on an email survives a move (it never sends anything)."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="really make the one fake draft and move it (without this it is a dry run)",
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
