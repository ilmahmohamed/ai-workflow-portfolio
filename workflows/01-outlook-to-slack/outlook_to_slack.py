"""Read the Leads folder in the Nova Wellness Outlook and post to Slack #leads.

For each of the newest emails in the Leads folder it takes the sender and the
subject. By default this is a dry run: it only prints "would post: ..." and
sends nothing to Slack. Add --live to post for real.

The script only reads email. It never changes, moves or deletes anything.
The only thing it ever writes is a refreshed sign-in token, saved back to the
MS_TOKEN_CACHE line in .env. No token and no Slack link is ever printed.

Run:  python workflows\\01-outlook-to-slack\\outlook_to_slack.py
      python workflows\\01-outlook-to-slack\\outlook_to_slack.py --live
      python workflows\\01-outlook-to-slack\\outlook_to_slack.py --limit 1
Add --limit N (1 to 100, default 25) to read fewer of the newest emails.
Needs: MS_CLIENT_ID and MS_TOKEN_CACHE (run outlook_signin.py once first),
       the msal library, and for --live also SLACK_WEBHOOK_URL.
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Reuse the helpers from the one-time sign-in script (same folder).
from outlook_signin import (
    AUTHORITY,
    CACHE_NAME,
    ENV_PATH,
    SCOPES,
    SetupProblem,
    get_client_id,
    read_env_text,
    read_env_value,
    save_cache_line,
)

GRAPH_URL = "https://graph.microsoft.com/v1.0"
FOLDER_NAME = "leads"
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
WEB_TIMEOUT_SECONDS = 15


class SlackProblem(Exception):
    """Slack could not be reached or refused the message."""


def get_setting(env_text, name):
    """The environment wins; otherwise use the .env file. "" if not found."""
    value = os.environ.get(name, "").strip()
    if not value:
        value = read_env_value(env_text, name)
    return value


def get_slack_url(env_text):
    url = get_setting(env_text, "SLACK_WEBHOOK_URL")
    if not url:
        raise SetupProblem(
            "I can't find SLACK_WEBHOOK_URL, which --live needs. "
            "Add it to your .env file, or run without --live. "
            "Nothing was posted."
        )
    return url


def load_cache(env_text):
    """Turn the saved MS_TOKEN_CACHE value back into a token cache."""
    import msal

    value = get_setting(env_text, CACHE_NAME)
    if not value:
        raise SetupProblem(
            f"I can't find {CACHE_NAME}. Run outlook_signin.py once first."
        )
    cache = msal.SerializableTokenCache()
    try:
        cache.deserialize(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
    except Exception:
        raise SetupProblem(
            "Your saved sign-in looks damaged. Please run outlook_signin.py again."
        )
    return cache


def save_refreshed_cache(cache):
    """Save a refreshed token to .env, changing only the MS_TOKEN_CACHE line."""
    if not ENV_PATH.exists():
        print("Note: your sign-in was refreshed, but there is no .env file to save it in.")
        return
    try:
        # Read .env again right now so nothing added meanwhile is lost.
        env_text, has_bom = read_env_text()
        compact = json.dumps(json.loads(cache.serialize()), separators=(",", ":"))
        value = base64.urlsafe_b64encode(compact.encode("utf-8")).decode("ascii")
        save_cache_line(env_text, has_bom, value)
    except Exception:
        print(
            "Note: I couldn't save the refreshed sign-in. "
            "The emails below are still fine. Your .env was not changed."
        )


def get_access_token(env_text, client_id):
    """Sign in quietly (no browser) with the saved sign-in. Returns the token."""
    try:
        import msal
    except ImportError:
        raise SetupProblem(
            "The sign-in helper (msal) isn't installed yet. "
            "Install it and try again. Nothing was changed."
        )

    cache = load_cache(env_text)
    app = msal.PublicClientApplication(
        client_id, authority=AUTHORITY, token_cache=cache
    )
    accounts = app.get_accounts()
    if not accounts:
        raise SetupProblem(
            "Your saved sign-in has no account in it. Please run outlook_signin.py again."
        )

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        # Show only the short error label, never the full response.
        reason = (result or {}).get("error", "unknown")
        raise SetupProblem(
            "Your saved sign-in no longer works "
            f"(reason: {reason}). Please run outlook_signin.py again."
        )

    if cache.has_state_changed:
        save_refreshed_cache(cache)
    return result["access_token"]


def graph_get(token, path, params):
    """Ask Outlook a question. This only ever reads (GET), never changes."""
    url = f"{GRAPH_URL}{path}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise SetupProblem(
                "Outlook didn't accept the sign-in. Please run outlook_signin.py again."
            )
        raise SetupProblem(f"Outlook refused the request (code {error.code}).")
    except (urllib.error.URLError, OSError):
        raise SetupProblem("I couldn't reach Outlook. Check the internet connection.")


def find_leads_folder(token):
    """Look for a folder called Leads at the top level, then inside Inbox."""
    for path in ("/me/mailFolders", "/me/mailFolders/inbox/childFolders"):
        data = graph_get(token, path, {"$top": "100", "$select": "id,displayName"})
        for folder in data.get("value", []):
            if folder.get("displayName", "").strip().lower() == FOLDER_NAME:
                return folder["id"]
    raise SetupProblem(
        "I couldn't find a folder called Leads in your mailbox "
        "(I looked at the top level and inside Inbox)."
    )


def read_leads(token, limit):
    """Return a list of (sender, subject) for the newest emails in Leads."""
    folder_id = urllib.parse.quote(find_leads_folder(token), safe="")
    data = graph_get(
        token,
        f"/me/mailFolders/{folder_id}/messages",
        {
            "$top": str(limit),
            "$select": "subject,from",
            "$orderby": "receivedDateTime desc",
        },
    )
    leads = []
    for message in data.get("value", []):
        address = ((message.get("from") or {}).get("emailAddress")) or {}
        sender = address.get("address") or address.get("name") or "(unknown sender)"
        subject = message.get("subject") or "(no subject)"
        # One tidy line each, even if the subject has line breaks.
        leads.append((" ".join(sender.split()), " ".join(subject.split())))
    return leads


def post_to_slack(slack_url, text):
    """Send one message to Slack. Raises SlackProblem if it did not work."""
    # Slack treats & < > as special, so write them the safe way.
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    request = urllib.request.Request(
        slack_url,
        data=json.dumps({"text": safe}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError as error:
        raise SlackProblem(f"Slack refused the message (code {error.code}).")
    except (urllib.error.URLError, OSError):
        raise SlackProblem("I couldn't reach Slack.")
    except ValueError:
        # Never show the Slack link itself; it is a secret.
        raise SlackProblem("The Slack link doesn't look like a web address.")


def run(live, limit=DEFAULT_LIMIT):
    env_text, _ = read_env_text()
    client_id = get_client_id(env_text)
    # Check the Slack link first, so a live run never half-works.
    slack_url = get_slack_url(env_text) if live else None

    print("LIVE: posting to Slack #leads." if live else "Dry run: nothing will be posted to Slack.")
    token = get_access_token(env_text, client_id)
    leads = read_leads(token, limit)

    if not leads:
        print("No emails found in the Leads folder. Nothing to post.")
        return 0

    # What is printed names no sender and no subject, only a number and a count (the Slack post itself still has them).
    if not live:
        for number in range(1, len(leads) + 1):
            print(f"would post: message {number} of {len(leads)}")
        print(f"Dry run finished: {len(leads)} email(s) would be posted. Add --live to post.")
        return 0

    posted = 0
    for sender, subject in leads:
        try:
            post_to_slack(slack_url, f"{sender}, {subject}")
        except SlackProblem as problem:
            print(problem)
            print(
                f"Stopped. {posted} of {len(leads)} message(s) were posted. "
                "The emails are untouched, so the next run picks them up again."
            )
            return 1
        posted += 1
        print(f"posted: message {posted} of {len(leads)}")
    print(f"Done: {posted} message(s) posted to Slack #leads.")
    return 0


def limit_number(text):
    """Check the --limit value is a whole number from 1 to MAX_LIMIT."""
    message = f"--limit must be a whole number from 1 to {MAX_LIMIT}."
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(message)
    if not 1 <= number <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(message)
    return number


def main():
    # Stop odd characters in a subject from crashing the printout.
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Post the sender and subject of each Leads email to Slack."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="really post to Slack (without this it is a dry run)",
    )
    parser.add_argument(
        "--limit",
        type=limit_number,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"how many of the newest emails to read (1 to {MAX_LIMIT}, default {DEFAULT_LIMIT})",
    )
    args = parser.parse_args()

    try:
        return run(args.live, args.limit)
    except SetupProblem as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something unexpected went wrong ({type(unexpected).__name__}).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
