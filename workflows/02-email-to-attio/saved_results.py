"""Save Gemini's full answer on the email itself, and read it back later.

The triage asks Gemini one question per email (prompt v3) and gets everything back. For a lead it
saves that answer on the email in Outlook, as a small hidden "open extension". The extension stays
on the email when the triage moves it to Leads, so the Attio step can read it there and never needs
to ask Gemini again. Nothing is kept in a local file, so this also works where local files are not
kept (GitHub Actions). A live check showed the saved data survives the move.

  save_result(token, message_id, result)  saves the answer. Returns True if it saved, False if an
                                          answer was already there (it never overwrites).
  load_result(token, message_id)          returns the saved answer, or None if nothing usable is saved.

Only the extracted fields and the draft are ever saved, never the email's own text.

This code can never send anything. Every request goes through one guard (see graph) that allows only
two kinds of request: read our one extension (by its fixed name, no query string) on one email, and add
one extension of that fixed name and shape to one email. Anything else is refused before it leaves the
computer. The sign-in permission is Mail.ReadWrite, which is not enough to send in any case.
It prints nothing and opens no file, so no lead detail or token can reach a log.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS

EXTENSION_NAME = "com.novawellness.leadresult"
EXTENSION_TYPE = "microsoft.graph.openTypeExtension"
VERSION = 1
MAX_DATA_CHARS = 8000

ALLOWED_FIELDS = (
    "is_lead", "category", "name", "email", "company", "role",
    "request_type", "urgency", "summary", "draft_reply",
)

ONE_EXTENSION = re.compile(r"^/me/messages/[^/]+/extensions/" + re.escape(EXTENSION_NAME) + r"$")
ADD_EXTENSION = re.compile(r"^/me/messages/[^/]+/extensions$")


class SavedResultProblem(Exception):
    """A problem with saving or reading an answer, in plain words."""


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the code, never something to retry."""


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- The guard ----------------------------------------------------------------------

def _is_our_extension(body):
    return (
        isinstance(body, dict)
        and set(body) == {"@odata.type", "extensionName", "version", "data"}
        and body["@odata.type"] == EXTENSION_TYPE
        and body["extensionName"] == EXTENSION_NAME
        and type(body["version"]) is int
        and body["version"] == VERSION
        and isinstance(body["data"], str)
    )


def check_allowed(method, path, params, body):
    """Refuse anything except the two kinds of request this code is for."""
    if method == "GET" and ONE_EXTENSION.match(path) and not params:
        return
    if method == "POST" and ADD_EXTENSION.match(path) and _is_our_extension(body):
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
        raise SavedResultProblem("I couldn't reach Outlook. Check the internet connection.")


def _problem_for(refused):
    if refused.code in (401, 403):
        return SavedResultProblem(
            "Outlook refused the request. Your sign-in may need redoing: run outlook_signin.py again."
        )
    return SavedResultProblem(f"Outlook refused the request (code {refused.code}).")


# ---- Reading and checking ---------------------------------------------------------------

def _extension_path(message_id):
    return "/me/messages/" + urllib.parse.quote(message_id, safe="") + "/extensions"


def _read_extension(token, message_id):
    """Our extension as Outlook holds it, or None if the email has none ("not found")."""
    try:
        return graph(token, "GET", _extension_path(message_id) + "/" + EXTENSION_NAME)
    except _Refused as refused:
        if refused.code == 404:
            return None
        raise _problem_for(refused)


def _usable_answer(answer):
    """True if this is an answer we could have saved: is_lead true or false, a text category, known fields."""
    return (
        isinstance(answer, dict)
        and isinstance(answer.get("is_lead"), bool)
        and isinstance(answer.get("category"), str)
        and set(answer) <= set(ALLOWED_FIELDS)
    )


def _answer_from(extension):
    """The answer inside an extension, or None if it is damaged, of another version, or not an answer."""
    if not isinstance(extension, dict):
        return None
    version = extension.get("version")
    if type(version) is not int or version != VERSION or not isinstance(extension.get("data"), str):
        return None
    try:
        answer = json.loads(extension["data"])
    except ValueError:
        return None
    return answer if _usable_answer(answer) else None


def load_result(token, message_id):
    """The answer saved on this email, or None if nothing usable is saved.

    A problem with Outlook (not "not found") is raised as SavedResultProblem, so an outage is never
    mistaken for "nothing saved".
    """
    return _answer_from(_read_extension(token, message_id))


# ---- Saving ----------------------------------------------------------------------------------

def _data_text(result):
    if not _usable_answer(result):
        raise SavedResultProblem(
            "The answer can't be saved: it needs is_lead (true or false), a category, and only the known fields."
        )
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_DATA_CHARS:
        raise SavedResultProblem(
            f"The answer can't be saved: it is too long (more than {MAX_DATA_CHARS} characters)."
        )
    return text


def save_result(token, message_id, result):
    """Save the answer on the email. True if saved, False if an answer was already there.

    Nothing is overwritten: the first saved answer is kept. The answer is checked before any request.
    """
    text = _data_text(result)
    if _read_extension(token, message_id) is not None:
        return False
    body = {
        "@odata.type": EXTENSION_TYPE,
        "extensionName": EXTENSION_NAME,
        "version": VERSION,
        "data": text,
    }
    try:
        graph(token, "POST", _extension_path(message_id), body=body)
    except _Refused as refused:
        raise _problem_for(refused)
    return True
