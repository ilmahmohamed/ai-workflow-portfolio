"""Build the failure alert that the GitHub Actions workflow posts to #automation-log.

Usage (the workflow's alert step):  python alert_message.py "<failed step name>"
It prints ONE line of JSON, {"text": "..."}, which the workflow hands to Slack. The text has one item per line:

  Pipeline step failed: <step>.
  Reason: <one reason from a fixed list, or "Unknown error, see the run">
  Waiting: <how many emails are still waiting, and where>     (left out if it cannot be worked out)
  First waiting: <sender's name>, "<subject>"                  (left out if it cannot be worked out)
  Run: <link to the run>

The reason is found by looking for known phrases in the run's filtered log (the workflow keeps a copy of each
step's log in the runner's temporary folder, after the log filter has blanked every address). Only the fixed
reason is ever used: no part of the log is copied into the alert. The two "waiting" lines come from a read-only
look at the mailbox (see find_details): the Inbox after a failed Triage, the Leads folder after a failed Attio
sync. They hold a name and a subject only: never an address, never any body text. These details live only in the
Slack message: they are not printed, not logged and not saved, and anything the lookup prints is thrown away.

Whatever goes wrong, the alert still comes out: a missing log gives the unknown reason, a failed lookup leaves out
the two "waiting" lines, and main always ends successfully. A key, token, webhook link or address that is somehow
in the text is replaced by [removed]. The lookup only reads: it makes no change in Outlook and posts nothing.
"""

import contextlib
import datetime
import io
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "01-outlook-to-slack"))

from log_filter import clean as remove_addresses  # noqa: E402

UNKNOWN_REASON = "Unknown error, see the run"
MARK = "[removed]"
TOKEN = re.compile(r"\S+")
MAX_SUBJECT_CHARS = 100
MAX_NAME_CHARS = 60
MIN_SECRET_CHARS = 6        # a shorter value would wipe out ordinary words, so it is not used for cleaning

# The secrets the workflow knows by name (see .github/workflows/pipeline.yml).
SECRET_NAMES = (
    "GEMINI_API_KEY", "MS_CLIENT_ID", "MS_TOKEN_CACHE", "SLACK_WEBHOOK_URL", "SLACK_LOG_WEBHOOK_URL",
    "ATTIO_API_KEY", "ATTIO_DEAL_OWNER",
)

# The steps that can have handled some emails, and the log kept for each.
LOG_FILES = {"Triage": "triage.log", "Attio sync": "attio.log"}

# Fixed reasons for steps that fail before any email is looked at.
STEP_REASONS = {
    "Install libraries": "The libraries could not be installed",
    "set-up (checkout or Python)": "Set-up failed before the pipeline started",
}

# (phrases that mean it, the reason shown), checked in this order, ignoring capitals. The phrases are the plain
# sentences the scripts print. The first reason with a phrase found in the log wins, so the specific ones come first.
REASONS = (
    (("i can't find ",), "A required setting is missing"),
    (("saved sign-in", "didn't accept the sign-in", "sign-in may need redoing", "run outlook_signin.py"),
     "Outlook sign-in expired"),
    (("gemini did not accept the key",), "Gemini rejected the key"),
    (("gemini doesn't know the model",), "Gemini model name not found"),
    (("free limit was reached",), "Gemini's free limit was reached"),
    (("couldn't reach the ai service", "ai service kept failing"), "Gemini could not be reached"),
    (("gemini refused the request",), "Gemini refused the request"),
    (("attio did not accept the key",), "Attio rejected the key"),
    (("attio's request limit",), "Attio's request limit was reached"),
    (("couldn't reach attio",), "Attio could not be reached"),
    (("deal owner",), "Attio deal owner not found"),
    (("couldn't find the stage",), "Attio deal stage not found"),
    (("attio refused",), "Attio refused a record"),
    (("couldn't find a folder",), "An Outlook folder is missing"),
    (("couldn't reach outlook", "outlook could not be reached"), "Outlook could not be reached"),
    (("outlook refused",), "Outlook refused a request"),
    (("slack refused", "couldn't reach slack", "the slack link"), "Slack could not be posted to"),
)


# ---- The reason -----------------------------------------------------------------------------

def reason_from_log(log_text):
    """One reason from the fixed list, or the unknown reason. The log text itself is never used in the answer."""
    if not isinstance(log_text, str):
        return UNKNOWN_REASON
    lowered = log_text.lower()
    for phrases, reason in REASONS:
        if any(phrase in lowered for phrase in phrases):
            return reason
    return UNKNOWN_REASON


def reason_for(step, log_text):
    """The reason for a failed step: a fixed one for install and set-up, otherwise read from the step's log."""
    if step in STEP_REASONS:
        return STEP_REASONS[step]
    return reason_from_log(log_text)


# ---- What is still waiting --------------------------------------------------------------------

def _name_and_subject(message):
    sender = ((message.get("from") or {}).get("emailAddress")) or {}
    return (sender.get("name") or ""), (message.get("subject") or "")


def waiting_for_triage(messages, now):
    """(how many Inbox emails are not handled yet, (name, subject) of the oldest of them or None)."""
    import outlook_triage

    since = now - datetime.timedelta(days=outlook_triage.LOOKBACK_DAYS)
    eligible, _skipped, _system = outlook_triage.choose_emails(messages, {"emails": {}}, since, None)
    if not eligible:
        return 0, None
    return len(eligible), _name_and_subject(eligible[0][0])


def waiting_in_leads(messages):
    """(how many emails wait in Leads, (name, subject) of the oldest of them or None). System emails do not count."""
    import leads_to_attio

    empty = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    ordered = sorted(
        (m for m in messages if not leads_to_attio.is_system_email(m)),
        key=lambda m: (leads_to_attio.parse_time(m.get("receivedDateTime")) or empty, m.get("id", "")),
    )
    if not ordered:
        return 0, None
    return len(ordered), _name_and_subject(ordered[0])


def _sign_in_settings():
    from outlook_signin import get_client_id, read_env_text

    env_text, _ = read_env_text()
    return env_text, get_client_id(env_text)


def find_details(step):
    """(waiting, first) for a failed step, from a read-only look at the mailbox. Raises if it cannot be done.

    waiting is (count, "the Inbox" or "Leads") and first is (name, subject) or None. Steps that cannot have left
    emails waiting give (None, None).
    """
    if step not in LOG_FILES:
        return None, None
    import leads_to_attio
    import outlook_to_slack
    import outlook_triage

    env_text, client_id = _sign_in_settings()
    token = outlook_to_slack.get_access_token(env_text, client_id)
    if step == "Triage":
        now = outlook_triage.now()
        since = now - datetime.timedelta(days=outlook_triage.LOOKBACK_DAYS)
        count, first = waiting_for_triage(outlook_triage.list_inbox(token, since), now)
        return (count, "the Inbox"), first
    leads_id = leads_to_attio.find_leads_folder(token)
    count, first = waiting_in_leads(leads_to_attio.list_leads(token, leads_id))
    return (count, "Leads"), first


# ---- The text ---------------------------------------------------------------------------------

def secret_values(env):
    """The values of the known secrets that are set in this environment (long enough to be worth cleaning)."""
    found = []
    for name in SECRET_NAMES:
        value = (env or {}).get(name)
        if isinstance(value, str) and len(value.strip()) >= MIN_SECRET_CHARS:
            found.append(value.strip())
    return found


def _one_line(text):
    return " ".join(str(text or "").split())


def _scrub(text, secrets):
    """Replace an address, a Slack link or a secret value by [removed]."""
    text = remove_addresses(text)
    text = TOKEN.sub(lambda match: MARK if "hooks.slack.com" in match.group(0) else match.group(0), text)
    for secret in secrets or ():
        if isinstance(secret, str) and len(secret.strip()) >= MIN_SECRET_CHARS:
            text = text.replace(secret.strip(), MARK)
    return text


def _slack_safe(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(text, limit):
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _waiting_line(waiting):
    if (isinstance(waiting, tuple) and len(waiting) == 2 and type(waiting[0]) is int and waiting[0] >= 0
            and isinstance(waiting[1], str) and waiting[1]):
        return f"Waiting: {waiting[0]} in {waiting[1]}"
    return None


def _first_line(first, secrets):
    if not (isinstance(first, tuple) and len(first) == 2):
        return None
    name, subject = (_cut(_scrub(_one_line(part), secrets), limit) for part, limit in
                     zip(first, (MAX_NAME_CHARS, MAX_SUBJECT_CHARS)))
    return f'First waiting: {_slack_safe(name or "(no name)")}, "{_slack_safe(subject or "(no subject)")}"'


def build_text(step, reason, run_url=None, waiting=None, first=None, secrets=()):
    """The alert, one item per line. Lines without a usable detail are left out."""
    lines = [f"Pipeline step failed: {_one_line(step)}.", f"Reason: {reason}"]
    for line in (_waiting_line(waiting), _first_line(first, secrets)):
        if line:
            lines.append(line)
    if run_url:
        lines.append(f"Run: {_one_line(run_url)}")
    return _scrub("\n".join(lines), secrets)


# ---- The step -----------------------------------------------------------------------------------

def _read_log(step, env):
    name = LOG_FILES.get(step)
    folder = (env or {}).get("RUNNER_TEMP")
    if not name or not folder:
        return ""
    try:
        return (Path(folder) / name).read_bytes().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _details(step, env, lookup):
    """(waiting, first) or (None, None). Anything the lookup prints or raises is thrown away."""
    if step not in LOG_FILES:
        return None, None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            found = lookup(step)
        waiting, first = found
        return waiting, first
    except Exception:
        return None, None


def main(argv=None, env=None, stdout=None, details=None):
    """Print the alert as one line of JSON. Always returns 0, so the alert can never be the thing that fails."""
    out = stdout if stdout is not None else sys.stdout
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        environment = os.environ if env is None else env
        step = args[0] if args else "(unknown step)"
        lookup = details if details is not None else find_details
        reason = reason_for(step, _read_log(step, environment))
        waiting, first = _details(step, environment, lookup)
        text = build_text(step, reason, environment.get("RUN_URL"), waiting, first, secret_values(environment))
    except Exception:
        text = "Pipeline step failed. Reason: " + UNKNOWN_REASON
    out.write(json.dumps({"text": text}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
