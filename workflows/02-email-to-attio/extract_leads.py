"""Ask Gemini to pull lead details out of the 30 test emails, then score it.

Reads tests/cases/lead-emails.md, sends each email to the model named in
config.json together with a prompt from prompts/, and compares the answers
with tests/cases/expected-results.json. Each live run adds one row to
accuracy.md.

Dry run by default: shows what would be sent and makes no calls.

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
REQUEST_TIMEOUT = 60
PAUSE_SECONDS = 6          # between emails, to stay inside the free per-minute limit
RETRY_WAIT_SECONDS = 30    # wait before the one retry after "too many requests"

# Scored for every email, then for real leads only. Summary is never scored.
LEAD_FIELDS = ["name", "email", "company", "role", "request_type", "urgency"]


class SetupProblem(Exception):
    """A problem we can explain to the user in plain words."""


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

def parse_emails(text):
    """Split lead-emails.md into [{"id": 1, "text": "From: ..."}, ...]."""
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


def load_test_data():
    try:
        emails = parse_emails(EMAILS_PATH.read_text(encoding="utf-8"))
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SetupProblem("I couldn't read the test emails or the answer file. Nothing was changed.")
    if sorted(e["id"] for e in emails) != sorted(e["id"] for e in expected):
        raise SetupProblem("The emails file and the answer file don't list the same emails.")
    return emails, expected


def build_message(prompt, email_text):
    return f"{prompt.strip()}\n\nHere is the email:\n\n{email_text}"


# ---- Talking to Gemini ------------------------------------------------------

def call_once(model, key, message):
    """One request. Returns the model's text, or raises SetupProblem / RateLimited."""
    try:
        response = requests.post(
            API_URL.format(model=model),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": message}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        raise SetupProblem("I couldn't reach the AI service. Check your internet and try again.")

    code = response.status_code
    if code == 200:
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return ""  # no usable answer, so it will count as a broken reply
    if code == 429:
        raise RateLimited()
    if code in (401, 403) or (code == 400 and "api key" in response.text.lower()):
        raise SetupProblem("Gemini did not accept the key. Check it and try again.")
    if code == 404:
        raise SetupProblem(
            f"Gemini doesn't know the model name {model}. Check the name in config.json."
        )
    if code >= 500:
        raise SetupProblem("I couldn't reach the AI service. Check your internet and try again.")
    raise SetupProblem(f"Gemini refused the request (code {code}). Nothing was saved.")


class RateLimited(Exception):
    pass


def ask_gemini(model, key, message):
    """Ask once; if the free limit is hit, wait and try one more time."""
    try:
        return call_once(model, key, message)
    except RateLimited:
        time.sleep(RETRY_WAIT_SECONDS)
    try:
        return call_once(model, key, message)
    except RateLimited:
        raise SetupProblem(
            "The free limit was reached, so I stopped. Nothing was saved. Please try again later."
        )


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


def save_score(prompt_version, model, result):
    if not ACCURACY_PATH.exists():
        ACCURACY_PATH.write_text(
            "# Accuracy by prompt version\n\n"
            "One row per live run. Accuracy = correct fields divided by all scored fields.\n\n"
            "| Date | Prompt | Model | Accuracy | Broken replies | Per field |\n"
            "|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    per_field = "; ".join(f"{f} {c}/{t}" for f, (c, t) in result["per_field"].items())
    row = (
        f"| {datetime.date.today().isoformat()} | {prompt_version} | {model} "
        f"| {percent(result['accuracy'])} ({result['correct']}/{result['total']}) "
        f"| {result['broken']} | {per_field} |\n"
    )
    with ACCURACY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(row)


def save_replies(prompt_version, model, result, replies):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    path = RESULTS_DIR / f"{stamp}_{prompt_version}_{safe_model}.json"
    path.write_text(
        json.dumps(
            {
                "prompt": prompt_version,
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

def dry_run(prompt_version, model, prompt, emails):
    print("Dry run: nothing will be sent. Add --live to make real AI calls.")
    print(f"Prompt version: {prompt_version}")
    print(f"Model: {model}")
    print(f"Found {len(emails)} emails.")
    if emails:
        first = emails[0]
        print(f"\nThis is what would be sent for email {first['id']} (the first one):\n")
        print("would send:")
        print(build_message(prompt, first["text"]))
    return 0


def live_run(prompt_version, model, prompt, emails, expected):
    key = get_api_key()
    print(f"Live run: {len(emails)} emails, prompt {prompt_version}, model {model}.")
    answers = {}
    replies = []
    for position, email in enumerate(emails):
        if position:
            time.sleep(PAUSE_SECONDS)
        raw = ask_gemini(model, key, build_message(prompt, email["text"]))
        parsed = parse_reply(raw)
        answers[email["id"]] = parsed
        replies.append({"id": email["id"], "raw_reply": raw, "parsed": parsed})
        print(f"Email {email['id']}: {'reply read' if parsed is not None else 'BROKEN reply'}")

    result = score_results(expected, answers)
    save_score(prompt_version, model, result)
    saved = save_replies(prompt_version, model, result, replies)

    print()
    print(
        f"Accuracy: {percent(result['accuracy'])} "
        f"({result['correct']} of {result['total']} fields correct)"
    )
    print(f"Broken replies: {result['broken']}")
    for field, (correct, total) in result["per_field"].items():
        print(f"  {field}: {correct} of {total}")
    print(f"Score added to {ACCURACY_PATH.name}. Replies saved in {saved.parent.name}\\{saved.name}.")
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Score Gemini's lead extraction on the test emails.")
    parser.add_argument("--live", action="store_true", help="really call Gemini (default is a dry run)")
    parser.add_argument("--prompt", default="v1", help="prompt version to use, like v1 (default v1)")
    args = parser.parse_args()

    try:
        model = load_model()
        prompt = load_prompt(args.prompt)
        emails, expected = load_test_data()
        if not args.live:
            return dry_run(args.prompt, model, prompt, emails)
        return live_run(args.prompt, model, prompt, emails, expected)
    except SetupProblem as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something unexpected went wrong ({type(unexpected).__name__}). Nothing was saved.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
