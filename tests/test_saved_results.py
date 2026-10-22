"""Pretend-data checks for workflows/02-email-to-attio/saved_results.py.

These follow tests/cases/08-saved-results.md.
Everything here is fake and self-contained:
  - a pretend Outlook answers in place of the real one,
  - the internet is blocked, so nothing can leave this computer,
  - while saving or reading, any attempt to open a local file stops the test with an error.

Run:  python tests\\test_saved_results.py
"""

import copy
import io
import json
import re
import socket
import sys
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / "workflows"
sys.path.insert(0, str(WORKFLOWS / "01-outlook-to-slack"))
sys.path.insert(0, str(WORKFLOWS / "02-email-to-attio"))

import saved_results  # noqa: E402

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-SECRET"

LEAD = {
    "is_lead": True,
    "category": "Lead - Demo",
    "name": "Fake Person",
    "email": "fake.person@example.com",
    "company": None,
    "role": "Studio owner",
    "request_type": "demo",
    "urgency": 2,
    "summary": "Pide una demostración para su estudio de yoga, ¿cuándo podemos hablar? Café incluido.",
    "draft_reply": "Hello Fake, thank you for getting in touch about a demo. A member of our team will follow up.",
}
NOT_A_LEAD = {"is_lead": False, "category": "Not a lead"}

OTHER_APP = {
    "@odata.type": "#microsoft.graph.openTypeExtension",
    "extensionName": "com.other.app",
    "id": "Microsoft.OutlookServices.OpenTypeExtension.com.other.app",
    "data": "not ours",
}


def http_error(code):
    return urllib.error.HTTPError("https://graph.test/x", code, "pretend", {}, io.BytesIO(b""))


def our_extension(data, version=1):
    return {
        "@odata.type": "#microsoft.graph.openTypeExtension",
        "extensionName": saved_results.EXTENSION_NAME,
        "id": "Microsoft.OutlookServices.OpenTypeExtension." + saved_results.EXTENSION_NAME,
        "version": version,
        "data": data,
    }


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
    """A pretend mailbox of emails with extensions. Records every request made to it."""

    def __init__(self, extensions=None, get_error=None, post_error=None, unreachable=False):
        # {message id: [extension, ...]}  (an id that is not here does not exist)
        self.extensions = {k: copy.deepcopy(v) for k, v in (extensions or {"msg-1": []}).items()}
        self.get_error = get_error
        self.post_error = post_error
        self.unreachable = unreachable
        self.requests = []
        self.post_bodies = []
        self.unexpected = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        if self.unreachable:
            raise urllib.error.URLError("pretend: no internet")
        method = request.get_method()
        path = urllib.parse.urlparse(request.full_url).path

        found = re.search(r"/me/messages/([^/]+)/extensions/([^/]+)$", path)
        if found and method == "GET":
            # Strict, like the real Outlook: only the documented form (one extension, read by its
            # name, no query string) is answered. Anything else gets the 400 the real one gave.
            if urllib.parse.urlparse(request.full_url).query:
                raise http_error(400)
            if self.get_error is not None:
                raise self.get_error
            message_id = urllib.parse.unquote(found.group(1))
            wanted = urllib.parse.unquote(found.group(2))
            for extension in self.extensions.get(message_id, []):
                if extension.get("extensionName") == wanted:
                    return FakeResponse(json.dumps(copy.deepcopy(extension)).encode())
            raise http_error(404)

        if method == "GET" and "$expand" in urllib.parse.urlparse(request.full_url).query:
            self.unexpected.append(f"{method} {request.full_url}")
            raise http_error(400)

        found = re.search(r"/me/messages/([^/]+)/extensions$", path)
        if found and method == "POST":
            message_id = urllib.parse.unquote(found.group(1))
            body = json.loads(request.data.decode())
            self.post_bodies.append(body)
            if self.post_error is not None:
                raise self.post_error
            if message_id in self.extensions:
                stored = {
                    "@odata.type": "#microsoft.graph.openTypeExtension",
                    "id": "Microsoft.OutlookServices.OpenTypeExtension." + body["extensionName"],
                    **{k: v for k, v in body.items() if k != "@odata.type"},
                }
                self.extensions[message_id].append(stored)
                return FakeResponse(json.dumps(stored).encode())

        self.unexpected.append(f"{method} {request.full_url}")
        raise urllib.error.URLError("pretend world: unexpected request")

    def methods(self):
        return [r.get_method() for r in self.requests]

    def changes(self):
        return [r for r in self.requests if r.get_method() != "GET"]


class SavedResultsTests(unittest.TestCase):
    def setUp(self):
        def no_network(*args, **kwargs):
            raise AssertionError("TEST SAFETY: the internet is blocked in these tests")

        patcher = mock.patch.object(socket.socket, "connect", no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, world, function, *args):
        """Call a saved_results function against the pretend Outlook.

        While it runs, opening any local file stops the test, and anything printed is collected.
        """
        buffer = io.StringIO()

        def no_files(*a, **k):
            raise AssertionError("TEST SAFETY: saving and reading must not open any local file")

        with mock.patch("urllib.request.urlopen", world.urlopen), \
                mock.patch("builtins.open", no_files), \
                mock.patch.object(sys, "stdout", buffer):
            try:
                return function(FAKE_TOKEN, *args)
            finally:
                self.printed = buffer.getvalue()
                self.assertEqual(self.printed, "", "saving or reading must print nothing")
                self.assertEqual(world.unexpected, [], "an unexpected request was sent")

    def save(self, world, result, message_id="msg-1"):
        return self.call(world, saved_results.save_result, message_id, result)

    def load(self, world, message_id="msg-1"):
        return self.call(world, saved_results.load_result, message_id)

    # ---- Case 1: a full answer is saved on the email ------------------------

    def test_case1_one_extension_is_added_with_the_fixed_shape(self):
        world = PretendOutlook()
        self.assertTrue(self.save(world, LEAD))
        self.assertEqual(world.methods(), ["GET", "POST"])
        self.assertEqual(len(world.changes()), 1)
        request = world.changes()[0]
        self.assertTrue(request.full_url.split("?")[0].endswith("/me/messages/msg-1/extensions"))
        body = world.post_bodies[0]
        self.assertEqual(set(body), {"@odata.type", "extensionName", "version", "data"})
        self.assertEqual(body["extensionName"], saved_results.EXTENSION_NAME)
        self.assertEqual(body["version"], 1)
        self.assertIsInstance(body["data"], str)
        self.assertEqual(json.loads(body["data"]), LEAD)

    def test_case1_no_other_email_is_touched(self):
        world = PretendOutlook({"msg-1": [], "msg-2": [copy.deepcopy(OTHER_APP)]})
        self.save(world, LEAD)
        self.assertEqual(world.extensions["msg-2"], [OTHER_APP])
        for request in world.requests:
            self.assertNotIn("msg-2", request.full_url)

    # ---- Case 2: reading it back gives the same answer ----------------------------

    def test_case2_the_saved_answer_reads_back_unchanged(self):
        for result in (LEAD, NOT_A_LEAD):
            with self.subTest(category=result["category"]):
                world = PretendOutlook()
                self.save(world, result)
                loaded = self.load(world)
                self.assertEqual(loaded, result)
        self.assertIsNone(loaded.get("name"))

    def test_case2_urgency_stays_a_whole_number_and_nulls_stay_null(self):
        world = PretendOutlook()
        self.save(world, LEAD)
        loaded = self.load(world)
        self.assertIsInstance(loaded["urgency"], int)
        self.assertIsNone(loaded["company"])
        self.assertIn("demostración", loaded["summary"])

    def test_case2_reading_sends_only_read_requests(self):
        world = PretendOutlook({"msg-1": [our_extension(json.dumps(LEAD))]})
        self.load(world)
        self.assertEqual(set(world.methods()), {"GET"})
        self.assertEqual(world.changes(), [])

    def test_case2_the_read_asks_for_the_one_extension_by_its_fixed_name_with_no_query_string(self):
        world = PretendOutlook({"msg-1": [our_extension(json.dumps(LEAD))]})
        self.load(world)
        self.assertEqual(len(world.requests), 1)
        url = world.requests[0].full_url
        self.assertTrue(url.endswith("/me/messages/msg-1/extensions/" + saved_results.EXTENSION_NAME))
        self.assertNotIn("?", url)
        self.assertNotIn("$expand", url)

    # ---- Case 3: nothing saved yet ----------------------------------------------------

    def test_case3_no_extension_means_nothing_saved(self):
        self.assertIsNone(self.load(PretendOutlook({"msg-1": []})))

    def test_case3_another_programs_extension_is_ignored_and_left_alone(self):
        world = PretendOutlook({"msg-1": [copy.deepcopy(OTHER_APP)]})
        self.assertIsNone(self.load(world))
        self.assertEqual(world.extensions["msg-1"], [OTHER_APP])

    def test_case3_a_not_found_answer_means_nothing_saved_and_saving_goes_ahead(self):
        world = PretendOutlook({"msg-1": []})
        self.assertIsNone(self.load(world))
        self.assertTrue(self.save(world, LEAD))
        self.assertEqual(len(world.changes()), 1)

    def test_case3_ours_is_found_next_to_another_programs(self):
        world = PretendOutlook({"msg-1": [copy.deepcopy(OTHER_APP), our_extension(json.dumps(LEAD))]})
        self.assertEqual(self.load(world), LEAD)

    # ---- Case 4: a damaged saved answer counts as nothing saved ---------------------------

    def test_case4_damaged_answers_read_as_nothing_saved(self):
        damaged = [
            our_extension("not json {"),
            our_extension("[1, 2]"),
            our_extension("5"),
            our_extension("null"),
            our_extension(json.dumps({"category": "Lead - Demo"})),
            our_extension(json.dumps({"is_lead": "yes", "category": "Lead - Demo"})),
            our_extension(json.dumps({"is_lead": True})),
            our_extension(json.dumps(LEAD), version=2),
            our_extension(json.dumps(LEAD), version=None),
            our_extension({"is_lead": True, "category": "Lead - Demo"}),   # data is not text
            our_extension(None),
        ]
        for extension in damaged:
            with self.subTest(extension=extension):
                world = PretendOutlook({"msg-1": [extension]})
                self.assertIsNone(self.load(world))

    # ---- Case 5: saving twice does not change the first answer -----------------------------

    def test_case5_a_second_save_sends_no_change_and_keeps_the_first(self):
        world = PretendOutlook()
        self.assertTrue(self.save(world, LEAD))
        second = dict(LEAD, summary="A different summary")
        self.assertFalse(self.save(world, second))
        self.assertEqual(len(world.changes()), 1)
        self.assertEqual(self.load(world), LEAD)

    def test_case5_an_answer_already_there_is_not_added_again(self):
        world = PretendOutlook({"msg-1": [our_extension(json.dumps(LEAD))]})
        self.assertFalse(self.save(world, LEAD))
        self.assertEqual(world.changes(), [])

    # ---- Case 6: an incomplete or oversized answer is refused ---------------------------------

    def assert_refused_before_any_request(self, result, words=()):
        world = PretendOutlook()
        with self.assertRaises(saved_results.SavedResultProblem) as caught:
            self.save(world, result)
        for word in words:
            self.assertIn(word, str(caught.exception))
        self.assertEqual(world.requests, [], "a refused answer must send nothing, not even a read")

    def test_case6_a_field_that_is_not_in_the_list_is_refused(self):
        self.assert_refused_before_any_request(dict(LEAD, favourite_colour="blue"))

    def test_case6_missing_is_lead_or_category_is_refused(self):
        without_flag = {k: v for k, v in LEAD.items() if k != "is_lead"}
        without_category = {k: v for k, v in LEAD.items() if k != "category"}
        self.assert_refused_before_any_request(without_flag)
        self.assert_refused_before_any_request(without_category)

    def test_case6_is_lead_must_be_true_or_false_and_category_must_be_text(self):
        self.assert_refused_before_any_request(dict(LEAD, is_lead="yes"))
        self.assert_refused_before_any_request(dict(LEAD, is_lead=1))
        self.assert_refused_before_any_request(dict(LEAD, category=5))
        self.assert_refused_before_any_request(dict(LEAD, category=None))

    def test_case6_something_that_is_not_an_answer_is_refused(self):
        self.assert_refused_before_any_request(None)
        self.assert_refused_before_any_request(["is_lead"])

    def test_case6_an_answer_that_is_too_long_is_refused_with_a_plain_message(self):
        self.assert_refused_before_any_request(dict(LEAD, summary="x" * 9000), words=("too long",))

    def test_case6_a_long_but_allowed_answer_is_saved(self):
        world = PretendOutlook()
        self.assertTrue(self.save(world, dict(LEAD, draft_reply="y" * 3000)))

    # ---- Case 7: when Outlook has a problem ---------------------------------------------------------

    def test_case7_a_refused_sign_in_says_it_may_need_redoing(self):
        for code_number in (401, 403):
            for action in ("save", "load"):
                with self.subTest(code=code_number, action=action):
                    if action == "save":
                        world = PretendOutlook(post_error=http_error(code_number))
                        run = lambda: self.save(world, LEAD)
                    else:
                        world = PretendOutlook(get_error=http_error(code_number))
                        run = lambda: self.load(world)
                    with self.assertRaises(saved_results.SavedResultProblem) as caught:
                        run()
                    message = str(caught.exception)
                    self.assertIn("sign-in may need redoing", message)
                    self.assertIn("outlook_signin.py", message)
                    self.assertNotIn(str(code_number), message)
                    self.assertNotIn("HTTPError", message)

    def test_case7_no_internet_is_explained(self):
        for action in ("save", "load"):
            with self.subTest(action=action):
                world = PretendOutlook(unreachable=True)
                with self.assertRaises(saved_results.SavedResultProblem) as caught:
                    self.save(world, LEAD) if action == "save" else self.load(world)
                self.assertIn("couldn't reach Outlook", str(caught.exception))
                self.assertNotIn("URLError", str(caught.exception))

    def test_case7_a_failed_read_is_not_mistaken_for_nothing_saved(self):
        world = PretendOutlook(get_error=http_error(500))
        with self.assertRaises(saved_results.SavedResultProblem) as caught:
            self.load(world)
        self.assertIn("refused", str(caught.exception))

    def test_case7_a_failed_read_before_saving_stops_the_save(self):
        world = PretendOutlook(get_error=http_error(500))
        with self.assertRaises(saved_results.SavedResultProblem):
            self.save(world, LEAD)
        self.assertEqual(world.changes(), [], "nothing is saved when it could not check first")

    def test_case7_a_failed_save_leaves_nothing_behind(self):
        world = PretendOutlook(post_error=http_error(403))
        with self.assertRaises(saved_results.SavedResultProblem):
            self.save(world, LEAD)
        self.assertEqual(world.extensions["msg-1"], [])
        self.assertEqual(len(world.post_bodies), 1, "it should not try again")

    # ---- Case 8: only the allowed requests can ever be sent --------------------------------------------

    def good_extension_body(self):
        return {
            "@odata.type": "microsoft.graph.openTypeExtension",
            "extensionName": saved_results.EXTENSION_NAME,
            "version": 1,
            "data": json.dumps(LEAD),
        }

    def test_case8_the_guard_refuses_everything_else(self):
        good = self.good_extension_body()
        refused = [
            ("POST", "/me/messages/abc/send", {}),
            ("POST", "/me/sendMail", {"message": {}}),
            ("POST", "/me/messages/abc/reply", {}),
            ("POST", "/me/messages/abc/forward", {}),
            ("POST", "/me/messages/abc/createReply", {}),
            ("POST", "/me/messages/abc/move", {"destinationId": "inbox"}),
            ("POST", "/me/messages/abc/copy", {"destinationId": "inbox"}),
            ("POST", "/me/mailFolders/drafts/messages", {"subject": "x", "body": {}}),
            ("POST", "/me/messages/abc/extensions", {**good, "extensionName": "com.other.app"}),
            ("POST", "/me/messages/abc/extensions", {**good, "isRead": True}),
            ("POST", "/me/messages/abc/extensions", {k: v for k, v in good.items() if k != "data"}),
            ("POST", "/me/messages/abc/extensions", {**good, "data": {"is_lead": True}}),
            ("POST", "/me/messages/abc/extensions", {**good, "version": "1"}),
            ("POST", "/me/messages/abc/extensions", None),
            ("POST", "/me/messages/abc/extensions/extra", good),
            ("DELETE", "/me/messages/abc/extensions/" + saved_results.EXTENSION_NAME, None),
            ("DELETE", "/me/messages/abc", None),
            ("PATCH", "/me/messages/abc", {"categories": []}),
            ("PATCH", "/me/messages/abc/extensions/" + saved_results.EXTENSION_NAME, {"data": "x"}),
            ("PUT", "/me/messages/abc/extensions", good),
            ("GET", "/me/messages", None),
            ("GET", "/me/messages/abc", None),
            ("GET", "/me/mailFolders/inbox/messages", None),
            ("GET", "/me/messages/abc/attachments", None),
            ("GET", "/me/messages/abc/send", None),
            ("GET", "/me/messages/abc/extensions", None),
            ("GET", "/me/messages/abc/extensions/com.other.app", None),
            ("GET", "/me/messages/abc/extensions/" + saved_results.EXTENSION_NAME + "/extra", None),
        ]
        for method, path, body in refused:
            with self.subTest(method=method, path=path, body=body):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(saved_results.BlockedRequest):
                        saved_results.graph(FAKE_TOKEN, method, path, body=body)
                self.assertEqual(calls, [], "a refused request must never leave the script")

    def test_case8_reading_the_extension_with_any_query_string_is_refused(self):
        path = "/me/messages/abc/extensions/" + saved_results.EXTENSION_NAME
        for params in ({"$expand": "extensions"}, {"$select": "id"}):
            with self.subTest(params=params):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    with self.assertRaises(saved_results.BlockedRequest):
                        saved_results.graph(FAKE_TOKEN, "GET", path, params=params)
                self.assertEqual(calls, [])

    def test_case8_the_guard_lets_the_two_wanted_requests_through(self):
        allowed = [
            ("GET", "/me/messages/abc%3D/extensions/" + saved_results.EXTENSION_NAME, None),
            ("POST", "/me/messages/abc%3D/extensions", self.good_extension_body()),
        ]
        for method, path, body in allowed:
            with self.subTest(method=method, path=path):
                calls = []
                with mock.patch("urllib.request.urlopen", lambda *a, **k: calls.append(a) or FakeResponse(b"{}")):
                    saved_results.graph(FAKE_TOKEN, method, path, body=body)
                self.assertEqual(len(calls), 1)

    def test_case8_the_code_text_has_no_send_reply_forward_move_copy_or_delete(self):
        text = (WORKFLOWS / "02-email-to-attio" / "saved_results.py").read_text(encoding="utf-8").lower()
        patterns = [
            r"/send(?![a-z])", r"sendmail", r"/reply", r"replyall", r"createreply",
            r"/forward", r"createforward", r"/move", r"/copy", r"permanentdelete",
            r"\"delete\"", r"\"patch\"", r"\"put\"", r"mail\.send",
        ]
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"found {pattern} in saved_results.py")

    # ---- Case 9: no files, no printing, no token on screen -------------------------------------------------

    def test_case9_saving_and_reading_open_no_local_file_and_print_nothing(self):
        # self.call() fails the test if a file is opened or anything is printed.
        world = PretendOutlook()
        self.save(world, LEAD)
        self.load(world)
        self.assertEqual(self.printed, "")

    def test_case9_the_token_goes_to_outlook_but_is_never_printed(self):
        world = PretendOutlook()
        self.save(world, LEAD)
        self.assertEqual(world.requests[0].get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertNotIn(FAKE_TOKEN, self.printed)

    def test_case9_problem_messages_never_contain_the_token(self):
        for world in (PretendOutlook(post_error=http_error(403)), PretendOutlook(unreachable=True)):
            with self.assertRaises(saved_results.SavedResultProblem) as caught:
                self.save(world, LEAD)
            self.assertNotIn(FAKE_TOKEN, str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
