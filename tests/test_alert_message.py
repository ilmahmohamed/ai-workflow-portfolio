"""Pretend-data checks for workflows/02-email-to-attio/alert_message.py (the failure alert for #automation-log).

They say what the module must offer:

  alert_message.reason_from_log(log_text)   a reason from a FIXED list, or "Unknown error, see the run"
  alert_message.reason_for(step, log_text)  the same, but a failed install or set-up has its own fixed reason
  alert_message.waiting_for_triage(messages, now)   (how many Inbox emails are not handled yet, the first one)
  alert_message.waiting_in_leads(messages)          (how many emails are waiting in Leads, the first one)
  alert_message.build_text(step, reason, run_url=None, waiting=None, first=None, secrets=())
        the alert, one item per line: step, Reason, Waiting, First waiting, Run
  alert_message.main(argv, env, stdout, details)    prints ONE line of JSON {"text": ...} and always returns 0

Everything here is fake. The internet is blocked, and nothing the module reads from Outlook is ever printed.

Run:  python tests\\test_alert_message.py
"""

import datetime
import io
import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / "workflows"
sys.path.insert(0, str(WORKFLOWS / "01-outlook-to-slack"))
sys.path.insert(0, str(WORKFLOWS / "02-email-to-attio"))

import alert_message  # noqa: E402

UNKNOWN = "Unknown error, see the run"
RUN_URL = "https://github.com/example-owner/example-repo/actions/runs/123456789"
NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=datetime.timezone.utc)


def lines_of(text):
    return text.split("\n")


def message(mid, subject="Demo please", name="Sam Fake", address="sam.fake@example.com",
            received="2026-10-20T09:00:00Z", categories=()):
    return {"id": mid, "subject": subject, "categories": list(categories), "receivedDateTime": received,
            "from": {"emailAddress": {"address": address, "name": name}},
            "body": {"contentType": "text", "content": "PRIVATE-BODY-WORDS"}}


# What each script really prints when it fails, and the fixed reason it must give.
KNOWN = [
    ("Gemini did not accept the key. Check it and try again.", "Gemini rejected the key"),
    ("Gemini doesn't know the model name x. Check the name in config.json.", "Gemini model name not found"),
    ("The free limit was reached, so I stopped. Nothing was saved. Please try again later.",
     "Gemini's free limit was reached"),
    ("I couldn't reach the AI service (the request timed out). Check your internet and try again.",
     "Gemini could not be reached"),
    ("The AI service kept failing, so I stopped. The rest were left untouched.", "Gemini could not be reached"),
    ("Gemini refused the request (code 400). Nothing was saved.", "Gemini refused the request"),
    ("Attio did not accept the key (code 401: the key is not valid). Check it and try again.",
     "Attio rejected the key"),
    ("Attio did not accept the key (code 403: no permission). The key may be missing a permission",
     "Attio rejected the key"),
    ("Attio's request limit was reached, so I stopped. Please try again later.", "Attio's request limit was reached"),
    ("I couldn't reach Attio (timed out). Check your internet and try again.", "Attio could not be reached"),
    ("Attio did not accept the deal owner. Check the ATTIO_DEAL_OWNER setting.", "Attio deal owner not found"),
    ("I couldn't find the deal owner in Attio. Check the ATTIO_DEAL_OWNER setting.", "Attio deal owner not found"),
    ("I couldn't find the stage New in the Attio Deals list. Check the stage names in Attio.",
     "Attio deal stage not found"),
    ("Attio refused to create the person. Nothing else was changed for this email.", "Attio refused a record"),
    ("Attio refused to create the company. Nothing else was changed for this email.", "Attio refused a record"),
    ("Your saved sign-in no longer works (reason: invalid_grant). Please run outlook_signin.py again.",
     "Outlook sign-in expired"),
    ("Your saved sign-in has no account in it. Please run outlook_signin.py again.", "Outlook sign-in expired"),
    ("Your saved sign-in looks damaged. Please run outlook_signin.py again.", "Outlook sign-in expired"),
    ("Outlook didn't accept the sign-in. Please run outlook_signin.py again.", "Outlook sign-in expired"),
    ("Outlook refused the change. Your sign-in may need redoing: run outlook_signin.py again. That email was left as it was.",
     "Outlook sign-in expired"),
    ("I couldn't reach Outlook. Check the internet connection.", "Outlook could not be reached"),
    ("Outlook refused the request (code 500).", "Outlook refused a request"),
    ("The lead was saved in Attio but the email could not be moved to Leads/Processed (Outlook refused).",
     "Outlook refused a request"),
    ("I couldn't find a folder called Processed inside Leads. Create it in Outlook", "An Outlook folder is missing"),
    ("I couldn't find a folder called Leads in your mailbox", "An Outlook folder is missing"),
    ("Slack refused the message (code 404).", "Slack could not be posted to"),
    ("I couldn't reach Slack.", "Slack could not be posted to"),
    ("The Slack link doesn't look like a Slack webhook address (it should start with", "Slack could not be posted to"),
    ("I can't find GEMINI_API_KEY. Add it to your .env file and try again.", "A required setting is missing"),
    ("I can't find ATTIO_API_KEY. Add it to your .env file and try again.", "A required setting is missing"),
    ("I can't find MS_TOKEN_CACHE. Run outlook_signin.py once first.", "A required setting is missing"),
]


class NoNetwork(unittest.TestCase):
    def setUp(self):
        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)


class ReasonTests(NoNetwork):
    def test_every_known_failure_gets_its_fixed_reason(self):
        for printed, expected in KNOWN:
            with self.subTest(printed=printed[:50]):
                log = f"LIVE: updating\nEmail: e-1a2b3c\n{printed}\nStopped. 0 email(s) were handled.\n"
                self.assertEqual(alert_message.reason_from_log(log), expected)

    def test_the_reasons_come_from_a_fixed_list(self):
        allowed = {reason for _, reason in KNOWN}
        for printed, _ in KNOWN:
            self.assertIn(alert_message.reason_from_log(printed), allowed)
        self.assertEqual(alert_message.reason_from_log(""), UNKNOWN)

    def test_anything_else_is_unknown_and_its_text_is_never_copied(self):
        raw = "Traceback RAW-ERROR-TEXT-XYZ key=SECRET-VALUE-123 for sam@example.com"
        for log in (raw, "Something unexpected went wrong (KeyError). The mailbox shows which emails were already handled.",
                    "", "   \n", None, 12345):
            with self.subTest(log=str(log)[:30]):
                reason = alert_message.reason_from_log(log)
                self.assertEqual(reason, UNKNOWN)
        text = alert_message.build_text("Triage", alert_message.reason_from_log(raw), RUN_URL)
        for leaked in ("RAW-ERROR-TEXT", "SECRET-VALUE", "sam@example.com"):
            self.assertNotIn(leaked, text)

    def test_a_known_phrase_among_other_text_gives_only_the_fixed_reason(self):
        log = "line RAW-LEAK-ONE\nGemini did not accept the key. Check it and try again.\nline RAW-LEAK-TWO"
        reason = alert_message.reason_from_log(log)
        self.assertEqual(reason, "Gemini rejected the key")
        self.assertNotIn("RAW-LEAK", reason)

    def test_matching_ignores_capitals(self):
        self.assertEqual(alert_message.reason_from_log("GEMINI DID NOT ACCEPT THE KEY"), "Gemini rejected the key")

    def test_a_failed_install_or_set_up_has_its_own_fixed_reason(self):
        self.assertEqual(alert_message.reason_for("Install libraries", "anything"), "The libraries could not be installed")
        self.assertEqual(alert_message.reason_for("set-up (checkout or Python)", "anything"),
                         "Set-up failed before the pipeline started")
        self.assertEqual(alert_message.reason_for("Triage", "Gemini did not accept the key."), "Gemini rejected the key")
        self.assertEqual(alert_message.reason_for("Attio sync", ""), UNKNOWN)


class WaitingTests(NoNetwork):
    def test_triage_counts_the_inbox_emails_not_handled_and_names_the_oldest(self):
        messages = [
            message("late", "Second one", "Lee Late", received="2026-10-20T11:00:00Z"),
            message("early", "First one", "Eve Early", received="2026-10-20T09:00:00Z"),
            message("done", "Already tagged", categories=["Not a lead"]),
            message("system", "Your verification code", address="noreply@example.com"),
            message("old", "Too old", received="2026-09-01T09:00:00Z"),
        ]
        count, first = alert_message.waiting_for_triage(messages, NOW)
        self.assertEqual(count, 2)
        self.assertEqual(first, ("Eve Early", "First one"))

    def test_triage_with_nothing_waiting_gives_no_first_email(self):
        self.assertEqual(alert_message.waiting_for_triage([], NOW), (0, None))

    def test_leads_counts_the_waiting_emails_and_names_the_oldest(self):
        messages = [
            message("b", "Second lead", "Bo Second", received="2026-10-20T11:00:00Z"),
            message("a", "First lead", "Ann First", received="2026-10-20T09:00:00Z"),
            message("s", "Security alert", address="no-reply@example.com"),
        ]
        count, first = alert_message.waiting_in_leads(messages)
        self.assertEqual(count, 2)
        self.assertEqual(first, ("Ann First", "First lead"))
        self.assertEqual(alert_message.waiting_in_leads([]), (0, None))

    def test_only_a_name_and_a_subject_are_kept_never_an_address_or_a_body(self):
        _, first = alert_message.waiting_for_triage([message("a")], NOW)
        self.assertEqual(first, ("Sam Fake", "Demo please"))
        self.assertNotIn("example.com", repr(first))
        self.assertNotIn("PRIVATE", repr(first))

    def test_a_missing_name_or_subject_is_kept_as_empty_text(self):
        bare = {"id": "x", "receivedDateTime": "2026-10-20T09:00:00Z", "categories": [], "from": None, "subject": None}
        self.assertEqual(alert_message.waiting_for_triage([bare], NOW), (1, ("", "")))
        self.assertEqual(alert_message.waiting_in_leads([bare]), (1, ("", "")))


class BuildTextTests(NoNetwork):
    def full(self, **changes):
        arguments = dict(step="Triage", reason="Gemini rejected the key", run_url=RUN_URL,
                         waiting=(3, "the Inbox"), first=("Sam Fake", "Demo please"))
        arguments.update(changes)
        return alert_message.build_text(**arguments)

    def test_the_alert_has_each_item_on_its_own_line_in_order(self):
        self.assertEqual(lines_of(self.full()), [
            "Pipeline step failed: Triage.",
            "Reason: Gemini rejected the key",
            "Waiting: 3 in the Inbox",
            "First waiting: Sam Fake, \"Demo please\"",
            f"Run: {RUN_URL}",
        ])

    def test_waiting_names_the_place_for_each_step(self):
        self.assertIn("Waiting: 1 in Leads", lines_of(self.full(waiting=(1, "Leads"))))
        self.assertIn("Waiting: 0 in Leads", lines_of(self.full(waiting=(0, "Leads"))))

    def test_without_waiting_that_line_is_left_out(self):
        text = self.full(waiting=None)
        self.assertNotIn("Waiting", text)
        self.assertIn("First waiting", text)

    def test_without_a_first_email_that_line_is_left_out(self):
        text = self.full(first=None)
        self.assertNotIn("First waiting", text)
        self.assertIn("Waiting: 3", text)

    def test_with_no_details_at_all_the_alert_still_has_step_reason_and_link(self):
        text = self.full(waiting=None, first=None)
        self.assertEqual(lines_of(text), ["Pipeline step failed: Triage.", "Reason: Gemini rejected the key",
                                          f"Run: {RUN_URL}"])

    def test_a_missing_link_leaves_out_the_run_line(self):
        self.assertNotIn("Run:", self.full(run_url=None))

    def test_a_missing_name_or_subject_gets_a_plain_placeholder(self):
        self.assertIn("First waiting: (no name), \"(no subject)\"", self.full(first=("", "")))
        self.assertIn("First waiting: Sam Fake, \"(no subject)\"", self.full(first=("Sam Fake", None)))
        self.assertIn("First waiting: (no name), \"Hi\"", self.full(first=(None, "Hi")))

    def test_text_is_written_the_safe_way_for_slack_on_one_line(self):
        text = self.full(first=("Sam & Co <x>", "Q&A\n<now>\r\n>> go\tok"))
        line = [l for l in lines_of(text) if l.startswith("First waiting")]
        self.assertEqual(line, ['First waiting: Sam &amp; Co &lt;x&gt;, "Q&amp;A &lt;now&gt; &gt;&gt; go ok"'])
        self.assertEqual(len(lines_of(text)), 5)

    def test_a_long_subject_is_cut(self):
        text = self.full(first=("Sam", "word " * 100))
        line = [l for l in lines_of(text) if l.startswith("First waiting")][0]
        self.assertLessEqual(len(line), 200)
        self.assertTrue(line.endswith('..."'))

    def test_a_negative_or_odd_count_never_breaks_the_alert(self):
        for bad in ((None, "Leads"), ("many", "Leads"), (-1, "Leads"), (3,), "3", 3):
            with self.subTest(waiting=bad):
                text = self.full(waiting=bad)
                self.assertNotIn("many", text)
                self.assertIn("Pipeline step failed", text)

    # ---- never a key, token, webhook link or address ----------------------------------------------

    def test_an_email_address_in_a_name_or_subject_is_removed(self):
        text = self.full(first=("sam.fake@example.com", "Write to sam@example.com or <b@example.com> now"))
        self.assertNotIn("@", text)
        self.assertNotIn("example.com", text.replace("github.com/example-owner", ""))
        self.assertIn("[removed]", text)

    def test_secret_values_are_removed_wherever_they_appear(self):
        secrets = ["SECRET-KEY-VALUE-123", "TOKEN-VALUE-ABCDEF"]
        text = self.full(first=("Name SECRET-KEY-VALUE-123", "see TOKEN-VALUE-ABCDEF now"), secrets=secrets)
        for secret in secrets:
            self.assertNotIn(secret, text)
        self.assertIn("[removed]", text)

    def test_a_short_secret_value_is_not_used_to_wipe_out_ordinary_words(self):
        text = self.full(first=("Sam", "a demo"), secrets=["a", "", None, "demo"])
        self.assertIn("a demo", text)

    def test_a_slack_webhook_link_is_removed(self):
        text = self.full(first=("Sam", "link https://hooks.slack.com/services/T0/B0/XYZ here"))
        self.assertNotIn("hooks.slack.com", text)
        self.assertNotIn("XYZ", text)

    def test_the_real_run_link_survives_the_cleaning(self):
        self.assertIn(f"Run: {RUN_URL}", self.full())

    def test_secrets_given_as_the_environment_are_found_by_name(self):
        env = {"GEMINI_API_KEY": "GEMINI-SECRET-VALUE", "ATTIO_API_KEY": "ATTIO-SECRET-VALUE",
               "SLACK_LOG_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/LOGSECRET", "OTHER": "ordinary-value"}
        found = alert_message.secret_values(env)
        self.assertEqual(set(found), {"GEMINI-SECRET-VALUE", "ATTIO-SECRET-VALUE",
                                      "https://hooks.slack.com/services/T/B/LOGSECRET"})


class MainTests(NoNetwork):
    SECRETS = {"GEMINI_API_KEY": "GEMINI-SECRET-VALUE", "SLACK_LOG_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/LOGSECRET"}

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def env(self, **more):
        env = {"RUNNER_TEMP": str(self.folder), "RUN_URL": RUN_URL, **self.SECRETS}
        env.update(more)
        return env

    def log(self, name, text):
        (self.folder / name).write_text(text, encoding="utf-8")

    def run_main(self, step, env=None, details=None):
        out = io.StringIO()
        calls = []

        def fake_details(step_name):
            calls.append(step_name)
            return details(step_name) if callable(details) else details

        code = alert_message.main([step], env if env is not None else self.env(), out, fake_details)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("\n"), 1, "exactly one line of output")
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload), {"text"})
        return payload["text"], calls

    def test_a_triage_failure_has_every_line(self):
        self.log("triage.log", "Email: e-1a2b3c\nGemini did not accept the key. Check it and try again.\n")
        text, calls = self.run_main("Triage", details=((3, "the Inbox"), ("Sam Fake", "Demo please")))
        self.assertEqual(calls, ["Triage"])
        self.assertEqual(lines_of(text), [
            "Pipeline step failed: Triage.", "Reason: Gemini rejected the key", "Waiting: 3 in the Inbox",
            'First waiting: Sam Fake, "Demo please"', f"Run: {RUN_URL}"])

    def test_an_attio_failure_reads_the_attio_log(self):
        self.log("triage.log", "Gemini did not accept the key.")
        self.log("attio.log", "Attio did not accept the key (code 401: the key is not valid).")
        text, _ = self.run_main("Attio sync", details=((2, "Leads"), ("Ann First", "First lead")))
        self.assertIn("Reason: Attio rejected the key", text)
        self.assertIn("Waiting: 2 in Leads", text)

    def test_when_the_details_cannot_be_worked_out_the_alert_still_posts(self):
        self.log("triage.log", "Gemini did not accept the key.")

        def broken(step_name):
            raise RuntimeError("Outlook is down RAW-DETAIL-LEAK sam@example.com")

        text, _ = self.run_main("Triage", details=broken)
        self.assertEqual(lines_of(text), ["Pipeline step failed: Triage.", "Reason: Gemini rejected the key",
                                          f"Run: {RUN_URL}"])
        self.assertNotIn("RAW-DETAIL-LEAK", text)

    def test_when_the_details_come_back_in_a_bad_shape_the_alert_still_posts(self):
        self.log("triage.log", "Gemini did not accept the key.")
        for bad in (None, "text", 5, (1,), ((1, "x"),), ("a", "b", "c", "d")):
            with self.subTest(details=bad):
                text, _ = self.run_main("Triage", details=bad)
                self.assertIn("Reason: Gemini rejected the key", text)
                self.assertIn(f"Run: {RUN_URL}", text)

    def test_a_missing_or_unreadable_log_gives_the_unknown_reason(self):
        text, _ = self.run_main("Triage", details=None)
        self.assertIn(f"Reason: {UNKNOWN}", text)
        (self.folder / "triage.log").write_bytes(b"\xff\xfe\x00 not text \x80")
        text, _ = self.run_main("Triage", details=None)
        self.assertIn(f"Reason: {UNKNOWN}", text)
        text, _ = self.run_main("Triage", env={"RUN_URL": RUN_URL}, details=None)    # no RUNNER_TEMP at all
        self.assertIn(f"Reason: {UNKNOWN}", text)

    def test_a_failed_install_or_set_up_does_not_look_in_outlook(self):
        for step, reason in (("Install libraries", "The libraries could not be installed"),
                             ("set-up (checkout or Python)", "Set-up failed before the pipeline started")):
            with self.subTest(step=step):
                text, calls = self.run_main(step, details=((1, "x"), ("A", "B")))
                self.assertEqual(calls, [], "no Outlook lookup for a step that cannot have handled any email")
                self.assertEqual(lines_of(text), [f"Pipeline step failed: {step}.", f"Reason: {reason}", f"Run: {RUN_URL}"])

    def test_an_unknown_step_name_still_posts(self):
        text, calls = self.run_main("Something else", details=None)
        self.assertIn("Pipeline step failed: Something else.", text)
        self.assertEqual(calls, [])

    def test_the_output_never_holds_an_address_a_secret_or_the_log_text(self):
        self.log("triage.log", "RAW-LOG-LINE sam@example.com GEMINI-SECRET-VALUE\nGemini did not accept the key.")
        text, _ = self.run_main("Triage", details=((1, "the Inbox"),
                                                   ("sam@example.com GEMINI-SECRET-VALUE", "https://hooks.slack.com/services/T/B/LOGSECRET")))
        for leaked in ("RAW-LOG-LINE", "sam@example.com", "GEMINI-SECRET-VALUE", "LOGSECRET", "hooks.slack.com"):
            self.assertNotIn(leaked, text)

    def test_nothing_the_lookup_prints_reaches_the_output(self):
        self.log("triage.log", "Gemini did not accept the key.")

        def noisy(step_name):
            print("Note: running on GitHub Actions, so the refreshed sign-in was not saved. NOISE-FROM-LOOKUP")
            print("PRIVATE-LINE sam@example.com", file=sys.stderr)
            return ((1, "the Inbox"), ("Sam", "Hi"))

        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            text, _ = self.run_main("Triage", details=noisy)
        self.assertNotIn("NOISE-FROM-LOOKUP", text)
        self.assertEqual(stderr.getvalue(), "", "the lookup's own error output is swallowed too")

    def test_main_never_fails_whatever_it_is_given(self):
        for argv, env in (([], {}), ([""], {}), (["Triage"], None), (None, None), (["Triage", "x"], {"RUNNER_TEMP": None})):
            with self.subTest(argv=argv):
                out = io.StringIO()
                code = alert_message.main(argv, env if env is not None else {}, out, lambda step: 1 / 0)
                self.assertEqual(code, 0)
                self.assertIn("text", json.loads(out.getvalue()))

    def test_the_default_lookup_is_used_when_none_is_given(self):
        out = io.StringIO()
        with mock.patch.object(alert_message, "find_details", lambda step: ((4, "Leads"), ("Ann", "Hi"))):
            alert_message.main(["Attio sync"], self.env(), out)
        self.assertIn("Waiting: 4 in Leads", json.loads(out.getvalue())["text"])


class FindDetailsTests(NoNetwork):
    """find_details signs in quietly and READS the mailbox. Here every Outlook call is pretend."""

    def pretend(self, inbox=(), leads=()):
        import leads_to_attio
        import outlook_to_slack
        import outlook_triage

        calls = []
        patches = [
            mock.patch.object(outlook_to_slack, "get_access_token", lambda env_text, client_id: calls.append("token") or "FAKE-TOKEN"),
            mock.patch.object(alert_message, "_sign_in_settings", lambda: ("", "FAKE-CLIENT")),
            mock.patch.object(outlook_triage, "list_inbox", lambda token, since: calls.append("inbox") or list(inbox)),
            mock.patch.object(outlook_triage, "now", lambda: NOW),
            mock.patch.object(leads_to_attio, "find_leads_folder", lambda token: calls.append("folder") or "leads-id"),
            mock.patch.object(leads_to_attio, "list_leads", lambda token, folder_id: calls.append("leads") or list(leads)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return calls

    def test_the_triage_lookup_reads_the_inbox_only(self):
        calls = self.pretend(inbox=[message("a", "First one", "Eve Early"), message("b", "Second", "Bo")])
        self.assertEqual(alert_message.find_details("Triage"), ((2, "the Inbox"), ("Eve Early", "First one")))
        self.assertIn("inbox", calls)
        self.assertNotIn("leads", calls)

    def test_the_attio_lookup_reads_the_leads_folder_only(self):
        calls = self.pretend(leads=[message("a", "A lead", "Ann First")])
        self.assertEqual(alert_message.find_details("Attio sync"), ((1, "Leads"), ("Ann First", "A lead")))
        self.assertIn("leads", calls)
        self.assertNotIn("inbox", calls)

    def test_an_empty_list_still_gives_the_count_and_no_first_email(self):
        self.pretend(inbox=[])
        self.assertEqual(alert_message.find_details("Triage"), ((0, "the Inbox"), None))

    def test_a_step_without_a_lookup_gives_nothing(self):
        self.pretend()
        self.assertEqual(alert_message.find_details("Install libraries"), (None, None))

    def test_a_lookup_failure_is_raised_so_main_can_leave_the_lines_out(self):
        import outlook_to_slack
        self.pretend()
        with mock.patch.object(outlook_to_slack, "get_access_token", side_effect=RuntimeError("down")):
            with self.assertRaises(Exception):
                alert_message.find_details("Triage")


class SafetyTextTests(unittest.TestCase):
    def source(self):
        return (WORKFLOWS / "02-email-to-attio" / "alert_message.py").read_text(encoding="utf-8")

    def test_the_code_only_reads_outlook(self):
        text = self.source().lower()
        for pattern in (r"\"post\"", r"\"patch\"", r"\"put\"", r"\"delete\"", r"/send(?![a-z])", r"sendmail", r"/reply",
                        r"/forward", r"/move", r"/copy", r"mail\.send", r"hooks\.slack\.com/services/[a-z0-9]"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in alert_message.py")

    def test_the_code_posts_nothing_and_writes_no_file(self):
        text = self.source()
        for pattern in (r"urllib\.request", r"\brequests\b", r"\.write_text", r"\.write_bytes", r"open\([^)]*['\"][wax]"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in alert_message.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
