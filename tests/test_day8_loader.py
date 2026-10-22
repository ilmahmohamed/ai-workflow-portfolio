"""Pretend-data checks for workflows/02-email-to-attio/day8_loader.py (not written yet).

These follow tests/cases/14-day8-loader.md. They say what the new one-off script must offer:

  day8_loader.TAG                      "[DAY8]"
  day8_loader.PILOT_IDS                (9, 5): a pricing lead and an out-of-office reply
  day8_loader.read_dataset(path=None)  the 30 emails of tests/cases/lead-emails.md as a list of dicts with
                                       id, from_line, from_address, subject, body (LoaderProblem if damaged)
  day8_loader.build_mime(email)        the MIME text (bytes) that creates one tagged message
  day8_loader.graph(token, method, path, params=None, mime=None)   the guard; refused requests raise
                                       day8_loader.BlockedRequest before anything leaves the computer
  day8_loader.main()                   command line: [--live] [--pilot | --only 9,5]; returns 0 or 1
  day8_loader.LoaderProblem, day8_loader.BlockedRequest

Everything here is fake and self-contained: a strict pretend Outlook, the internet blocked, a throwaway .env,
and any attempt to touch the real .env or to write a file stops the test.

Run:  python tests\\test_day8_loader.py
"""

import base64
import copy
import email
import email.policy
import io
import json
import os
import re
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / "workflows"
sys.path.insert(0, str(WORKFLOWS / "01-outlook-to-slack"))
sys.path.insert(0, str(WORKFLOWS / "02-email-to-attio"))

import day8_loader  # noqa: E402
import outlook_signin  # noqa: E402
import outlook_to_slack  # noqa: E402

REAL_ENV = (PROJECT_ROOT / ".env").resolve()
FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"
FAKE_CACHE = "FAKE-TOKEN-CACHE-SECRET"
FAKE_ENV = {"MS_CLIENT_ID": "fake-client-id", "MS_TOKEN_CACHE": FAKE_CACHE}
TAG = "[DAY8]"
LIST_PARAMS = {"$filter": ["startswith(subject,'[DAY8]')"], "$select": ["id,subject"], "$top": ["100"]}


def http_error(code):
    return urllib.error.HTTPError("https://graph.test/x", code, "pretend", {}, io.BytesIO(b""))


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class PretendOutlook:
    """A strict pretend mailbox: Inbox, Leads and Leads/Processed. Records every request.

    create_errors: {n: error} makes the n-th create request fail. read_error: raised by any list request."""

    def __init__(self, inbox=(), leads=(), processed=(), create_errors=None, read_error=None):
        self.folders = {
            "inbox": [dict(m) for m in inbox],
            "leads-id": [dict(m) for m in leads],
            "processed-id": [dict(m) for m in processed],
        }
        self.create_errors = create_errors or {}
        self.read_error = read_error
        self.requests = []
        self.created = []          # the messages that were created (parsed back from the MIME sent)
        self.create_attempts = 0
        self.unexpected = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        method = request.get_method()
        parsed = urllib.parse.urlparse(request.full_url)
        path = parsed.path.replace("/v1.0", "", 1)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if method == "GET" and path == "/me/mailFolders":
            folders = [{"id": "inbox-id", "displayName": "Inbox"}, {"id": "leads-id", "displayName": "Leads"}]
            return FakeResponse(json.dumps({"value": folders}).encode())
        if method == "GET" and path == "/me/mailFolders/inbox/childFolders":
            return FakeResponse(json.dumps({"value": []}).encode())
        if method == "GET" and path == "/me/mailFolders/leads-id/childFolders":
            return FakeResponse(json.dumps({"value": [{"id": "processed-id", "displayName": "Processed"}]}).encode())

        found = re.fullmatch(r"/me/mailFolders/([^/]+)/messages", path)
        if method == "GET" and found:
            folder = urllib.parse.unquote(found.group(1))
            if query != LIST_PARAMS or folder not in self.folders:
                raise http_error(400)
            if self.read_error is not None:
                raise self.read_error
            rows = [{"id": m["id"], "subject": m["subject"]} for m in self.folders[folder]
                    if m["subject"].startswith(TAG)]
            return FakeResponse(json.dumps({"value": rows}).encode())

        if method == "POST" and path == "/me/mailFolders/inbox/messages":
            if request.get_header("Content-type") != "text/plain" or query:
                raise http_error(400)
            self.create_attempts += 1
            error = self.create_errors.get(self.create_attempts)
            if error is not None:
                raise error
            try:
                raw = base64.b64decode(request.data, validate=True)
            except Exception:
                raise http_error(400)
            parsed_message = email.message_from_bytes(raw, policy=email.policy.default)
            body = parsed_message.get_body(preferencelist=("plain",))
            record = {
                "id": f"new-{len(self.created) + 1}",
                "subject": str(parsed_message["Subject"]),
                "from": str(parsed_message["From"]),
                "to": str(parsed_message["To"] or ""),
                "cc": parsed_message["Cc"], "bcc": parsed_message["Bcc"],
                "multipart": parsed_message.is_multipart(),
                "body": body.get_content() if body is not None else "",
                "isDraft": True,
            }
            self.created.append(record)
            self.folders["inbox"].append({"id": record["id"], "subject": record["subject"]})
            return FakeResponse(json.dumps({"id": record["id"], "isDraft": True}).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]


def same_text(text):
    """Text with the line-ending style ignored (real email uses Windows-style endings) and trimmed."""
    return text.replace(chr(13) + chr(10), chr(10)).strip()


def touches_real_env(path):
    try:
        return str(Path(path).resolve()).lower().startswith(str(REAL_ENV).lower())
    except (OSError, ValueError, TypeError):
        return False


class Day8LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = day8_loader.read_dataset()
        cls.by_id = {e["id"]: e for e in cls.dataset}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env_path = self.tmp / ".env"
        self.env_path.write_text("MS_CLIENT_ID=fake-from-file\n", encoding="utf-8")
        self.assertFalse(touches_real_env(self.env_path))
        for module in (outlook_signin, outlook_to_slack):
            patcher = mock.patch.object(module, "ENV_PATH", self.env_path)
            patcher.start()
            self.addCleanup(patcher.stop)

        # Tripwires: the real .env is never opened, and no file may be written.
        real_open = open

        def guarded_open(file, mode="r", *args, **kwargs):
            if touches_real_env(file):
                raise AssertionError("TEST SAFETY: the real .env must not be touched")
            if any(flag in mode for flag in "wax+") and not str(file).startswith(str(self.tmp)):
                raise AssertionError("the loader must not write any file")
            return real_open(file, mode, *args, **kwargs)

        patcher = mock.patch("builtins.open", guarded_open)
        patcher.start()
        self.addCleanup(patcher.stop)

        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch("time.sleep", lambda seconds: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, outlook, argv=(), token_error=None):
        buffer = io.BytesIO()
        out = io.TextIOWrapper(buffer, encoding="utf-8", write_through=True)
        if token_error is not None:
            token_patch = mock.patch.object(day8_loader, "get_access_token", side_effect=token_error)
        else:
            token_patch = mock.patch.object(day8_loader, "get_access_token", return_value=FAKE_TOKEN)
        with mock.patch.dict(os.environ, FAKE_ENV), \
                mock.patch.object(sys, "argv", ["day8_loader.py", *argv]), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch("urllib.request.urlopen", outlook.urlopen), \
                token_patch:
            code = day8_loader.main()
        output = buffer.getvalue().decode("utf-8")
        self.assertNotIn(FAKE_TOKEN, output, "a token was printed")
        self.assertNotIn(FAKE_CACHE, output, "a token was printed")
        self.assertEqual(outlook.unexpected, [], "an unexpected request was sent")
        return code, output

    # ---- Case L1: reading the 30 emails -------------------------------------------------

    def test_l1_the_thirty_emails_are_read_as_written(self):
        self.assertEqual([e["id"] for e in self.dataset], list(range(1, 31)))
        for e in self.dataset:
            self.assertIn("@", e["from_address"])
            self.assertEqual(e["from_address"], e["from_address"].lower().strip())
            self.assertTrue(e["subject"].strip())
            self.assertTrue(e["body"].strip())
        self.assertEqual(len({e["subject"] for e in self.dataset}), 30)
        nine = self.by_id[9]
        self.assertEqual(nine["from_address"], "jonas.meyer@example.com")
        self.assertIn("Jonas Meyer", nine["from_line"])
        self.assertEqual(nine["subject"], "Pricing request")
        self.assertIn("Blue Reed Yoga", nine["body"])
        self.assertEqual(day8_loader.TAG, TAG)
        self.assertEqual(tuple(day8_loader.PILOT_IDS), (9, 5))

    def test_l1_one_email_has_only_an_address_in_its_from_line(self):
        plain = [e for e in self.dataset if "<" not in e["from_line"]]
        self.assertEqual(len(plain), 1)
        self.assertEqual(plain[0]["from_line"].strip(), plain[0]["from_address"])

    def test_l1_a_damaged_file_is_refused(self):
        cases = {
            "no from": "# Set\n\n-----\n\n## Email 1\nSubject: Hello\nBody:\nText\n",
            "no subject": "# Set\n\n-----\n\n## Email 1\nFrom: A <a@example.com>\nBody:\nText\n",
            "no emails": "# Set\n\nnothing here\n",
            "no body": "# Set\n\n-----\n\n## Email 1\nFrom: A <a@example.com>\nSubject: Hello\n",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                path = self.tmp / "bad.md"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(day8_loader.LoaderProblem):
                    day8_loader.read_dataset(path)

    # ---- Case L2: what is created -------------------------------------------------------------

    def parse(self, e):
        return email.message_from_bytes(day8_loader.build_mime(e), policy=email.policy.default)

    def test_l2_each_message_has_the_same_from_the_tagged_subject_and_the_same_body(self):
        for e in self.dataset:
            parsed = self.parse(e)
            self.assertEqual(str(parsed["Subject"]), f"{TAG} {e['subject']}", e["id"])
            sender = parsed["From"].addresses[0]
            self.assertEqual(sender.addr_spec.lower(), e["from_address"], e["id"])
            if "<" in e["from_line"]:
                name = e["from_line"].split("<")[0].strip().strip('"')
                self.assertEqual(sender.display_name, name, e["id"])
            self.assertEqual(same_text(parsed.get_body(preferencelist=("plain",)).get_content()), same_text(e["body"]), e["id"])

    def test_l2_accented_letters_are_kept(self):
        accented = [e for e in self.dataset if any(ord(c) > 127 for c in e["subject"] + e["body"] + e["from_line"])]
        self.assertEqual(len(accented), 2)
        for e in accented:
            parsed = self.parse(e)
            self.assertEqual(str(parsed["Subject"]), f"{TAG} {e['subject']}")
            self.assertEqual(same_text(parsed.get_body(preferencelist=("plain",)).get_content()), same_text(e["body"]))

    def test_l2_the_recipient_is_made_up_and_there_is_nothing_else(self):
        for e in self.dataset:
            parsed = self.parse(e)
            self.assertTrue(str(parsed["To"]).strip().endswith(".example>") or ".example" in str(parsed["To"]))
            self.assertIsNone(parsed["Cc"])
            self.assertIsNone(parsed["Bcc"])
            self.assertFalse(parsed.is_multipart())
            self.assertEqual(list(parsed.iter_attachments()), [])

    def test_l2_it_is_sent_as_base64_mime_text(self):
        outlook = PretendOutlook()
        code, _ = self.run_main(outlook, ["--live", "--only", "9"])
        self.assertEqual(code, 0)
        post = outlook.changes()[0]
        self.assertEqual(post.get_header("Content-type"), "text/plain")
        base64.b64decode(post.data, validate=True)

    # ---- Case L3: dry run by default ---------------------------------------------------------------

    def test_l3_a_dry_run_only_reads_and_lists_what_it_would_create(self):
        outlook = PretendOutlook()
        code, output = self.run_main(outlook, [])
        self.assertEqual(code, 0)
        self.assertEqual(outlook.changes(), [])
        self.assertEqual(outlook.created, [])
        self.assertIn("Dry run", output)
        self.assertEqual(output.count("would create: "), 30)
        self.assertIn(f"would create: jonas.meyer@example.com, {TAG} Pricing request", output)
        self.assertNotIn("Blue Reed Yoga", output, "no email body may be printed")
        self.assertIn("--live", output)

    def test_l3_a_dry_run_reads_the_inbox_leads_and_processed(self):
        outlook = PretendOutlook()
        self.run_main(outlook, [])
        folders = set()
        for request in outlook.requests:
            found = re.search(r"/me/mailFolders/([^/]+)/messages$", urllib.parse.urlparse(request.full_url).path)
            if found:
                folders.add(urllib.parse.unquote(found.group(1)))
        self.assertEqual(folders, {"inbox", "leads-id", "processed-id"})

    # ---- Case L4: --live creates exactly the chosen emails -----------------------------------------

    def test_l4_live_creates_all_thirty_in_the_inbox_only(self):
        outlook = PretendOutlook()
        code, output = self.run_main(outlook, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.created), 30)
        for request in outlook.changes():
            self.assertEqual(request.get_method(), "POST")
            self.assertTrue(request.full_url.split("?")[0].endswith("/me/mailFolders/inbox/messages"))
        self.assertEqual(output.count("created: "), 30)
        self.assertIn("30 created", output)
        self.assertIn("drafts", output)
        for e in self.dataset:
            made = [m for m in outlook.created if m["subject"] == f"{TAG} {e['subject']}"]
            self.assertEqual(len(made), 1)
            self.assertEqual(same_text(made[0]["body"]), same_text(e["body"]))

    def test_l4_the_pilot_is_exactly_emails_9_and_5(self):
        for argv in (["--live", "--pilot"], ["--live", "--only", "9,5"], ["--live", "--only", "5, 9"]):
            with self.subTest(argv=argv):
                outlook = PretendOutlook()
                code, output = self.run_main(outlook, argv)
                self.assertEqual(code, 0)
                subjects = sorted(m["subject"] for m in outlook.created)
                self.assertEqual(subjects, sorted([f"{TAG} {self.by_id[9]['subject']}", f"{TAG} {self.by_id[5]['subject']}"]))
                self.assertIn("2 created", output)

    def test_l4_a_dry_run_of_the_pilot_shows_two_lines(self):
        outlook = PretendOutlook()
        code, output = self.run_main(outlook, ["--pilot"])
        self.assertEqual(code, 0)
        self.assertEqual(output.count("would create: "), 2)
        self.assertEqual(outlook.created, [])

    def test_l4_an_unknown_or_unreadable_number_is_refused_before_any_request(self):
        for argument in ("31", "0", "-1", "abc", "9,,5", "", " ", "9,31"):
            with self.subTest(only=argument):
                outlook = PretendOutlook()
                code, output = self.run_main(outlook, ["--live", "--only", argument])
                self.assertEqual(code, 1)
                self.assertEqual(outlook.requests, [])
                self.assertIn("--only", output)
                self.assertNotIn("Traceback", output)

    def test_l4_pilot_and_only_together_are_refused(self):
        outlook = PretendOutlook()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                self.run_main(outlook, ["--pilot", "--only", "1"])
        self.assertEqual(outlook.requests, [])

    # ---- Case L5: never twice ----------------------------------------------------------------------------

    def test_l5_emails_already_loaded_are_skipped_wherever_they_are(self):
        nine = f"{TAG} {self.by_id[9]['subject']}"
        five = f"{TAG} {self.by_id[5]['subject']}"
        for where in ("inbox", "leads", "processed"):
            with self.subTest(where=where):
                outlook = PretendOutlook(**{where: [{"id": "old-1", "subject": nine}]})
                code, output = self.run_main(outlook, ["--live", "--pilot"])
                self.assertEqual(code, 0)
                self.assertEqual([m["subject"] for m in outlook.created], [five])
                self.assertIn("already loaded", output)
                self.assertIn("1 created", output)

    def test_l5_running_live_twice_creates_nothing_the_second_time(self):
        outlook = PretendOutlook()
        self.run_main(outlook, ["--live"])
        before = len(outlook.created)
        code, output = self.run_main(outlook, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.created), before)
        self.assertEqual(output.count("already loaded"), 30)
        self.assertIn("0 created", output)

    def test_l5_a_dry_run_also_says_which_are_already_loaded(self):
        nine = f"{TAG} {self.by_id[9]['subject']}"
        outlook = PretendOutlook(inbox=[{"id": "old", "subject": nine}])
        code, output = self.run_main(outlook, ["--pilot"])
        self.assertEqual(code, 0)
        self.assertIn("already loaded", output)
        self.assertEqual(output.count("would create: "), 1)

    def test_l5_other_emails_with_the_tag_in_the_middle_do_not_count(self):
        outlook = PretendOutlook(inbox=[{"id": "x", "subject": f"Re: {TAG} {self.by_id[9]['subject']}"}])
        code, _ = self.run_main(outlook, ["--live", "--only", "9"])
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.created), 1)

    # ---- Case L6: when Outlook has a problem ------------------------------------------------------------------

    def test_l6_a_refused_create_stops_with_a_plain_message(self):
        for code_number in (401, 403):
            with self.subTest(code=code_number):
                outlook = PretendOutlook(create_errors={1: http_error(code_number)})
                code, output = self.run_main(outlook, ["--live", "--pilot"])
                self.assertEqual(code, 1)
                self.assertIn("sign-in may need redoing", output)
                self.assertIn("outlook_signin.py", output)
                self.assertNotIn(str(code_number), output)
                self.assertNotIn("HTTPError", output)
                self.assertNotIn("Traceback", output)
                self.assertEqual(outlook.create_attempts, 1, "no second try in the same run")

    def test_l6_other_errors_and_lost_connections_are_explained(self):
        cases = [
            (dict(create_errors={1: http_error(500)}), "refused"),
            (dict(create_errors={1: urllib.error.URLError("pretend: down")}), "couldn't reach Outlook"),
            (dict(read_error=http_error(500)), "refused"),
            (dict(read_error=urllib.error.URLError("pretend: down")), "couldn't reach Outlook"),
            (dict(read_error=http_error(403)), "sign-in may need redoing"),
        ]
        for options, words in cases:
            with self.subTest(options=list(options)):
                outlook = PretendOutlook(**options)
                code, output = self.run_main(outlook, ["--live", "--pilot"])
                self.assertEqual(code, 1)
                self.assertIn(words, output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("URLError", output)

    def test_l6_a_problem_part_way_keeps_the_earlier_ones_and_the_next_run_carries_on(self):
        outlook = PretendOutlook(create_errors={3: http_error(500)})
        code, output = self.run_main(outlook, ["--live"])
        self.assertEqual(code, 1)
        self.assertEqual(len(outlook.created), 2)
        self.assertEqual(outlook.create_attempts, 3, "it stops at the first problem")
        self.assertIn("2 created", output)
        outlook.create_errors.clear()
        code, output = self.run_main(outlook, ["--live"])
        self.assertEqual(code, 0)
        self.assertEqual(len(outlook.created), 30)
        self.assertEqual(output.count("already loaded"), 2)

    def test_l6_an_expired_sign_in_stops_before_any_request(self):
        outlook = PretendOutlook()
        error = outlook_signin.SetupProblem("Your saved sign-in no longer works (reason: x). Please run outlook_signin.py again.")
        code, output = self.run_main(outlook, ["--live"], token_error=error)
        self.assertEqual(code, 1)
        self.assertIn("no longer works", output)
        self.assertEqual(outlook.requests, [])

    # ---- Case L7: only safe requests -----------------------------------------------------------------------------

    def test_l7_the_guard_refuses_everything_else_before_it_leaves_the_computer(self):
        mime = base64.b64encode(b"From: a@example.com\r\n\r\nx")
        good_params = {"$filter": "startswith(subject,'[DAY8]')", "$select": "id,subject", "$top": "100"}
        refused = [
            ("POST", "/me/mailFolders/drafts/messages", None, mime),
            ("POST", "/me/mailFolders/leads-id/messages", None, mime),
            ("POST", "/me/mailFolders/deleteditems/messages", None, mime),
            ("POST", "/me/messages", None, mime),
            ("POST", "/me/mailFolders/inbox/messages", {"x": "1"}, mime),
            ("POST", "/me/mailFolders/inbox/messages", None, None),
            ("POST", "/me/mailFolders/inbox/messages", None, b""),
            ("POST", "/me/messages/abc/send", None, mime),
            ("POST", "/me/sendMail", None, mime),
            ("POST", "/me/messages/abc/reply", None, mime),
            ("POST", "/me/messages/abc/replyAll", None, mime),
            ("POST", "/me/messages/abc/forward", None, mime),
            ("POST", "/me/messages/abc/createReply", None, mime),
            ("POST", "/me/messages/abc/move", None, mime),
            ("POST", "/me/messages/abc/copy", None, mime),
            ("POST", "/me/messages/abc/extensions", None, mime),
            ("DELETE", "/me/messages/abc", None, None),
            ("PATCH", "/me/messages/abc", None, mime),
            ("PUT", "/me/mailFolders/inbox/messages", None, mime),
            ("GET", "/me/messages", good_params, None),
            ("GET", "/me/messages/abc", None, None),
            ("GET", "/me/mailFolders/inbox/messages", None, None),
            ("GET", "/me/mailFolders/inbox/messages", {**good_params, "$select": "id,subject,body"}, None),
            ("GET", "/me/mailFolders/inbox/messages", {**good_params, "$top": "500"}, None),
            ("GET", "/me/mailFolders/inbox/messages", {**good_params, "$filter": "isRead eq false"}, None),
            ("GET", "/me/mailFolders/inbox/messages", {**good_params, "$expand": "extensions"}, None),
            ("GET", "/me/mailFolders/inbox/childFolders", good_params, None),
            ("GET", "/me/messages/abc/attachments", good_params, None),
        ]
        for method, path, params, mime_body in refused:
            with self.subTest(method=method, path=path, params=params):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(day8_loader.BlockedRequest):
                        day8_loader.graph(FAKE_TOKEN, method, path, params=params, mime=mime_body)
                self.assertEqual(calls, [], "a refused request must never leave the code")

    def test_l7_the_guard_lets_the_wanted_requests_through(self):
        mime = base64.b64encode(b"From: a@example.com\r\n\r\nx")
        good_params = {"$filter": "startswith(subject,'[DAY8]')", "$select": "id,subject", "$top": "100"}
        allowed = [
            ("GET", "/me/mailFolders/inbox/messages", good_params, None),
            ("GET", "/me/mailFolders/AAMk%3D/messages", good_params, None),
            ("POST", "/me/mailFolders/inbox/messages", None, mime),
        ]
        for method, path, params, mime_body in allowed:
            with self.subTest(method=method, path=path):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    day8_loader.graph(FAKE_TOKEN, method, path, params=params, mime=mime_body)
                self.assertEqual(len(calls), 1)

    def test_l7_only_reads_and_creates_in_the_inbox_are_ever_sent(self):
        outlook = PretendOutlook()
        self.run_main(outlook, ["--live"])
        for request in outlook.requests:
            path = urllib.parse.urlparse(request.full_url).path.lower()
            for word in ("/send", "/reply", "/forward", "/move", "/copy", "/createreply", "/permanentdelete"):
                self.assertNotIn(word, path)
            self.assertIn(request.get_method(), ("GET", "POST"))
            if request.get_method() == "POST":
                self.assertTrue(path.endswith("/me/mailfolders/inbox/messages"))

    def test_l7_the_code_text_has_no_send_reply_forward_move_copy_or_delete(self):
        text = (WORKFLOWS / "02-email-to-attio" / "day8_loader.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreply",
            r"/forward", r"createforward", r"/move", r"/copy", r"permanentdelete",
            r"\"delete\"", r"\"patch\"", r"\"put\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in day8_loader.py")

    def test_l7_the_permission_list_is_still_exactly_mail_readwrite(self):
        self.assertEqual(outlook_signin.SCOPES, ["Mail.ReadWrite"])

    # ---- Case L8: no leaks ---------------------------------------------------------------------------------------

    def test_l8_no_token_no_body_no_file_and_the_real_env_is_never_opened(self):
        # guarded_open (see setUp) fails the test if the real .env is opened or any file is written.
        outlook = PretendOutlook()
        code, output = self.run_main(outlook, ["--live", "--pilot"])
        self.assertEqual(code, 0)
        for e in (self.by_id[9], self.by_id[5]):
            self.assertNotIn(e["body"].strip()[:40], output)
        self.assertEqual(list(self.tmp.iterdir()), [self.env_path])


if __name__ == "__main__":
    unittest.main(verbosity=2)
