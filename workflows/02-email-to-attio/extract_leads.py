"""Ask Gemini to pull lead details out of the 30 test emails, then score it.

Reads tests/cases/lead-emails.md, sends each email to the model named in
config.json together with a prompt from prompts/, and compares the answers
with tests/cases/expected-results.json. Each live run adds one row to
accuracy.md.

Dry run by default: shows what would be sent and makes no calls.

Options:
  --live            really call Gemini
  --prompt v2       use prompts/v2.md (default v1)
  --emails FILE --answers FILE
                    use another email file and answer key (give both)
  --only ID         send just that one email and show how it matched the key

Run:  python workflows\\02-email-to-attio\\extract_leads.py
      python workflows\\02-email-to-attio\\extract_leads.py --live --prompt v1
Needs: GEMINI_API_KEY (in .env or set in the terminal) for --live only.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

import v3_reply

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]

ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH = HERE / "config.json"
PROMPTS_DIR = HERE / "prompts"
RESULTS_DIR = HERE / "results"
ACCURACY_PATH = HERE / "accuracy.md"
EMAILS_PATH = PROJECT_ROOT / "tests" / "cases" / "lead-emails.md"
EXPECTED_PATH = PROJECT_ROOT / "tests" / "cases" / "expected-results.json"

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEY_NAME = "GEMINI_API_KEY"
REQUEST_TIMEOUT = 120      # seconds Gemini gets to answer one request (60 was too short on GitHub Actions)
PAUSE_SECONDS = 6          # between emails, to stay inside the free per-minute limit
RETRY_WAIT_SECONDS = 30    # wait before the one retry after "too many requests"
BUSY_WAIT_SECONDS = 10     # wait before the one retry after a temporary 503, a timeout or a lost connection
SAMPLE_NOTE = f"up to {v3_reply.SAMPLE_LIMIT}, spread across the leads"
GENERATION_SEED = 42       # a fixed seed, to keep answers as repeatable as the API allows
_seed_rejected = False     # set for the rest of the run if Gemini does not accept the seed setting

# Scored for every email, then for real leads only. Summary is never scored.
LEAD_FIELDS = ["name", "email", "company", "role", "request_type", "urgency"]


class SetupProblem(Exception):
    """A problem we can explain to the user in plain words."""


class GeminiUnreachable(SetupProblem):
    """Gemini could not be reached, or answered with a server error, even after any retry.

    The key and the model are fine, so a caller that handles many emails may leave this one alone and carry on.
    Every other SetupProblem (a rejected key, an unknown model, the free limit) is a reason to stop at once.
    """


class GeminiNoAnswer(GeminiUnreachable):
    """No answer came back at all (a timeout or a lost connection): worth one retry."""


# ---- Settings ---------------------------------------------------------------

def read_env_value(name):
    """The terminal's value wins; otherwise look in .env. Returns "" if not found."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        text = ENV_PATH.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, found = line.split("=", 1)
        if key.strip() == name:
            return found.strip().strip("'\"")
    return ""


def get_api_key():
    key = read_env_value(KEY_NAME)
    if not key:
        raise SetupProblem(f"I can't find {KEY_NAME}. Add it to your .env file and try again.")
    return key


def load_model():
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        model = config["model"]
        if not isinstance(model, str) or not model.strip():
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise SetupProblem(
            "I can't find a model name in config.json. "
            'Add a line like {"model": "gemini-3.5-flash-lite"} and try again.'
        )
    return model.strip()


def load_prompt(version):
    path = PROMPTS_DIR / f"{version}.md"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", version) or not path.is_file():
        raise SetupProblem(f"I couldn't find the prompt file {version}.md in the prompts folder.")
    return path.read_text(encoding="utf-8")


# ---- Test data --------------------------------------------------------------

def resolve_path(text):
    """Use the path as typed; if it isn't there, try it from the project folder."""
    path = Path(text)
    if path.is_file():
        return path
    from_project = PROJECT_ROOT / text
    if from_project.is_file():
        return from_project
    raise SetupProblem(f"I couldn't find the file {text}.")


def parse_emails(text):
    """Split an email file into [{"id": 1, "text": "From: ..."}, ...]."""
    heading = re.compile(r"^## Email (\d+)[ \t]*$", re.MULTILINE)
    matches = list(heading.finditer(text))
    emails = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        lines = []
        for line in text[match.end():end].splitlines():
            if line.strip() == "-----":
                break
            lines.append(line)
        emails.append({"id": int(match.group(1)), "text": "\n".join(lines).strip()})
    return emails


def load_test_data(emails_path=None, answers_path=None):
    """The 30 test emails and their key, unless other files are given."""
    try:
        emails = parse_emails((emails_path or EMAILS_PATH).read_text(encoding="utf-8"))
        expected = json.loads((answers_path or EXPECTED_PATH).read_text(encoding="utf-8"))
        email_ids = sorted(e["id"] for e in emails)
        expected_ids = sorted(e["id"] for e in expected)
        [e["is_lead"] for e in expected]
    except (OSError, ValueError, KeyError, TypeError):
        raise SetupProblem("I couldn't read the test emails or the answer file. Nothing was changed.")
    if email_ids != expected_ids:
        raise SetupProblem("The emails file and the answer file don't list the same emails.")
    return emails, expected


def build_message(prompt, email_text):
    return f"{prompt.strip()}\n\nHere is the email:\n\n{email_text}"


def count_emails(number):
    return f"{number} email" + ("" if number == 1 else "s")


# ---- Talking to Gemini ------------------------------------------------------

def unreachable(reason, no_answer=False):
    """The plain message for "couldn't reach Gemini", with a safe hint about why.

    The reason is always one of our own short phrases or a status number, never
    the error text from the failure, so it can't contain a key or email text.
    """
    kind = GeminiNoAnswer if no_answer else GeminiUnreachable
    return kind(
        f"I couldn't reach the AI service ({reason}). Check your internet and try again."
    )


def generation_config():
    """The settings that keep Gemini's answers as steady as the API allows."""
    config = {"temperature": 0, "responseMimeType": "application/json"}
    if not _seed_rejected:
        config["seed"] = GENERATION_SEED
    return config


def send_request(model, key, message):
    """Send one request to Gemini. Raises a plain-words SetupProblem if it can't be reached."""
    try:
        return requests.post(
            API_URL.format(model=model),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": message}]}],
                "generationConfig": generation_config(),
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        raise unreachable("the request timed out", no_answer=True)
    except requests.exceptions.ConnectionError:
        raise unreachable("no connection", no_answer=True)
    except requests.exceptions.RequestException:
        raise unreachable("a connection problem", no_answer=True)


def call_once(model, key, message):
    """One request. Returns the model's text, or raises SetupProblem / RateLimited."""
    global _seed_rejected
    response = send_request(model, key, message)
    if response.status_code == 400 and not _seed_rejected and "seed" in response.text.lower():
        # This model does not know the seed setting: carry on without it, for the rest of the run.
        _seed_rejected = True
        response = send_request(model, key, message)

    code = response.status_code
    if code == 200:
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return ""  # no usable answer, so it will count as a broken reply
    if code == 429:
        raise RateLimited()
    if code == 503:
        raise ServiceBusy()
    if code in (401, 403) or (code == 400 and "api key" in response.text.lower()):
        raise SetupProblem("Gemini did not accept the key. Check it and try again.")
    if code == 404:
        raise SetupProblem(
            f"Gemini doesn't know the model name {model}. Check the name in config.json."
        )
    if code >= 500:
        raise unreachable(f"Gemini answered with code {code}")
    raise SetupProblem(f"Gemini refused the request (code {code}). Nothing was saved.")


class RateLimited(Exception):
    pass


class ServiceBusy(Exception):
    """Gemini answered 503: temporarily busy."""


def ask_gemini(model, key, message):
    """Ask once; if the free limit is hit, Gemini is busy (503) or no answer comes back (a timeout or a lost
    connection), wait and try one more time.

    There is only one retry in total, whichever problem came first. A rejected key, an unknown model and other
    server errors (500, 502, 504) are not retried.
    """
    try:
        return call_once(model, key, message)
    except RateLimited:
        time.sleep(RETRY_WAIT_SECONDS)
    except (ServiceBusy, GeminiNoAnswer):
        time.sleep(BUSY_WAIT_SECONDS)
    try:
        return call_once(model, key, message)
    except RateLimited:
        raise SetupProblem(
            "The free limit was reached, so I stopped. Nothing was saved. Please try again later."
        )
    except ServiceBusy:
        raise unreachable("Gemini answered with code 503")


def parse_reply(raw):
    """Turn the model's text into a dict, or None if it is broken."""
    text = (raw or "").strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


# ---- Scoring ----------------------------------------------------------------

def same(expected, actual):
    """Capitals and spaces at the ends are ignored; the type must match too."""
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower()
    return type(expected) is type(actual) and expected == actual


def score_results(expected_list, answers):
    """answers maps id -> the model's dict, or None if its reply was broken."""
    fields = ["is_lead"] + LEAD_FIELDS
    counts = {field: [0, 0] for field in fields}
    broken = 0
    for entry in expected_list:
        answer = answers.get(entry["id"])
        if answer is None:
            broken += 1
        scored = ["is_lead"] + (LEAD_FIELDS if entry["is_lead"] else [])
        for field in scored:
            counts[field][1] += 1
            if isinstance(answer, dict) and field in answer and same(entry[field], answer[field]):
                counts[field][0] += 1
    correct = sum(c for c, _ in counts.values())
    total = sum(t for _, t in counts.values())
    return {
        "correct": correct,
        "total": total,
        "accuracy": correct / total if total else 0.0,
        "per_field": {field: tuple(pair) for field, pair in counts.items()},
        "broken": broken,
    }


# ---- Saving -----------------------------------------------------------------

def percent(value):
    return f"{value * 100:.1f}%"


def save_score(prompt_label, model, result):
    if not ACCURACY_PATH.exists():
        ACCURACY_PATH.write_text(
            "# Accuracy by prompt version\n\n"
            "One row per live run. Accuracy = correct fields divided by all scored fields.\n\n"
            "| Date | Prompt | Model | Accuracy | Broken replies | Per field |\n"
            "|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    per_field = "; ".join(f"{f} {c}/{t}" for f, (c, t) in result["per_field"].items())
    if result.get("extra"):
        per_field += "; " + "; ".join(result["extra"])
    row = (
        f"| {datetime.date.today().isoformat()} | {prompt_label} | {model} "
        f"| {percent(result['accuracy'])} ({result['correct']}/{result['total']}) "
        f"| {result['broken']} | {per_field} |\n"
    )
    with ACCURACY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(row)


def save_replies(prompt_version, model, result, replies, set_name=None):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    safe_set = re.sub(r"[^A-Za-z0-9._-]", "_", set_name) if set_name else ""
    parts = [stamp, prompt_version] + ([safe_set] if safe_set else []) + [safe_model]
    path = RESULTS_DIR / ("_".join(parts) + ".json")
    path.write_text(
        json.dumps(
            {
                "prompt": prompt_version,
                "email_set": set_name,
                "model": model,
                "accuracy": result["accuracy"],
                "replies": replies,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


# ---- The run ----------------------------------------------------------------

def dry_run(prompt_version, model, prompt, emails, set_name=None):
    print("Dry run: nothing will be sent. Add --live to make real AI calls.")
    print(f"Prompt version: {prompt_version}")
    print(f"Model: {model}")
    if set_name:
        print(f"Email set: {set_name}")
    print(f"Found {count_emails(len(emails))}.")
    if emails:
        first = emails[0]
        which = "the only one" if len(emails) == 1 else "the first one"
        print(f"\nThis is what would be sent for email {first['id']} ({which}):\n")
        print("would send:")
        print(build_message(prompt, first["text"]))
    return 0


def show_single(entry, answer, raw):
    """Print one email's result field by field. Nothing is saved."""
    print(f"\nEmail {entry['id']} only (nothing is saved).")
    if answer is None:
        print("  Gemini's reply was broken (not valid JSON), so every field is wrong.")
        print(f"  Gemini said: {(raw or '')[:300]!r}")
    else:
        scored = ["is_lead"] + (LEAD_FIELDS if entry["is_lead"] else [])
        for field in scored:
            key_text = json.dumps(entry[field], ensure_ascii=False)
            if field in answer:
                gemini_text = json.dumps(answer[field], ensure_ascii=False)
                verdict = "match" if same(entry[field], answer[field]) else "WRONG"
            else:
                gemini_text, verdict = "(missing)", "WRONG"
            print(f"  {field}: key {key_text}, Gemini {gemini_text}: {verdict}")
    result = score_results([entry], {entry["id"]: answer})
    print(f"Result: {result['correct']} of {result['total']} fields match the key.")


def live_run(prompt_version, model, prompt, emails, expected, set_name=None, only=None):
    key = get_api_key()
    print(f"Live run: {count_emails(len(emails))}, prompt {prompt_version}, model {model}.")
    if set_name:
        print(f"Email set: {set_name}")
    answers = {}
    replies = []
    raw_by_id = {}
    for position, email in enumerate(emails):
        if position:
            time.sleep(PAUSE_SECONDS)
        raw = ask_gemini(model, key, build_message(prompt, email["text"]))
        parsed = parse_reply(raw)
        answers[email["id"]] = parsed
        raw_by_id[email["id"]] = raw
        replies.append({"id": email["id"], "raw_reply": raw, "parsed": parsed})
        if only is None:
            print(f"Email {email['id']}: {'reply read' if parsed is not None else 'BROKEN reply'}")

    if only is not None:
        entry = next(e for e in expected if e["id"] == only)
        show_single(entry, answers[only], raw_by_id[only])
        return 0

    result = score_results(expected, answers)
    # A prompt that asks for a draft reply is a v3-style prompt: check the category and the drafts too.
    report = None
    if '"draft_reply"' in prompt:
        report = v3_reply.v3_report(expected, answers)
        correct_categories, total_categories = report["category"]
        result["extra"] = [
            f"category {correct_categories}/{total_categories}",
            f"draft violations {len(report['violations'])}",
        ]
        for reply in replies:
            read, problems = v3_reply.read_v3(reply["parsed"])
            reply["v3_result"] = read
            reply["v3_problems"] = problems
    label = f"{prompt_version} [{set_name}]" if set_name else prompt_version
    save_score(label, model, result)
    saved = save_replies(prompt_version, model, result, replies, set_name)

    print()
    print(
        f"Accuracy: {percent(result['accuracy'])} "
        f"({result['correct']} of {result['total']} fields correct)"
    )
    print(f"Broken replies: {result['broken']}")
    for field, (correct, total) in result["per_field"].items():
        print(f"  {field}: {correct} of {total}")
    if report is not None:
        print_v3_report(report)
    print(f"Score added to {ACCURACY_PATH.name}. Replies saved in {saved.parent.name}\\{saved.name}.")
    return 0


def print_v3_report(report):
    """The extra lines for a v3 run: category score, draft rule violations and a sample to read."""
    correct, total = report["category"]
    print(f"Category: {correct} of {total} correct ({percent(correct / total if total else 0.0)})")
    for email_id, problems in report["category_problems"]:
        print(f"  Email {email_id}: {'; '.join(problems)}")
    violations = report["violations"]
    if violations:
        noun = "draft" if len(violations) == 1 else "drafts"
        print(f"Draft rules: {len(violations)} {noun} broke a rule:")
        for email_id, problems in violations:
            print(f"  Email {email_id}: {'; '.join(problems)}")
    else:
        count = report["drafts_ok"]
        print(f"Draft rules: 0 violations in {count} {'draft' if count == 1 else 'drafts'}.")
    if report["drafts_missing"]:
        print(f"Leads with no draft at all: {report['drafts_missing']}")
    if report["samples"]:
        print(f"\nSample drafts ({SAMPLE_NOTE}):")
        for email_id, category, text in report["samples"]:
            print(f"  Email {email_id} ({category}):")
            for line in text.splitlines():
                print(f"    {line}" if line else "")
    print()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Score Gemini's lead extraction on the test emails.")
    parser.add_argument("--live", action="store_true", help="really call Gemini (default is a dry run)")
    parser.add_argument("--prompt", default="v1", help="prompt version to use, like v1 (default v1)")
    parser.add_argument("--emails", metavar="FILE", help="another email file (use with --answers)")
    parser.add_argument("--answers", metavar="FILE", help="the answer key for that email file")
    parser.add_argument("--only", type=int, metavar="ID", help="send only this one email and show how it matched")
    args = parser.parse_args()

    try:
        if bool(args.emails) != bool(args.answers):
            raise SetupProblem(
                "Please use --emails and --answers together, "
                "because another email file needs its own answer key."
            )
        model = load_model()
        prompt = load_prompt(args.prompt)
        emails_path = resolve_path(args.emails) if args.emails else None
        answers_path = resolve_path(args.answers) if args.answers else None
        emails, expected = load_test_data(emails_path, answers_path)
        set_name = Path(args.emails).stem if args.emails else None
        if args.only is not None:
            emails = [e for e in emails if e["id"] == args.only]
            if not emails:
                raise SetupProblem(f"I couldn't find email {args.only} in the emails file.")
        if not args.live:
            return dry_run(args.prompt, model, prompt, emails, set_name)
        return live_run(args.prompt, model, prompt, emails, expected, set_name, args.only)
    except SetupProblem as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something unexpected went wrong ({type(unexpected).__name__}). Nothing was saved.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
