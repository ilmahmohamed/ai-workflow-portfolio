"""Make one reply draft in Outlook's Drafts folder for a lead, addressed only to the person who wrote to us.

The draft is never sent. It is created with Microsoft's "create reply" (it needs only the Mail.ReadWrite
permission) and carries only the finished text: the fixed line "AI draft - please review before sending",
the greeting and the body Gemini wrote (saved on the email, see saved_results.py), and the fixed sign-off, each
in its own paragraph (the text is sent as simple HTML, because Outlook would otherwise run plain lines together).
The code adds the first line and the sign-off, never Gemini. Right after it is created, the draft is read back and checked:
it is a draft, it is in Drafts, it has exactly one recipient (the original sender), no cc or bcc, and it
starts with the AI line. If any check fails, nothing is deleted or changed: the plain message says where the
draft may be, so a person can remove it by hand.

No draft is made when it would be wrong (see reply_skip_reason): the lead is not confirmed, the lead's
address is not the sender's (a forwarded enquiry), the email has a Reply-To that is somebody else, or the saved
draft is missing or breaks the draft rules. A person then writes the reply by hand.

A second draft is never made. A small marker (a hidden property on the email, fixed name and shape) is saved
once the draft is made, and an email that has it is skipped, even if the draft was deleted by hand later.
Before creating, Drafts is also asked whether it already holds a draft in the same conversation (for example
after a stop between creating the draft and saving the marker); if so, the marker is saved and nothing is
created. A failed read is never taken to mean "nothing there".

Every request goes through one guard (see graph) that allows only six things: read the Drafts folder id, read
Drafts for one conversation (ids only), read the marker by its fixed name, read the new draft with a few fixed
fields, create the reply with only the finished text, and add the marker. Anything else is refused before it
leaves the computer. This module prints nothing and opens no file, so no lead detail, draft text, address or
token can reach a log or a file, and no problem message contains one.
"""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from outlook_to_slack import GRAPH_URL, WEB_TIMEOUT_SECONDS
from v3_reply import AI_DRAFT_LINE, LEAD_CATEGORIES, SIGN_OFF, draft_problems

MARKER_NAME = "com.novawellness.draftmade"
EXTENSION_TYPE = "microsoft.graph.openTypeExtension"
VERSION = 1
DRAFT_SELECT = "id,isDraft,parentFolderId,toRecipients,ccRecipients,bccRecipients,conversationId,bodyPreview"
DRAFTS_FOLDER = "/me/mailFolders/drafts"
DRAFTS_MESSAGES = DRAFTS_FOLDER + "/messages"
CONVERSATION_LIMIT = "5"

ONE_MARKER = re.compile(r"^/me/messages/[^/]+/extensions/" + re.escape(MARKER_NAME) + r"$")
ADD_MARKER = re.compile(r"^/me/messages/[^/]+/extensions$")
ONE_DRAFT = re.compile(r"^/me/messages/[^/]+$")
CREATE_REPLY = re.compile(r"^/me/messages/[^/]+/createReply$")
IN_CONVERSATION = re.compile(r"^conversationId eq '[A-Za-z0-9_\-=+/]+'$")
CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_\-=+/]+$")
ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Outlook's "create reply" puts the comment into an HTML page, where plain line breaks collapse and everything
# runs together. So the finished text goes in as simple HTML: one paragraph each for the AI line, the greeting,
# the body and the sign-off, and a line break only inside the sign-off. No other HTML is ever used or allowed.
AI_LINE_HTML = "<p>" + html.escape(AI_DRAFT_LINE, quote=False) + "</p>"
SIGN_OFF_HTML = "<p>" + "<br>".join(html.escape(line, quote=False) for line in SIGN_OFF.split("\n")) + "</p>"
GREETING = re.compile(r"^(Hello(?:[ \t][^,\n]{1,40})?,)\s+(\S.*)$", re.S)
ALLOWED_TAGS = re.compile(r"</?p>|<br>")


class ReplyDraftProblem(Exception):
    """A problem with Outlook or with the new draft, in plain words (never a token, address or draft text)."""


class BlockedRequest(Exception):
    """The guard refused a request. This is a bug in the code, never something to retry."""


class _Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- The Outlook guard --------------------------------------------------------------------

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


def _is_finished_text(body):
    """Only the finished draft text and nothing else: no recipient, no message, no extra field.

    The text must be the finished HTML: it starts with the AI line, ends with the fixed sign-off, and holds no HTML
    except paragraphs and the one line break (anything else in Gemini's text was written safely, as plain text).
    """
    if not isinstance(body, dict) or set(body) != {"comment"}:
        return False
    text = body["comment"]
    return (
        isinstance(text, str)
        and text.startswith(AI_LINE_HTML + "\n")
        and text.endswith("\n" + SIGN_OFF_HTML)
        and "<" not in ALLOWED_TAGS.sub("", text)
        and ">" not in ALLOWED_TAGS.sub("", text)
    )


def _paragraph(text):
    """One paragraph: the text written safely, with a line break wherever the text has a single line break."""
    lines = [html.escape(line.strip(), quote=False) for line in text.strip().split("\n")]
    return "<p>" + "<br>".join(line for line in lines if line) + "</p>"


def finished_html(draft):
    """The finished draft as simple HTML: the AI line, the greeting, the body and the sign-off, one paragraph each.

    The greeting is the start of Gemini's text up to its first comma when the text starts with "Hello"; the body is
    the rest, with a capital first letter now that it starts a paragraph. A text with no such greeting stays
    together as one body, unchanged. Blank lines inside the body make more paragraphs.
    """
    text = draft.strip()
    pieces = [AI_LINE_HTML]
    found = GREETING.match(text)
    if found:
        pieces.append(_paragraph(found.group(1)))
        body = found.group(2)
        body = body[:1].upper() + body[1:]
    else:
        body = text
    pieces.extend(_paragraph(part) for part in re.split(r"\n\s*\n", body) if part.strip())
    pieces.append(SIGN_OFF_HTML)
    return "\n".join(pieces)


def check_allowed(method, path, params, body):
    """Refuse anything except the six requests this module is for."""
    if method == "GET":
        if path == DRAFTS_FOLDER and params == {"$select": "id"}:
            return
        if (
            path == DRAFTS_MESSAGES
            and isinstance(params, dict)
            and set(params) == {"$filter", "$select", "$top"}
            and params["$select"] == "id"
            and isinstance(params["$filter"], str)
            and IN_CONVERSATION.match(params["$filter"])
            and isinstance(params["$top"], str)
            and params["$top"].isdigit()
        ):
            return
        if ONE_DRAFT.match(path) and params == {"$select": DRAFT_SELECT}:
            return
        if ONE_MARKER.match(path) and not params:
            return
    if method == "POST" and not params:
        if CREATE_REPLY.match(path) and _is_finished_text(body):
            return
        if ADD_MARKER.match(path) and _is_marker(body):
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
        raise ReplyDraftProblem("I couldn't reach Outlook. Check the internet connection.")
    except ValueError:
        raise ReplyDraftProblem("Outlook gave an answer I could not read.")


def _outlook_problem(refused):
    if refused.code in (401, 403):
        return ReplyDraftProblem(
            "Outlook refused the request. Your sign-in may need redoing: run outlook_signin.py again."
        )
    return ReplyDraftProblem(f"Outlook refused the request (code {refused.code}).")


def _ask(token, method, path, params=None, body=None):
    """One request with plain-words errors (a refusal becomes a ReplyDraftProblem)."""
    try:
        return graph(token, method, path, params=params, body=body)
    except _Refused as refused:
        raise _outlook_problem(refused)


# ---- Reading the email and the saved answer -------------------------------------------------

def _clean_address(value):
    return str(value or "").strip().lower()


def _sender_address(message):
    return _clean_address(((message.get("from") or {}).get("emailAddress") or {}).get("address"))


def _reply_to_addresses(message):
    found = []
    for entry in message.get("replyTo") or []:
        found.append(_clean_address(((entry or {}).get("emailAddress") or {}).get("address")))
    return found


def reply_skip_reason(message, answer):
    """A plain reason why no draft would be made, or None when one would be. Sends no request.

    The reason never contains an address, a name or any draft text.
    """
    if not (
        isinstance(answer, dict)
        and answer.get("is_lead") is True
        and answer.get("category") in LEAD_CATEGORIES
    ):
        return "the answer is not a confirmed lead"
    sender = _sender_address(message)
    if not ADDRESS.match(sender):
        return "the email has no usable sender address"
    if _clean_address(answer.get("email")) != sender:
        return "the lead's address is not the email's sender (a forwarded enquiry?), so a reply would go to someone else"
    if any(address != sender for address in _reply_to_addresses(message)):
        return "the email has a Reply-To address that is not the sender, so a reply would go to someone else"
    draft = answer.get("draft_reply")
    if not isinstance(draft, str) or not draft.strip():
        return "the saved answer has no draft"
    problems = draft_problems(draft)
    if problems:
        return "the saved draft breaks the draft rules (it " + "; it ".join(problems) + ")"
    conversation = message.get("conversationId")
    if not isinstance(conversation, str) or not CONVERSATION_ID.match(conversation):
        return "the email has no usable conversation id to check Drafts with"
    return None


# ---- Outlook questions and changes -----------------------------------------------------------------

def _message_path(message_id):
    return "/me/messages/" + urllib.parse.quote(message_id, safe="")


def _marker_exists(token, message_id):
    try:
        graph(token, "GET", _message_path(message_id) + "/extensions/" + MARKER_NAME)
    except _Refused as refused:
        if refused.code == 404:
            return False
        raise _outlook_problem(refused)
    return True


def _draft_in_conversation(token, conversation_id):
    data = _ask(token, "GET", DRAFTS_MESSAGES, params={
        "$filter": f"conversationId eq '{conversation_id}'", "$select": "id", "$top": CONVERSATION_LIMIT,
    })
    found = data.get("value") if isinstance(data, dict) else None
    if not isinstance(found, list):
        raise ReplyDraftProblem("Outlook gave an answer I did not understand when I looked in Drafts. Nothing was created.")
    return bool(found)


def _save_marker(token, message_id):
    body = {"@odata.type": EXTENSION_TYPE, "extensionName": MARKER_NAME, "version": VERSION, "data": "{}"}
    _ask(token, "POST", _message_path(message_id) + "/extensions", body=body)


def _confirm_failed(reason):
    return ReplyDraftProblem(
        f"The reply draft was created, but the confirm step failed ({reason.rstrip('.')}). "
        "The draft may be in your Drafts folder: please look there and delete it by hand. "
        "Nothing was sent, and the email was not marked as done."
    )


def _draft_is_right(draft, sender, drafts_id):
    if not isinstance(draft, dict) or draft.get("isDraft") is not True:
        return False
    if not drafts_id or draft.get("parentFolderId") != drafts_id:
        return False
    to = draft.get("toRecipients")
    if not isinstance(to, list) or len(to) != 1:
        return False
    if _clean_address(((to[0] or {}).get("emailAddress") or {}).get("address")) != sender:
        return False
    if draft.get("ccRecipients") != [] or draft.get("bccRecipients") != []:
        return False
    return str(draft.get("bodyPreview") or "").lstrip().startswith(AI_DRAFT_LINE)


def _create_and_check(token, message, text):
    """Create the reply draft, read it back and check it. Returns nothing; raises ReplyDraftProblem."""
    created = _ask(token, "POST", _message_path(message["id"]) + "/createReply", body={"comment": text})
    draft_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(draft_id, str) or not draft_id:
        raise _confirm_failed("Outlook did not say which draft it made")
    try:
        draft = _ask(token, "GET", _message_path(draft_id), params={"$select": DRAFT_SELECT})
        folder = _ask(token, "GET", DRAFTS_FOLDER, params={"$select": "id"})
    except ReplyDraftProblem as problem:
        raise _confirm_failed(str(problem))
    drafts_id = folder.get("id") if isinstance(folder, dict) else None
    if not _draft_is_right(draft, _sender_address(message), drafts_id):
        raise _confirm_failed("the draft did not match what was expected")


def make_reply_draft(token, message, answer):
    """Make one reply draft for this lead unless it should not or was made before.

    Returns (status, detail): ("created", None), ("exists", None) or ("skipped", plain reason).
    Raises ReplyDraftProblem (plain words) if Outlook has a problem or the new draft does not check out.
    """
    reason = reply_skip_reason(message, answer)
    if reason:
        return "skipped", reason
    message_id = message["id"]
    found = _already_made(token, message)
    if found == "marker":
        return "exists", None
    if found == "drafts":
        _save_marker_after("A draft was already in Drafts", token, message_id)
        return "exists", None
    _create_and_check(token, message, finished_html(answer["draft_reply"]))
    _save_marker_after("The reply draft was created", token, message_id)
    return "created", None


def _already_made(token, message):
    """"marker" (the email has the marker), "drafts" (Drafts holds a draft in the same conversation) or None."""
    if _marker_exists(token, message["id"]):
        return "marker"
    if _draft_in_conversation(token, message["conversationId"]):
        return "drafts"
    return None


def draft_already_made(token, message):
    """True if a reply draft was already made for this email. Only reads: it never writes the marker."""
    return _already_made(token, message) is not None


def _save_marker_after(first_part, token, message_id):
    try:
        _save_marker(token, message_id)
    except ReplyDraftProblem as problem:
        raise ReplyDraftProblem(
            f"{first_part}, but I could not mark the email as done ({str(problem).rstrip('.')}). "
            "While that draft stays in Drafts, no second one is made."
        )
