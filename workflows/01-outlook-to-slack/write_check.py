"""One-off check that the Outlook sign-in can really write.

Finds the email in the Inbox whose subject is exactly "WRITE-CHECK test".
By default this is a dry run: it only says what it WOULD do and changes nothing.
With --live it adds the category "Needs review" to that one email, confirms
Outlook accepted it, then removes the category again so the email ends up with
exactly the categories it had before.

It never moves, deletes, sends, replies to or forwards anything. The only change
it ever asks Outlook for is the category list of that one email. No token is
ever printed.

Run:  python workflows\\01-outlook-to-slack\\write_check.py
      python workflows\\01-outlook-to-slack\\write_check.py --live
Needs: MS_CLIENT_ID and MS_TOKEN_CACHE (run outlook_signin.py once first)
       and the msal library.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# Reuse the sign-in helpers, the same way the Slack script does.
from outlook_signin import SetupProblem, get_client_id, read_env_text
from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS, get_access_token

SUBJECT = "WRITE-CHECK test"
CATEGORY = "Needs review"
SEARCH_LIMIT = 50


class OutlookRefused(Exception):
    """Outlook answered with an error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def graph(token, method, path, params=None, body=None):
    """Talk to Outlook. Only GET and PATCH are ever used, and PATCH only for categories."""
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


def same_categories(first, second):
    return [c.strip().lower() for c in first] == [c.strip().lower() for c in second]


def find_matches(token):
    """Emails in the Inbox whose subject is exactly SUBJECT (extra spaces ignored)."""
    data = read_from_outlook(
        token,
        "/me/mailFolders/inbox/messages",
        {
            "$filter": "startswith(subject,'WRITE-CHECK')",
            "$select": "id,subject,categories",
            "$top": str(SEARCH_LIMIT),
        },
    )
    return [m for m in data.get("value", []) if (m.get("subject") or "").strip() == SUBJECT]


def set_categories(token, path, categories):
    """The only change this script ever asks for. Returns Outlook's answer."""
    return graph(token, "PATCH", path, body={"categories": categories})


def add_category(token, path, original):
    try:
        set_categories(token, path, original + [CATEGORY])
    except OutlookRefused:
        raise SetupProblem(
            "Outlook refused the change. Your sign-in may need redoing: "
            "run outlook_signin.py again. Nothing was changed."
        )


def category_is_there(token, path):
    data = read_from_outlook(token, path, {"$select": "categories"})
    return CATEGORY.lower() in [c.strip().lower() for c in data.get("categories", [])]


def put_back(token, path, original):
    """Remove the category again. Never raises. Returns True only if it worked."""
    try:
        answer = set_categories(token, path, original)
    except (OutlookRefused, SetupProblem):
        return False
    except Exception:
        return False
    if isinstance(answer, dict) and "categories" in answer:
        return same_categories(answer["categories"], original)
    return True


def run(live):
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    token = get_access_token(env_text, client_id)

    matches = find_matches(token)
    if not matches:
        print(f'I couldn\'t find an email in your Inbox with the subject "{SUBJECT}". Nothing was changed.')
        return 1
    if len(matches) > 1:
        print(
            f'I found {len(matches)} emails with the subject "{SUBJECT}", '
            "but I need exactly one. Nothing was changed."
        )
        return 1

    found = matches[0]
    original = list(found.get("categories") or [])
    if CATEGORY.lower() in [c.strip().lower() for c in original]:
        print(
            f'That email already has the category "{CATEGORY}", so I can\'t test it, '
            "because I couldn't put the email back exactly as it was. Nothing was changed."
        )
        return 1

    if not live:
        print("Dry run: nothing will be changed.")
        print(f'Found 1 email in your Inbox with the subject "{SUBJECT}".')
        print(f"would add the category {CATEGORY} to 1 email, confirm it, then remove it again.")
        print("Add --live to do it for real.")
        return 0

    path = "/me/messages/" + urllib.parse.quote(found["id"], safe="")
    print(f'LIVE: adding the category "{CATEGORY}" to the email "{SUBJECT}".')
    add_category(token, path, original)

    confirmed = False
    restored = False
    try:
        try:
            confirmed = category_is_there(token, path)
        except Exception:
            confirmed = False
    finally:
        # Always put the email back, even if the confirm step went wrong.
        restored = put_back(token, path, original)

    if not restored:
        print(
            f'I couldn\'t remove the category "{CATEGORY}" again. '
            f'It may still be on the email "{SUBJECT}". '
            "Please remove it by hand in Outlook."
        )
        return 1
    if not confirmed:
        print(
            "The confirm step failed, so the check did not pass. "
            "I put the email back as it was."
        )
        return 1
    print(
        f'Write check passed: Outlook accepted the category "{CATEGORY}" on the email, '
        "and I removed it again. The email's categories are back to what they were."
    )
    return 0


def main():
    # Stop odd characters from crashing the printout.
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description='Check that the Outlook sign-in can write, using the email "WRITE-CHECK test".'
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="really add and remove the category (without this it is a dry run)",
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
