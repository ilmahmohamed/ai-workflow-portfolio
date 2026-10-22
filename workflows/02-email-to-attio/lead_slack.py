"""Post one short Slack message for a lead, once, and remember that it was posted.

When the triage accepts a lead it calls announce_lead. The message says only the category, the sender's
address and the subject (never the body, the draft or any detail of the lead). To post once per email, a small
marker (a second hidden property on the email, fixed name and shape) is saved after a successful post, and an
email that already has the marker is never posted again. The marker travels with the email when it is moved,
and it works where local files are not kept (GitHub Actions).

One small gap cannot be closed: Slack cannot be asked what it already holds, so if the program stops in the
moment between a successful post and saving the marker, a retry would post that lead again. When the marker
cannot be saved the plain message says so.

The Slack link is a secret: it is never printed, never put in a message and never saved. Only an address that
starts with https://hooks.slack.com/services/ is ever posted to. The Outlook side goes through one guard (see
graph) that allows only two requests: read the marker by its fixed name (no query string) and add the marker
(fixed name and shape). Nothing here can send, reply to, forward, move, copy or delete an email; the sign-in
permission is Mail.ReadWrite. It prints nothing and opens no file.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS

MARKER_NAME = "com.novawellness.slackposted"
EXTENSION_TYPE = "microsoft.graph.openTypeExtension"
VERSION = 1
SLACK_PREFIX = "https://hooks.slack.com/services/"
PAUSE_SECONDS = 1.1          # the triage waits this long between two posts, to stay inside Slack's rate limit
MAX_SUBJECT_CHARS = 150

ONE_MARKER = re.compile(r"^/me/messages/[^/]+/extensions/" + re.escape(MARKER_NAME) + r"$")
ADD_MARKER = re.compile(r"^/me/messages/[^/]+/extensions$")


class LeadSlackProblem(Exception):
    """A problem with Slack or the marker, in plain words (it never contains the Slack link or a lead detail)."""


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the code, never something to retry."""


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- The message ------------------------------------------------------------------

def _one_line(text):
    return " ".join(str(text or "").split())


def slack_text(category, sender, subject):
    """"category: sender, subject" on one line, safe for Slack, with a long subject shortened."""
    sender = _one_line(sender) or "(unknown sender)"
    subject = _one_line(subject) or "(no subject)"
    if len(subject) > MAX_SUBJECT_CHARS:
        subject = subject[: MAX_SUBJECT_CHARS - 3].rstrip() + "..."
    text = f"{category}: {sender}, {subject}"
    # Slack treats & < > as special, so they are written the safe way.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _sender_of(message):
    address = ((message.get("from") or {}).get("emailAddress")) or {}
    return address.get("address") or address.get("name") or ""


# ---- The Outlook guard (the marker only) ----------------------------------------------

def _is_marker(body):
    return (
        isinstance(body, dict)
        and set(body) == {"@odata.type", "extensionName", "version", "data"}
        and body["@odata.type"] == EXTENSION_TYPE
        and body["extensionName"] == MARKER_NAME
        and type(body["version"]) is int
        and body["version"] == VERSION
        and isinstance(body["data"], str)
    )


def check_allowed(method, path, params, body):
    """Refuse anything except reading the marker and adding the marker."""
    if method == "GET" and ONE_MARKER.match(path) and not params:
        return
    if method == "POST" and ADD_MARKER.match(path) and not params and _is_marker(body):
        return
    raise BlockedRequest(f"{method} {path} is not allowed here")


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
        raise _Refused(error.code)
    except (urllib.error.URLError, OSError):
        raise LeadSlackProblem("I couldn't reach Outlook. Check the internet connection.")


def _outlook_problem(refused):
    if refused.code in (401, 403):
        return LeadSlackProblem(
            "Outlook refused the request. Your sign-in may need redoing: run outlook_signin.py again."
        )
    return LeadSlackProblem(f"Outlook refused the request (code {refused.code}).")


def _marker_exists(token, message_id):
    path = "/me/messages/" + urllib.parse.quote(message_id, safe="") + "/extensions/" + MARKER_NAME
    try:
        graph(token, "GET", path)
    except _Refused as refused:
        if refused.code == 404:
            return False
        raise _outlook_problem(refused)
    return True


def _save_marker(token, message_id):
    body = {"@odata.type": EXTENSION_TYPE, "extensionName": MARKER_NAME, "version": VERSION, "data": "{}"}
    try:
        graph(token, "POST", "/me/messages/" + urllib.parse.quote(message_id, safe="") + "/extensions", body=body)
    except _Refused as refused:
        raise _outlook_problem(refused)


# ---- Slack -------------------------------------------------------------------------------------

def _check_link(webhook_url):
    """Only a Slack webhook address is ever posted to. The message never shows the address itself."""
    if not (
        isinstance(webhook_url, str)
        and webhook_url.startswith(SLACK_PREFIX)
        and len(webhook_url) > len(SLACK_PREFIX)
        and not any(ch.isspace() for ch in webhook_url)
    ):
        raise LeadSlackProblem(
            "The Slack link doesn't look like a Slack webhook address (it should start with "
            "https://hooks.slack.com/services/). Nothing was posted."
        )


def post_to_slack(webhook_url, text):
    """Post one message to Slack. Raises LeadSlackProblem (never showing the link) if it did not work."""
    _check_link(webhook_url)
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEB_TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError as error:
        raise LeadSlackProblem(f"Slack refused the message (code {error.code}).")
    except (urllib.error.URLError, OSError):
        raise LeadSlackProblem("I couldn't reach Slack.")
    except ValueError:
        raise LeadSlackProblem("The Slack link doesn't look like a web address. Nothing was posted.")


# ---- The step ------------------------------------------------------------------------------------

def announce_lead(token, message, category, webhook_url):
    """Post one message for this lead unless it was posted before. Returns "posted" or "exists"."""
    _check_link(webhook_url)
    message_id = message["id"]
    if _marker_exists(token, message_id):
        return "exists"
    post_to_slack(webhook_url, slack_text(category, _sender_of(message), message.get("subject")))
    try:
        _save_marker(token, message_id)
    except LeadSlackProblem:
        raise LeadSlackProblem(
            "The lead was posted to Slack, but I could not mark it as posted. "
            "If this email is tried again, it could be posted twice."
        )
    return "posted"
