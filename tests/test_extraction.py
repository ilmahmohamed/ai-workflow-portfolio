"""Pretend-data checks for workflows/02-email-to-attio/extract_leads.py.

These follow tests/cases/02-extraction.md (which is not changed).
Everything here is fake and self-contained:
  - a pretend Gemini answers in place of the real one,
  - the internet is blocked, so nothing can leave this computer,
  - .env is swapped for a throwaway file, and any attempt to touch the real
    .env stops the test with an error (the real file is never opened),
  - scores, results and prompts are written to a throwaway folder, never to
    the real accuracy.md or the real prompts folder.

Run:  python tests\\test_extraction.py
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "02-email-to-attio"))

import extract_leads  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()
REAL_EMAILS = PROJECT_ROOT / "tests" / "cases" / "lead-emails.md"
REAL_EXPECTED = PROJECT_ROOT / "tests" / "cases" / "expected-results.json"

FAKE_KEY = "FAKE-GEMINI-KEY-SECRET"
FAKE_FILE_KEY = "FAKE-GEMINI-KEY-FROM-FILE-SECRET"
FAKE_ENV = {"GEMINI_API_KEY": FAKE_KEY}
SECRETS = (FAKE_KEY, FAKE_FILE_KEY)
ANSWER_KEY_MARKER = "SECRET-ANSWER-KEY-MARKER"

# Three small emails taken from the real set (ids 9, 5 and 4), with their answers.
TINY_EMAILS = """# Tiny test set

-----

## Email 9
From: Jonas Meyer <jonas.meyer@example.com>
Subject: Pricing request
Body:
Hello, could you send me your pricing for a small studio? Thanks, Jonas Meyer, Blue Reed Yoga

-----

## Email 5
From: Hannah Brooks <hannah.brooks@example.com>
Subject: Automatic reply: Re: Nova Wellness intro
Body:
I am out of the office until Tuesday 3 November with limited access to email.

-----

## Email 4
From: Darius Kowalski <darius.kowalski@example.com>
Subject: NEED A DEMO THIS WEEK!!!
Body:
OUR BOOKING SYSTEM STOPS WORKING ON FRIDAY. THIS IS URGENT. DARIUS KOWALSKI, OWNER, IRONWORKS GYM
"""

JONAS = {
    "is_lead": True,
    "name": "Jonas Meyer",
    "email": "jonas.meyer@example.com",
    "company": "Blue Reed Yoga",
    "role": None,
    "request_type": "pricing",
    "urgency": 1,
    "summary": "Studio asks for pricing.",
}
DARIUS = {
    "is_lead": True,
    "name": "Darius Kowalski",
    "email": "darius.kowalski@example.com",
    "company": "Ironworks Gym",
    "role": "Owner",
    "request_type": "demo",
    "urgency": 3,
    "summary": "Urgent demo request.",
}
HANNAH = {"is_lead": False}

TINY_EXPECTED = [
    {"id": 9, **JONAS, "summary": ANSWER_KEY_MARKER},
    {"id": 5, **HANNAH},
    {"id": 4, **DARIUS},
]

# Markers that appear in each tiny email, so the pretend Gemini knows which one it was sent.
JONAS_MARKER = "Jonas Meyer"
HANNAH_MARKER = "out of the office"
DARIUS_MARKER = "NEED A DEMO"

# A different, tiny email set (ids 201 and 202) for the --emails / --answers options.
WANDA = {
    "is_lead": True,
    "name": "Wanda Pruitt",
    "email": "wanda.pruitt@example.com",
    "company": "Pruitt Dance",
    "role": None,
    "request_type": "demo",
    "urgency": 1,
    "summary": "Dance studio owner asks for a demo.",
}
DIGEST = {"is_lead": False}
SET_EMAILS = """# Own test set

-----

## Email 201
From: Wanda Pruitt <wanda.pruitt@example.com>
Subject: Demo please
Body:
Hi, I'd like a demo of Nova Wellness for my dance studio. Thanks, Wanda Pruitt, Pruitt Dance

-----

## Email 202
From: Studio News <digest@example.com>
Subject: Your Weekly Digest
Body:
This is your Weekly Digest. Unsubscribe at any time.
"""
SET_EXPECTED = [
    {"id": 201, **WANDA, "summary": ANSWER_KEY_MARKER},
    {"id": 202, **DIGEST},
]
WANDA_MARKER = "Wanda Pruitt"
DIGEST_MARKER = "Weekly Digest"


def set_replies():
    """What a perfect model would say for the two emails in the other set."""
    return {WANDA_MARKER: json.dumps(WANDA), DIGEST_MARKER: json.dumps(DIGEST)}


def score_rows(text):
    """The rows of the accuracy table (lines that start with a date)."""
    return [line for line in text.splitlines() if re.match(r"\|\s*20\d\d-\d\d-\d\d", line)]


def good_replies():
    """What a perfect model would say for the three tiny emails."""
    return {
        JONAS_MARKER: json.dumps(JONAS),
        HANNAH_MARKER: json.dumps(HANNAH),
        DARIUS_MARKER: json.dumps(DARIUS),
    }


class Raise:
    """The pretend Gemini fails with this error instead of answering."""

    def __init__(self, error):
        self.error = error


class Status:
    """The pretend Gemini answers with an error status instead of a result."""

    def __init__(self, code, message="pretend error"):
        self.code = code
        self.message = message


class FakeHTTP:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class PretendGemini:
    """A pretend Gemini. Records every request made to it."""

    def __init__(self, replies):
        self.replies = {marker: list(r) if isinstance(r, list) else [r] for marker, r in replies.items()}
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        headers = headers or {}
        prompt_text = json["contents"][0]["parts"][0]["text"]
        self.calls.append({"url": url, "headers": headers, "text": prompt_text,
                           "config": (json or {}).get("generationConfig")})

        if headers.get("x-goog-api-key") not in (FAKE_KEY, FAKE_FILE_KEY):
            return FakeHTTP(400, {"error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "status": "INVALID_ARGUMENT",
            }})

        for marker, queue in self.replies.items():
            if marker in prompt_text:
                reply = queue.pop(0) if len(queue) > 1 else queue[0]
                break
        else:
            raise AssertionError("the pretend Gemini was sent an email it knows nothing about")

        if isinstance(reply, Raise):
            raise reply.error
        if isinstance(reply, Status):
            return FakeHTTP(reply.code, {"error": {"code": reply.code, "message": reply.message}})
        return FakeHTTP(200, {"candidates": [{"content": {"parts": [{"text": reply}]}}]})

    def texts(self):
        return [c["text"] for c in self.calls]


def touches_real_env(path):
    return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())


def score(expected, answers):
    return extract_leads.score_results(expected, answers)


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        # Throwaway .env in place of the real one.
        self.env_path = self.tmp / ".env"
        self.env_path.write_text("OTHER=1\n", encoding="utf-8")
        self.assertFalse(touches_real_env(self.env_path))

        # Throwaway config, prompts, test data and output places.
        self.config_path = self.tmp / "config.json"
        self.config_path.write_text(json.dumps({"model": "fake-model-a"}), encoding="utf-8")
        self.prompts_dir = self.tmp / "prompts"
        self.prompts_dir.mkdir()
        (self.prompts_dir / "v1.md").write_text("PROMPT-V1: read the email and answer in JSON.", encoding="utf-8")
        (self.prompts_dir / "v2.md").write_text("PROMPT-V2: read the email carefully.", encoding="utf-8")
        self.emails_path = self.tmp / "emails.md"
        self.emails_path.write_text(TINY_EMAILS, encoding="utf-8")
        self.expected_path = self.tmp / "expected.json"
        self.expected_path.write_text(json.dumps(TINY_EXPECTED), encoding="utf-8")
        self.results_dir = self.tmp / "results"
        self.accuracy_path = self.tmp / "accuracy.md"

        for name, value in (
            ("ENV_PATH", self.env_path),
            ("CONFIG_PATH", self.config_path),
            ("PROMPTS_DIR", self.prompts_dir),
            ("EMAILS_PATH", self.emails_path),
            ("EXPECTED_PATH", self.expected_path),
            ("RESULTS_DIR", self.results_dir),
            ("ACCURACY_PATH", self.accuracy_path),
        ):
            patcher = mock.patch.object(extract_leads, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        # Tripwires: any attempt to read or change the real .env fails the test.
        def guard(original):
            def wrapper(path, *args, **kwargs):
                if touches_real_env(path):
                    raise AssertionError("TEST SAFETY: the real .env must not be touched")
                return original(path, *args, **kwargs)
            return wrapper

        for name in ("read_bytes", "write_bytes", "read_text", "write_text", "unlink"):
            patcher = mock.patch.object(Path, name, guard(getattr(Path, name)))
            patcher.start()
            self.addCleanup(patcher.stop)

        real_replace = os.replace

        def guarded_replace(src, dst, *args, **kwargs):
            if touches_real_env(src) or touches_real_env(dst):
                raise AssertionError("TEST SAFETY: the real .env must not be touched")
            return real_replace(src, dst, *args, **kwargs)

        patcher = mock.patch.object(os, "replace", guarded_replace)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Block the real internet completely.
        import socket

        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Never really wait. Every wait the script asks for is written down instead.
        self.sleeps = []
        patcher = mock.patch("time.sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, world, argv=(), remove=()):
        """Run the script's main() against a pretend Gemini. Returns (code, output)."""
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["extract_leads.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("requests.post", world.post):
            for name in remove:
                os.environ.pop(name, None)
            code = extract_leads.main()
        output = buffer.getvalue().decode("utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, output, "a key was printed on screen")
            for call in world.calls:
                self.assertNotIn(secret, call["url"], "a key was put in the web address")
        self.assertNotIn(ANSWER_KEY_MARKER, " ".join(world.texts()), "the answer key was sent to the model")
        self.assertNotIn(FAKE_KEY, self.saved_text(), "a key was saved to a file")
        self.assertNotIn(FAKE_FILE_KEY, self.saved_text(), "a key was saved to a file")
        return code, output

    def saved_text(self):
        """Everything the script saved (results and scores), except the throwaway .env."""
        parts = []
        for path in self.tmp.rglob("*"):
            if path.is_file() and path.name != ".env" and path.parent != self.prompts_dir:
                if path not in (self.config_path, self.emails_path, self.expected_path):
                    parts.append(path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def result_files_text(self):
        if not self.results_dir.exists():
            return ""
        return "\n".join(p.read_text(encoding="utf-8") for p in self.results_dir.rglob("*") if p.is_file())

    # ---- Reading the real test data ---------------------------------------

    def test_real_email_file_gives_30_numbered_emails(self):
        emails = extract_leads.parse_emails(REAL_EMAILS.read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in emails], list(range(1, 31)))
        for e in emails:
            self.assertTrue(e["text"].lstrip().startswith("From:"))
            self.assertNotIn("-----", e["text"])
        by_id = {e["id"]: e["text"] for e in emails}
        self.assertIn("Jonas Meyer", by_id[9])
        self.assertIn("Subject: Demo?", by_id[20])

    # ---- Case 1: a normal lead email returns valid JSON -------------------

    def test_case1_good_reply_scores_all_seven_fields_correct(self):
        world = PretendGemini({JONAS_MARKER: json.dumps(JONAS)})
        self.emails_path.write_text(TINY_EMAILS.split("## Email 5")[0], encoding="utf-8")
        self.expected_path.write_text(json.dumps([TINY_EXPECTED[0]]), encoding="utf-8")
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("7 of 7", output)
        self.assertIn("100.0%", output)

    def test_case1_prompt_and_email_are_sent_to_the_model(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        self.assertEqual(len(world.calls), 3)
        self.assertIn("PROMPT-V1", world.texts()[0])
        self.assertIn("Blue Reed Yoga", world.texts()[0])

    def test_case1_the_key_is_sent_in_a_header_not_the_address(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        self.assertEqual(world.calls[0]["headers"].get("x-goog-api-key"), FAKE_KEY)

    def test_case1_reply_wrapped_in_code_fences_or_chat_is_still_read(self):
        fenced = "```json\n" + json.dumps(JONAS) + "\n```"
        chatty = "Sure, here it is: " + json.dumps(DARIUS) + " Hope that helps!"
        world = PretendGemini({
            JONAS_MARKER: fenced,
            HANNAH_MARKER: json.dumps(HANNAH),
            DARIUS_MARKER: chatty,
        })
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("15 of 15", output)

    # ---- Case 2: a non-lead email -----------------------------------------

    def test_case2_non_lead_scores_one_correct_field(self):
        world = PretendGemini({HANNAH_MARKER: json.dumps(HANNAH)})
        self.emails_path.write_text("## Email 5\n" + TINY_EMAILS.split("## Email 5\n")[1].split("-----")[0], encoding="utf-8")
        self.expected_path.write_text(json.dumps([TINY_EXPECTED[1]]), encoding="utf-8")
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("1 of 1", output)

    def test_case2_extra_fields_on_a_non_lead_are_ignored(self):
        result = score(
            [{"id": 5, "is_lead": False}],
            {5: {"is_lead": False, "name": "Hannah", "urgency": 3}},
        )
        self.assertEqual((result["correct"], result["total"]), (1, 1))

    # ---- Case 3: wrong is_lead on a real lead -----------------------------

    def test_case3_wrong_is_lead_makes_all_seven_fields_wrong(self):
        result = score([{"id": 4, **DARIUS}], {4: {"is_lead": False}})
        self.assertEqual((result["correct"], result["total"]), (0, 7))

    def test_case3_non_lead_wrongly_called_a_lead_loses_only_is_lead(self):
        result = score([{"id": 5, **HANNAH}], {5: dict(DARIUS)})
        self.assertEqual((result["correct"], result["total"]), (0, 1))

    # ---- Case 4: the model returns broken JSON ----------------------------

    def test_case4_broken_json_counts_as_wrong_and_does_not_crash(self):
        world = PretendGemini({
            JONAS_MARKER: "Sorry, I can only chat about yoga.",
            HANNAH_MARKER: json.dumps(HANNAH),
            DARIUS_MARKER: json.dumps(DARIUS),
        })
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.calls), 3, "it should carry on with the next email")
        self.assertIn("8 of 15", output)
        self.assertIn("Broken replies: 1", output)
        self.assertNotIn("Traceback", output)

    def test_case4_cut_off_json_is_broken(self):
        world = PretendGemini({
            JONAS_MARKER: '{"is_lead": tr',
            HANNAH_MARKER: json.dumps(HANNAH),
            DARIUS_MARKER: json.dumps(DARIUS),
        })
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("Broken replies: 1", output)

    def test_case4_a_list_instead_of_an_object_is_broken(self):
        world = PretendGemini({
            JONAS_MARKER: "[1, 2, 3]",
            HANNAH_MARKER: json.dumps(HANNAH),
            DARIUS_MARKER: json.dumps(DARIUS),
        })
        code, output = self.run_main(world, ["--live"])
        self.assertIn("Broken replies: 1", output)

    def test_case4_the_broken_reply_is_saved_for_reading_later(self):
        world = PretendGemini({
            JONAS_MARKER: "Sorry, I can only chat about yoga.",
            HANNAH_MARKER: json.dumps(HANNAH),
            DARIUS_MARKER: json.dumps(DARIUS),
        })
        self.run_main(world, ["--live"])
        self.assertIn("Sorry, I can only chat about yoga.", self.result_files_text())

    def test_case4_a_missing_field_counts_as_wrong_not_broken(self):
        reply = {k: v for k, v in JONAS.items() if k != "role"}
        world = PretendGemini({
            JONAS_MARKER: json.dumps(reply),
            HANNAH_MARKER: json.dumps(HANNAH),
            DARIUS_MARKER: json.dumps(DARIUS),
        })
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("14 of 15", output)
        self.assertIn("Broken replies: 0", output)

    # ---- Case 5: the model or internet cannot be reached ------------------

    def test_case5_no_internet_stops_with_a_plain_message(self):
        world = PretendGemini({JONAS_MARKER: Raise(requests.exceptions.ConnectionError("down"))})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the AI service", output)
        self.assertNotIn("ConnectionError", output)
        self.assertNotIn("Traceback", output)

    def test_case5_no_half_finished_score_is_saved(self):
        world = PretendGemini({JONAS_MARKER: Raise(requests.exceptions.ConnectionError("down"))})
        self.run_main(world, ["--live"])
        self.assertFalse(self.accuracy_path.exists())
        self.assertEqual(len(world.calls), 2, "one try plus one retry, then it stops")

    def test_case5_a_timeout_is_treated_the_same_way(self):
        world = PretendGemini({JONAS_MARKER: Raise(requests.exceptions.Timeout("slow"))})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the AI service", output)
        self.assertFalse(self.accuracy_path.exists())

    def test_case5_a_gemini_server_error_is_treated_as_unreachable(self):
        world = PretendGemini({JONAS_MARKER: Status(503, "overloaded")})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the AI service", output)
        self.assertFalse(self.accuracy_path.exists())

    # ---- Case 6: the free limit is reached --------------------------------

    def test_case6_one_limit_hit_waits_and_tries_again(self):
        replies = good_replies()
        replies[JONAS_MARKER] = [Status(429, "slow down"), json.dumps(JONAS)]
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn(extract_leads.RETRY_WAIT_SECONDS, self.sleeps)
        self.assertEqual(len([t for t in world.texts() if JONAS_MARKER in t]), 2)
        self.assertIn("15 of 15", output)

    def test_case6_still_limited_after_the_retry_stops_with_a_plain_message(self):
        replies = good_replies()
        replies[JONAS_MARKER] = Status(429, "slow down")
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("free limit", output)
        self.assertEqual(len([t for t in world.texts() if JONAS_MARKER in t]), 2, "one try plus one retry")
        self.assertFalse(self.accuracy_path.exists())
        self.assertNotIn("Traceback", output)

    # ---- Case 6b: a temporary 503 is retried once (tests/cases/10-single-call-v3.md, Case 8) ----

    def jonas_calls(self, world):
        return len([t for t in world.texts() if JONAS_MARKER in t])

    def test_v3case8_a_503_then_a_good_answer_carries_on(self):
        replies = good_replies()
        replies[JONAS_MARKER] = [Status(503, "overloaded"), json.dumps(JONAS)]
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(self.jonas_calls(world), 2)
        self.assertIn(extract_leads.BUSY_WAIT_SECONDS, self.sleeps)
        self.assertIn("15 of 15", output)

    def test_v3case8_two_503s_in_a_row_stop_with_the_plain_message_after_exactly_two_requests(self):
        replies = good_replies()
        replies[JONAS_MARKER] = Status(503, f"overloaded {FAKE_KEY} Blue Reed Yoga")
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't reach the AI service (Gemini answered with code 503)", output)
        self.assertEqual(self.jonas_calls(world), 2)
        self.assertNotIn("overloaded", output)
        self.assertNotIn("Blue Reed Yoga", output)
        self.assertFalse(self.accuracy_path.exists())

    def test_v3case8_500_and_502_are_not_retried(self):
        for code_number in (500, 502, 504):
            with self.subTest(code=code_number):
                replies = good_replies()
                replies[JONAS_MARKER] = Status(code_number, "broken")
                world = PretendGemini(replies)
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn(f"(Gemini answered with code {code_number})", output)
                self.assertEqual(self.jonas_calls(world), 1)

    def test_v3case8_only_one_retry_in_total_even_when_the_two_problems_differ(self):
        for first, second, words in (
            (Status(429, "slow"), Status(503, "busy"), "code 503"),
            (Status(503, "busy"), Status(429, "slow"), "free limit"),
        ):
            with self.subTest(first=first.code, second=second.code):
                replies = good_replies()
                replies[JONAS_MARKER] = [first, second, json.dumps(JONAS)]
                world = PretendGemini(replies)
                code, output = self.run_main(world, ["--live"])
                self.assertEqual(code, 1)
                self.assertIn(words, output)
                self.assertEqual(self.jonas_calls(world), 2)

    def test_v3case8_a_429_is_still_handled_as_before(self):
        replies = good_replies()
        replies[JONAS_MARKER] = [Status(429, "slow down"), json.dumps(JONAS)]
        world = PretendGemini(replies)
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn(extract_leads.RETRY_WAIT_SECONDS, self.sleeps)
        self.assertNotIn(extract_leads.BUSY_WAIT_SECONDS, self.sleeps)

    # ---- The v3 report (tests/cases/10-single-call-v3.md, Case 7) -------------

    CLEAN = (
        "Hello Jonas, thank you for getting in touch about pricing for your studio. "
        "A member of our team will be in touch."
    )

    def v3_replies(self, darius_draft="Hello Darius, thank you for getting in touch about a demo. A member of our team will be in touch."):
        return {
            JONAS_MARKER: json.dumps({**JONAS, "category": "Lead - Pricing", "draft_reply": self.CLEAN}),
            HANNAH_MARKER: json.dumps({"is_lead": False, "category": "Not a lead"}),
            DARIUS_MARKER: json.dumps({**DARIUS, "category": "Lead - Demo", "draft_reply": darius_draft}),
        }

    def use_v3_prompt(self):
        (self.prompts_dir / "v3.md").write_text(
            'PROMPT-V3: read the email and answer in JSON with "category" and "draft_reply".', encoding="utf-8"
        )

    def test_v3_the_field_score_is_worked_out_as_for_v2(self):
        self.use_v3_prompt()
        world = PretendGemini(self.v3_replies())
        code, output = self.run_main(world, ["--live", "--prompt", "v3"])
        self.assertEqual(code, 0)
        self.assertIn("Accuracy: 100.0% (15 of 15 fields correct)", output)
        self.assertIn("PROMPT-V3", world.texts()[0])

    def test_v3_the_category_is_scored_separately(self):
        self.use_v3_prompt()
        replies = self.v3_replies()
        replies[JONAS_MARKER] = json.dumps({**JONAS, "category": "Lead - Demo", "draft_reply": self.CLEAN})
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live", "--prompt", "v3"])
        self.assertEqual(code, 0)
        self.assertIn("Accuracy: 100.0% (15 of 15 fields correct)", output, "the field score does not count the category")
        self.assertIn("Category: 2 of 3 correct (66.7%)", output)

    def test_v3_a_clean_run_reports_zero_draft_violations_and_a_sample(self):
        self.use_v3_prompt()
        world = PretendGemini(self.v3_replies())
        code, output = self.run_main(world, ["--live", "--prompt", "v3"])
        self.assertEqual(code, 0)
        self.assertIn("Category: 3 of 3 correct (100.0%)", output)
        self.assertIn("Draft rules: 0 violations in 2 drafts.", output)
        self.assertIn("Sample drafts", output)
        self.assertIn("Email 9 (Lead - Pricing):", output)
        self.assertIn("    AI draft - please review before sending", output)
        self.assertIn(self.CLEAN, output)
        self.assertIn("    Kind regards,", output)
        self.assertIn("    Nova Wellness Team", output)

    def test_v3_a_draft_that_breaks_a_rule_is_named_and_not_shown_as_a_sample(self):
        self.use_v3_prompt()
        world = PretendGemini(self.v3_replies(darius_draft="Hello Darius, we will fix it today for $5."))
        code, output = self.run_main(world, ["--live", "--prompt", "v3"])
        self.assertEqual(code, 0)
        self.assertIn("Draft rules: 1 draft broke a rule:", output)
        self.assertIn("Email 4:", output)
        self.assertIn("money", output)
        self.assertNotIn("we will fix it today", output)
        self.assertIn("Email 9 (Lead - Pricing):", output)
        self.assertNotIn("Email 4 (Lead - Demo):", output)

    def test_v3_the_score_row_shows_the_category_and_the_violations(self):
        self.use_v3_prompt()
        world = PretendGemini(self.v3_replies(darius_draft="Hello Darius, it is free."))
        self.run_main(world, ["--live", "--prompt", "v3"])
        rows = score_rows(self.accuracy_path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertIn("| v3 |", rows[0])
        self.assertIn("category 3/3", rows[0])
        self.assertIn("draft violations 1", rows[0])
        self.assertIn("is_lead 3/3", rows[0])

    def test_v3_the_saved_replies_keep_what_was_read_including_the_problems(self):
        self.use_v3_prompt()
        world = PretendGemini(self.v3_replies(darius_draft="Hello Darius, it is free."))
        self.run_main(world, ["--live", "--prompt", "v3"])
        files = list(self.results_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text(encoding="utf-8"))
        by_id = {r["id"]: r for r in saved["replies"]}
        self.assertEqual(by_id[9]["v3_result"]["category"], "Lead - Pricing")
        self.assertEqual(by_id[9]["v3_result"]["draft_reply"], self.CLEAN)
        self.assertEqual(by_id[9]["v3_problems"], [])
        self.assertIsNone(by_id[4]["v3_result"]["draft_reply"])
        self.assertTrue(by_id[4]["v3_problems"])
        self.assertEqual(by_id[5]["v3_result"]["category"], "Not a lead")

    def test_v3_the_sample_has_at_most_four_drafts(self):
        self.use_v3_prompt()
        emails = "# Many\n\n" + "".join(
            f"-----\n\n## Email {i}\nFrom: Person {i} <p{i}@example.com>\nSubject: Pricing please {i}\nBody:\nPLENTY-{i} hello\n\n"
            for i in range(1, 11)
        )
        expected = [
            {"id": i, "is_lead": True, "name": f"Person {i}", "email": f"p{i}@example.com", "company": None,
             "role": None, "request_type": "pricing", "urgency": 1, "summary": ANSWER_KEY_MARKER}
            for i in range(1, 11)
        ]
        self.emails_path.write_text(emails, encoding="utf-8")
        self.expected_path.write_text(json.dumps(expected), encoding="utf-8")
        replies = {
            f"PLENTY-{i} ": json.dumps({**expected[i - 1], "summary": "s", "category": "Lead - Pricing", "draft_reply": self.CLEAN})
            for i in range(1, 11)
        }
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live", "--prompt", "v3"])
        self.assertEqual(code, 0)
        self.assertEqual(output.count("(Lead - Pricing):"), 4)
        self.assertIn("Draft rules: 0 violations in 10 drafts.", output)

    def test_v3_a_v1_or_v2_run_shows_no_v3_lines(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        for text in ("Category:", "Draft rules:", "Sample drafts"):
            self.assertNotIn(text, output)
        row = score_rows(self.accuracy_path.read_text(encoding="utf-8"))[0]
        self.assertNotIn("category", row)
        self.assertNotIn("draft violations", row)

    # ---- Case 7: GEMINI_API_KEY is missing --------------------------------

    def test_case7_missing_key_stops_before_any_call(self):
        world = PretendGemini(good_replies())
        before = self.env_path.read_text(encoding="utf-8")
        code, output = self.run_main(world, ["--live"], remove=["GEMINI_API_KEY"])
        self.assertEqual(code, 1)
        self.assertIn("I can't find GEMINI_API_KEY", output)
        self.assertIn("Add it to your .env file", output)
        self.assertEqual(world.calls, [])
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before)
        self.assertFalse(self.accuracy_path.exists())

    def test_case7_key_found_only_in_the_env_file_still_works(self):
        self.env_path.write_text(f"OTHER=1\nGEMINI_API_KEY={FAKE_FILE_KEY}\n", encoding="utf-8")
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live"], remove=["GEMINI_API_KEY"])
        self.assertEqual(code, 0)
        self.assertEqual(world.calls[0]["headers"].get("x-goog-api-key"), FAKE_FILE_KEY)

    def test_case7_the_env_file_is_never_changed(self):
        self.env_path.write_text(f"OTHER=1\nGEMINI_API_KEY={FAKE_FILE_KEY}\n", encoding="utf-8")
        before = self.env_path.read_bytes()
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"], remove=["GEMINI_API_KEY"])
        self.assertEqual(self.env_path.read_bytes(), before)

    # ---- Case 8: Gemini rejects the key -----------------------------------

    def test_case8_rejected_key_stops_with_a_plain_message(self):
        world = PretendGemini({JONAS_MARKER: Status(403, "permission denied")})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("Gemini did not accept the key", output)
        self.assertFalse(self.accuracy_path.exists())

    def test_case8_a_wrong_key_value_is_caught_and_never_shown(self):
        world = PretendGemini(good_replies())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "WRONG-KEY-VALUE-SECRET"}):
            buffer = io.BytesIO()
            out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
            with mock.patch.object(sys, "argv", ["extract_leads.py", "--live"]), \
                    mock.patch.object(sys, "stdout", out), \
                    mock.patch("requests.post", world.post):
                code = extract_leads.main()
        output = buffer.getvalue().decode("utf-8")
        self.assertEqual(code, 1)
        self.assertIn("Gemini did not accept the key", output)
        self.assertNotIn("WRONG-KEY-VALUE-SECRET", output)
        self.assertNotIn("WRONG-KEY-VALUE-SECRET", self.saved_text())
        for call in world.calls:
            self.assertNotIn("WRONG-KEY-VALUE-SECRET", call["url"])

    # ---- Case 9: scoring maths on a tiny example --------------------------

    def test_case9_tiny_example_is_13_of_15(self):
        expected = [
            {"id": 1, **DARIUS},
            {"id": 2, **JONAS},
            {"id": 3, **HANNAH},
        ]
        answers = {
            1: dict(DARIUS),
            2: dict(JONAS, urgency=3, role="Owner"),
            3: {"is_lead": False},
        }
        result = score(expected, answers)
        self.assertEqual(result["total"], 15)
        self.assertEqual(result["correct"], 13)
        self.assertAlmostEqual(result["accuracy"], 13 / 15)
        self.assertEqual(round(result["accuracy"] * 100, 1), 86.7)

    def test_case9_summary_is_never_counted(self):
        a = score([{"id": 1, **DARIUS}], {1: dict(DARIUS, summary="Totally different words")})
        b = score([{"id": 1, **DARIUS}], {1: dict(DARIUS)})
        self.assertEqual((a["correct"], a["total"]), (7, 7))
        self.assertEqual((b["correct"], b["total"]), (7, 7))
        self.assertNotIn("summary", a["per_field"])

    def test_case9_score_for_each_field_is_reported(self):
        expected = [{"id": 1, **DARIUS}, {"id": 2, **JONAS}]
        answers = {1: dict(DARIUS), 2: dict(JONAS, urgency=3)}
        result = score(expected, answers)
        self.assertEqual(result["per_field"]["urgency"], (1, 2))
        self.assertEqual(result["per_field"]["name"], (2, 2))
        self.assertEqual(result["per_field"]["is_lead"], (2, 2))

    # ---- Case 10: scored fields on the full set ---------------------------

    def test_case10_full_answer_key_has_174_scored_fields(self):
        expected = json.loads(REAL_EXPECTED.read_text(encoding="utf-8"))
        self.assertEqual(len(expected), 30)
        self.assertEqual(sum(1 for e in expected if e["is_lead"]), 24)
        perfect = {e["id"]: {k: v for k, v in e.items() if k != "id"} for e in expected}
        result = score(expected, perfect)
        self.assertEqual(result["total"], 174)
        self.assertEqual(result["correct"], 174)
        self.assertEqual(result["accuracy"], 1.0)

    # ---- Case 11: capitals and spaces -------------------------------------

    def one_field(self, expected_lead, field, model_value):
        answer = dict(expected_lead)
        answer[field] = model_value
        result = score([{"id": 1, **expected_lead}], {1: answer})
        return result["per_field"][field]

    def test_case11_capitals_and_edge_spaces_are_ignored(self):
        self.assertEqual(self.one_field(DARIUS, "name", "DARIUS KOWALSKI "), (1, 1))
        self.assertEqual(self.one_field(DARIUS, "name", "  darius kowalski"), (1, 1))
        self.assertEqual(self.one_field(DARIUS, "company", "IRONWORKS GYM"), (1, 1))

    def test_case11_a_different_spelling_is_wrong(self):
        self.assertEqual(self.one_field(DARIUS, "name", "Darius Kowalsky"), (0, 1))
        self.assertEqual(self.one_field(DARIUS, "name", "Darius  Kowalski"), (0, 1), "spaces inside the name still matter")

    def test_case11_null_is_only_matched_by_null(self):
        self.assertEqual(self.one_field(JONAS, "role", None), (1, 1))
        for wrong in ("null", "None", "none", "", " ", "N/A"):
            with self.subTest(value=wrong):
                self.assertEqual(self.one_field(JONAS, "role", wrong), (0, 1))

    def test_case11_a_real_value_where_null_was_expected_is_wrong(self):
        self.assertEqual(self.one_field(JONAS, "company", None), (0, 1))
        self.assertEqual(self.one_field(JONAS, "role", "Owner"), (0, 1))

    def test_case11_request_type_and_urgency_must_match_exactly(self):
        self.assertEqual(self.one_field(JONAS, "request_type", "Pricing "), (1, 1))
        self.assertEqual(self.one_field(JONAS, "request_type", "demo"), (0, 1))
        self.assertEqual(self.one_field(DARIUS, "urgency", 3), (1, 1))
        self.assertEqual(self.one_field(DARIUS, "urgency", 2), (0, 1))

    # ---- Case 12: each prompt version keeps its own score -----------------

    def test_case12_a_live_run_adds_one_row_to_accuracy_md(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        text = self.accuracy_path.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if re.match(r"\|\s*20\d\d-\d\d-\d\d", line)]
        self.assertEqual(len(rows), 1)
        self.assertIn("v1", rows[0])
        self.assertIn("fake-model-a", rows[0])
        self.assertIn("100.0%", rows[0])
        for field in ("is_lead", "name", "email", "company", "role", "request_type", "urgency"):
            self.assertIn(field, text)

    def test_case12_second_prompt_version_keeps_the_first_row(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live", "--prompt", "v1"])
        replies = good_replies()
        replies[JONAS_MARKER] = json.dumps(dict(JONAS, urgency=3))
        world2 = PretendGemini(replies)
        self.run_main(world2, ["--live", "--prompt", "v2"])
        text = self.accuracy_path.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if re.match(r"\|\s*20\d\d-\d\d-\d\d", line)]
        self.assertEqual(len(rows), 2)
        self.assertIn("v1", rows[0])
        self.assertIn("100.0%", rows[0])
        self.assertIn("v2", rows[1])
        self.assertIn("93.3%", rows[1])
        self.assertIn("PROMPT-V2", world2.texts()[0])
        self.assertNotIn("PROMPT-V1", world2.texts()[0])

    def test_case12_an_unknown_prompt_version_is_explained(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live", "--prompt", "v9"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't find the prompt file", output)
        self.assertIn("v9", output)
        self.assertEqual(world.calls, [])

    # ---- Case 13: switching models is a one-line change -------------------

    def test_case13_dry_run_shows_the_model_name_from_the_config(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn("fake-model-a", output)

    def test_case13_changing_the_config_changes_the_model_used(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        self.assertIn("fake-model-a", world.calls[0]["url"])

        self.config_path.write_text(json.dumps({"model": "fake-model-b"}), encoding="utf-8")
        world2 = PretendGemini(good_replies())
        self.run_main(world2, ["--live"])
        self.assertIn("fake-model-b", world2.calls[0]["url"])
        self.assertNotIn("fake-model-a", world2.calls[0]["url"])

    def test_case13_the_model_name_is_saved_with_the_score(self):
        self.config_path.write_text(json.dumps({"model": "fake-model-b"}), encoding="utf-8")
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        self.assertIn("fake-model-b", self.accuracy_path.read_text(encoding="utf-8"))

    def test_a_broken_config_is_explained_before_anything_happens(self):
        self.config_path.write_text("{}", encoding="utf-8")
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("model", output)
        self.assertIn("config.json", output)
        self.assertEqual(world.calls, [])

    # ---- Default behaviour ------------------------------------------------

    def test_default_is_a_dry_run_that_makes_no_calls_and_saves_nothing(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, [])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertEqual(world.calls, [])
        self.assertFalse(self.accuracy_path.exists())
        self.assertFalse(self.results_dir.exists())

    def test_dry_run_shows_prompt_version_model_and_the_first_email(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--prompt", "v2"])
        self.assertIn("v2", output)
        self.assertIn("fake-model-a", output)
        self.assertIn("3 emails", output)
        self.assertIn("would send", output)
        self.assertIn("Pricing request", output)
        self.assertIn("PROMPT-V2", output)

    def test_dry_run_works_without_the_key(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, [], remove=["GEMINI_API_KEY"])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertEqual(world.calls, [])

    # ---- A different set of emails (--emails and --answers) ---------------

    def make_set(self, name="own-emails", expected=None, folder=None):
        """Write the tiny other set. Returns (emails_path, answers_path) as text."""
        folder = folder or (self.tmp / "sets")
        folder.mkdir(parents=True, exist_ok=True)
        emails = folder / f"{name}.md"
        emails.write_text(SET_EMAILS, encoding="utf-8")
        answers = folder / f"{name}-expected.json"
        answers.write_text(json.dumps(SET_EXPECTED if expected is None else expected), encoding="utf-8")
        return str(emails), str(answers)

    def set_args(self, *extra, **kwargs):
        emails, answers = self.make_set(**kwargs)
        return ["--emails", emails, "--answers", answers, *extra]

    def test_case14_other_set_is_used_instead_of_the_30(self):
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", *self.set_args()])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.calls), 2)
        self.assertIn("Wanda Pruitt", world.texts()[0])
        self.assertNotIn("Jonas Meyer", " ".join(world.texts()))
        self.assertIn("Email set: own-emails", output)

    def test_case14_dry_run_shows_the_set_and_makes_no_calls(self):
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, self.set_args())
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertIn("Email set: own-emails", output)
        self.assertIn("2 emails", output)
        self.assertIn("Demo please", output)
        self.assertEqual(world.calls, [])

    def test_case14_ids_do_not_have_to_start_at_1(self):
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", *self.set_args()])
        self.assertIn("Email 201: reply read", output)
        self.assertIn("Email 202: reply read", output)

    def test_case15_perfect_answers_score_8_of_8(self):
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", *self.set_args()])
        self.assertIn("Accuracy: 100.0% (8 of 8 fields correct)", output)

    def test_case15_one_wrong_field_scores_7_of_8(self):
        replies = set_replies()
        replies[WANDA_MARKER] = json.dumps(dict(WANDA, urgency=3))
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live", *self.set_args()])
        self.assertIn("Accuracy: 87.5% (7 of 8 fields correct)", output)

    def test_case16_row_has_the_set_name_and_old_rows_are_untouched(self):
        self.run_main(PretendGemini(good_replies()), ["--live"])
        before = self.accuracy_path.read_text(encoding="utf-8")
        first_row = score_rows(before)[0]

        world = PretendGemini(set_replies())
        self.run_main(world, ["--live", "--prompt", "v2", *self.set_args()])
        after = self.accuracy_path.read_text(encoding="utf-8")

        self.assertTrue(after.startswith(before), "the existing text must not change")
        rows = score_rows(after)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], first_row)
        self.assertNotIn("[", first_row)
        self.assertIn("v2 [own-emails]", rows[1])
        self.assertIn("(8/8)", rows[1])

    def test_case16_replies_file_name_includes_the_set_name(self):
        self.run_main(PretendGemini(good_replies()), ["--live"])
        self.run_main(PretendGemini(set_replies()), ["--live", *self.set_args()])
        names = sorted(p.name for p in self.results_dir.iterdir())
        self.assertEqual(len(names), 2)
        self.assertEqual(len([n for n in names if "own-emails" in n]), 1)

    def test_case17_one_option_without_the_other_is_refused(self):
        emails, answers = self.make_set()
        for argv in (["--emails", emails], ["--answers", answers]):
            with self.subTest(argv=argv[0]):
                world = PretendGemini(set_replies())
                code, output = self.run_main(world, ["--live", *argv])
                self.assertEqual(code, 1)
                self.assertIn("--emails and --answers together", output)
                self.assertEqual(world.calls, [])
        self.assertFalse(self.accuracy_path.exists())

    def test_case18_missing_email_file_is_named(self):
        _, answers = self.make_set()
        missing = str(self.tmp / "sets" / "nope.md")
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", "--emails", missing, "--answers", answers])
        self.assertEqual(code, 1)
        self.assertIn("couldn't find the file", output)
        self.assertIn("nope.md", output)
        self.assertEqual(world.calls, [])

    def test_case18_unreadable_answer_file_is_explained(self):
        emails, answers = self.make_set()
        Path(answers).write_text("this is not json", encoding="utf-8")
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", "--emails", emails, "--answers", answers])
        self.assertEqual(code, 1)
        self.assertIn("couldn't read", output)
        self.assertEqual(world.calls, [])

    def test_case18_mismatched_ids_are_explained(self):
        emails, answers = self.make_set(expected=[SET_EXPECTED[0]])
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", "--emails", emails, "--answers", answers])
        self.assertEqual(code, 1)
        self.assertIn("don't list the same emails", output)
        self.assertEqual(world.calls, [])

    def test_case18_a_path_from_the_project_folder_is_found_from_anywhere(self):
        self.make_set(folder=self.tmp / "own")
        world = PretendGemini(set_replies())
        with mock.patch.object(extract_leads, "PROJECT_ROOT", self.tmp):
            code, output = self.run_main(
                world,
                ["--live", "--emails", "own/own-emails.md", "--answers", "own/own-emails-expected.json"],
            )
        self.assertEqual(code, 0)
        self.assertIn("8 of 8", output)

    # ---- --only: send a single email ---------------------------------------

    def test_case19_only_sends_one_email_and_shows_each_field(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live", "--only", "9"])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.calls), 1)
        self.assertIn("Jonas Meyer", world.texts()[0])
        self.assertIn("Email 9", output)
        self.assertIn('name: key "Jonas Meyer", Gemini "Jonas Meyer": match', output)
        self.assertIn("role: key null, Gemini null: match", output)
        self.assertIn("urgency: key 1, Gemini 1: match", output)
        self.assertIn("7 of 7 fields match the key", output)
        self.assertNotIn(extract_leads.PAUSE_SECONDS, self.sleeps)

    def test_case19_only_saves_nothing(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live", "--only", "9"])
        self.assertFalse(self.accuracy_path.exists())
        self.assertFalse(self.results_dir.exists())

    def test_case19_a_wrong_field_is_marked(self):
        replies = good_replies()
        replies[JONAS_MARKER] = json.dumps(dict(JONAS, urgency=3))
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live", "--only", "9"])
        self.assertIn("urgency: key 1, Gemini 3: WRONG", output)
        self.assertIn("6 of 7 fields match the key", output)

    def test_case19_a_non_lead_shows_only_is_lead(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live", "--only", "5"])
        self.assertEqual(code, 0)
        self.assertIn("is_lead: key false, Gemini false: match", output)
        self.assertIn("1 of 1 fields match the key", output)
        self.assertNotIn("name:", output)

    def test_case19_a_real_lead_called_a_non_lead_shows_missing_fields(self):
        replies = good_replies()
        replies[DARIUS_MARKER] = json.dumps({"is_lead": False})
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live", "--only", "4"])
        self.assertIn("is_lead: key true, Gemini false: WRONG", output)
        self.assertIn('name: key "Darius Kowalski", Gemini (missing): WRONG', output)
        self.assertIn("0 of 7 fields match the key", output)

    def test_case19_a_broken_reply_is_reported_without_crashing(self):
        replies = good_replies()
        replies[JONAS_MARKER] = "Sorry, I can only chat about yoga."
        world = PretendGemini(replies)
        code, output = self.run_main(world, ["--live", "--only", "9"])
        self.assertEqual(code, 0)
        self.assertIn("reply was broken", output)
        self.assertIn("0 of 7 fields match the key", output)
        self.assertNotIn("Traceback", output)

    def test_case19_an_unknown_id_is_explained(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live", "--only", "999"])
        self.assertEqual(code, 1)
        self.assertIn("couldn't find email 999", output)
        self.assertEqual(world.calls, [])

    def test_case19_dry_run_shows_only_that_email(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--only", "9"])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertIn("would send", output)
        self.assertIn("Pricing request", output)
        self.assertNotIn("Automatic reply", output)
        self.assertEqual(world.calls, [])

    def test_case19_only_works_with_another_set(self):
        world = PretendGemini(set_replies())
        code, output = self.run_main(world, ["--live", "--only", "202", *self.set_args()])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.calls), 1)
        self.assertIn("Weekly Digest", world.texts()[0])
        self.assertIn("Email set: own-emails", output)
        self.assertIn("Email 202", output)
        self.assertIn("1 of 1 fields match the key", output)

    def test_case19_an_id_that_is_not_a_number_is_refused(self):
        world = PretendGemini(good_replies())
        errors = io.StringIO()
        with mock.patch.object(sys, "stderr", errors):
            with self.assertRaises(SystemExit) as stop:
                self.run_main(world, ["--live", "--only", "abc"])
        self.assertNotEqual(stop.exception.code, 0)
        self.assertIn("--only", errors.getvalue())
        self.assertEqual(world.calls, [])

    # ---- Case 20: nothing changes without the new options ------------------

    def test_case20_without_the_new_options_nothing_changes(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(world.calls), 3)
        self.assertNotIn("Email set", output)
        self.assertIn("Accuracy: 100.0% (15 of 15 fields correct)", output)
        rows = score_rows(self.accuracy_path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("[", rows[0])

    # ---- Case 21: the "couldn't reach" message says why --------------------

    def unreachable_output(self, reply):
        world = PretendGemini({JONAS_MARKER: reply})
        code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("I couldn't reach the AI service", output)
        self.assertIn("Check your internet and try again.", output)
        self.assertNotIn("Traceback", output)
        self.assertFalse(self.accuracy_path.exists())
        return output

    def test_case21_a_timeout_says_the_request_timed_out(self):
        output = self.unreachable_output(Raise(requests.exceptions.Timeout("slow")))
        self.assertIn(
            "I couldn't reach the AI service (the request timed out). Check your internet and try again.",
            output,
        )

    def test_case21_no_connection_says_so(self):
        output = self.unreachable_output(Raise(requests.exceptions.ConnectionError("down")))
        self.assertIn("(no connection)", output)

    def test_case21_another_connection_problem_is_named_generally(self):
        output = self.unreachable_output(Raise(requests.exceptions.RequestException("odd")))
        self.assertIn("(a connection problem)", output)

    def test_case21_server_errors_show_the_code(self):
        for code_number in (500, 502, 503):
            with self.subTest(code=code_number):
                output = self.unreachable_output(Status(code_number, "overloaded"))
                self.assertIn(f"(Gemini answered with code {code_number})", output)

    def test_case21_the_hint_never_includes_secrets_email_text_or_error_text(self):
        leaky = Raise(requests.exceptions.ConnectionError(f"failed for {FAKE_KEY} Jonas Meyer Blue Reed Yoga"))
        output = self.unreachable_output(leaky)
        self.assertNotIn("failed for", output)
        self.assertNotIn("Blue Reed Yoga", output)
        output = self.unreachable_output(Status(503, f"overloaded {FAKE_KEY} Blue Reed Yoga"))
        self.assertNotIn("overloaded", output)
        self.assertNotIn("Blue Reed Yoga", output)

    # ---- Case 22: Gemini requests are as steady as the API allows ------------

    def test_case22_requests_ask_for_temperature_0_a_fixed_seed_and_json(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        self.assertEqual(len(world.calls), 3)
        for call in world.calls:
            self.assertEqual(call["config"]["temperature"], 0)
            self.assertEqual(call["config"]["seed"], extract_leads.GENERATION_SEED)
            self.assertEqual(call["config"]["responseMimeType"], "application/json")

    def test_case22_a_refused_seed_is_retried_once_without_it(self):
        refused = Status(400, 'Invalid JSON payload received. Unknown name "seed" at '
                              "'generation_config': Cannot find field.")
        replies = good_replies()
        replies[JONAS_MARKER] = [refused, json.dumps(JONAS)]
        world = PretendGemini(replies)
        with mock.patch.object(extract_leads, "_seed_rejected", False):
            code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)
        self.assertIn("15 of 15", output)
        self.assertNotIn("refused", output.lower())
        self.assertEqual(len(world.calls), 4, "one refused request, its retry, and the two other emails")
        self.assertIn("seed", world.calls[0]["config"])
        for call in world.calls[1:]:
            self.assertNotIn("seed", call["config"], "the seed must not be sent again once refused")

    def test_case22_other_400_errors_keep_their_usual_message(self):
        world = PretendGemini({JONAS_MARKER: Status(400, "something else is wrong")})
        with mock.patch.object(extract_leads, "_seed_rejected", False):
            code, output = self.run_main(world, ["--live"])
        self.assertEqual(code, 1)
        self.assertIn("Gemini refused the request (code 400)", output)
        self.assertEqual(len(world.calls), 1, "no retry for an error that is not about the seed")

    # ---- Other safety checks ----------------------------------------------

    def test_a_pause_is_taken_between_calls(self):
        world = PretendGemini(good_replies())
        self.run_main(world, ["--live"])
        self.assertGreaterEqual(self.sleeps.count(extract_leads.PAUSE_SECONDS), 2)

    def test_the_final_report_shows_the_score_in_plain_words(self):
        world = PretendGemini(good_replies())
        code, output = self.run_main(world, ["--live"])
        self.assertIn("Accuracy: 100.0% (15 of 15 fields correct)", output)

    def test_the_real_env_is_never_opened_by_a_normal_run(self):
        # The tripwires in setUp raise an error if the real .env is touched.
        world = PretendGemini(good_replies())
        code, _ = self.run_main(world, ["--live"])
        self.assertEqual(code, 0)


class AskGeminiRetryTests(unittest.TestCase):
    """ask_gemini on its own: a longer wait for each request, and exactly one retry in total."""

    class Answer:
        def __init__(self, code, text=""):
            self.status_code = code
            self.text = text or "error"
            self._text = text

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}

    def ask(self, *replies):
        """Run ask_gemini against pretend replies (an Answer, or an exception to raise). Returns (result, calls, sleeps)."""
        sleeps = []
        post = mock.Mock(side_effect=list(replies))
        with mock.patch.object(extract_leads.requests, "post", post), mock.patch("time.sleep", sleeps.append), \
                mock.patch.object(extract_leads, "_seed_rejected", True):
            try:
                result = extract_leads.ask_gemini("fake-model", FAKE_KEY, "an email")
            except Exception as problem:
                result = problem
        return result, post, sleeps

    def good(self):
        return self.Answer(200, '{"category": "Not a lead"}')

    def test_each_request_may_take_up_to_two_minutes(self):
        self.assertEqual(extract_leads.REQUEST_TIMEOUT, 120)
        _, post, _ = self.ask(self.good())
        self.assertEqual(post.call_args.kwargs["timeout"], extract_leads.REQUEST_TIMEOUT)

    def test_a_timeout_is_retried_once_after_a_short_wait(self):
        result, post, sleeps = self.ask(requests.exceptions.Timeout("slow"), self.good())
        self.assertEqual(result, '{"category": "Not a lead"}')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleeps, [extract_leads.BUSY_WAIT_SECONDS])

    def test_a_dropped_connection_is_retried_once(self):
        result, post, _ = self.ask(requests.exceptions.ConnectionError("down"), self.good())
        self.assertEqual(result, '{"category": "Not a lead"}')
        self.assertEqual(post.call_count, 2)

    def test_a_server_error_other_than_503_is_still_not_retried_but_can_be_skipped(self):
        for code in (500, 502, 504):
            with self.subTest(code=code):
                result, post, _ = self.ask(self.Answer(code), self.good())
                self.assertIsInstance(result, extract_leads.GeminiUnreachable)
                self.assertEqual(post.call_count, 1)

    def test_two_timeouts_give_up_after_exactly_two_requests_with_an_unreachable_error(self):
        result, post, _ = self.ask(requests.exceptions.Timeout("slow"), requests.exceptions.Timeout("slow"), self.good())
        self.assertIsInstance(result, extract_leads.GeminiUnreachable)
        self.assertIsInstance(result, extract_leads.SetupProblem)
        self.assertIn("(the request timed out)", str(result))
        self.assertEqual(post.call_count, 2)

    def test_there_is_only_one_retry_in_total_whichever_problem_comes_first(self):
        result, post, _ = self.ask(self.Answer(429), requests.exceptions.Timeout("slow"), self.good())
        self.assertIsInstance(result, extract_leads.GeminiUnreachable)
        self.assertEqual(post.call_count, 2)
        result, post, _ = self.ask(requests.exceptions.Timeout("slow"), self.Answer(429), self.good())
        self.assertNotIsInstance(result, extract_leads.GeminiUnreachable)    # the free limit: a stop, not a skip
        self.assertIn("free limit", str(result))
        self.assertEqual(post.call_count, 2)

    def test_a_rejected_key_or_unknown_model_is_not_retried_and_is_not_an_unreachable_error(self):
        for code in (401, 403, 404, 400):
            with self.subTest(code=code):
                result, post, _ = self.ask(self.Answer(code), self.good())
                self.assertIsInstance(result, extract_leads.SetupProblem)
                self.assertNotIsInstance(result, extract_leads.GeminiUnreachable)
                self.assertEqual(post.call_count, 1)

    def test_the_error_text_still_never_holds_anything_from_the_failure(self):
        leaky = requests.exceptions.ConnectionError(f"failed for {FAKE_KEY} Blue Reed Yoga")
        result, _, _ = self.ask(leaky, leaky)
        self.assertNotIn(FAKE_KEY, str(result))
        self.assertNotIn("Blue Reed Yoga", str(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
