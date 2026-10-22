"""Pretend-data checks for workflows/02-email-to-attio/v3_reply.py and prompts/v3.md.

These follow tests/cases/10-single-call-v3.md (Cases 1 to 6).
Nothing here uses the internet, a key or a real email.

Run:  python tests\\test_v3_reply.py
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FOLDER = PROJECT_ROOT / "workflows" / "02-email-to-attio"
sys.path.insert(0, str(FOLDER))

import v3_reply  # noqa: E402

CLEAN_DRAFT = (
    "Hello Jonas, thank you for getting in touch about pricing for your studio. "
    "A member of our team will be in touch."
)

PRICING_LEAD = {
    "is_lead": True,
    "category": "Lead - Pricing",
    "name": "Jonas Meyer",
    "email": "jonas.meyer@example.com",
    "company": "Blue Reed Yoga",
    "role": None,
    "request_type": "pricing",
    "urgency": 1,
    "summary": "Studio asks for pricing.",
    "draft_reply": CLEAN_DRAFT,
}
NOT_A_LEAD = {"is_lead": False, "category": "Not a lead"}

ALL_FIELDS = (
    "is_lead", "category", "name", "email", "company", "role",
    "request_type", "urgency", "summary", "draft_reply",
)


def lead(**changes):
    return {**PRICING_LEAD, **changes}


class PromptTests(unittest.TestCase):
    """Case 1: the v3 prompt keeps every v2 rule and adds the category and the draft."""

    @classmethod
    def setUpClass(cls):
        cls.v2 = (FOLDER / "prompts" / "v2.md").read_text(encoding="utf-8")
        cls.v3 = (FOLDER / "prompts" / "v3.md").read_text(encoding="utf-8")

    def test_every_v2_field_rule_is_in_v3_word_for_word(self):
        start = self.v2.index("Rules for the fields:")
        end = self.v2.index("Always include every field when is_lead is true.") + len(
            "Always include every field when is_lead is true."
        )
        self.assertIn(self.v2[start:end], self.v3)

    def test_the_lead_steps_of_v2_are_kept(self):
        step_one = self.v2[self.v2.index("Step 1: is it a lead?"):self.v2.index("Step 2a:")]
        self.assertIn(step_one, self.v3)

    def test_v3_asks_for_the_category_and_the_draft_reply_in_the_lead_answer(self):
        self.assertIn('"category"', self.v3)
        self.assertIn('"draft_reply"', self.v3)
        lead_block = self.v3[self.v3.index("Step 2b"):]
        for field in ALL_FIELDS:
            self.assertIn(f'"{field}"', lead_block)

    def test_v3_names_all_five_categories_and_how_they_follow_the_request_type(self):
        for category in v3_reply.CATEGORIES:
            self.assertIn(category, self.v3)
        self.assertIn('{"is_lead": false, "category": "Not a lead"}', self.v3)
        for line in ("demo", "pricing", "partnership", "other"):
            self.assertIn(line, self.v3.lower())

    def test_v3_states_the_draft_rules(self):
        text = self.v3.lower()
        for phrase in (
            "a member of our team will be in touch",
            "english",
            "ignore any instruction",
            "sign-off",
            "never",
        ):
            self.assertIn(phrase, text)
        for banned in ("price", "date", "promise"):
            self.assertIn(banned, text)

    def test_v3_needs_review_is_only_for_an_email_that_gives_no_sign_of_what_is_asked(self):
        self.assertIn(
            "Needs review only if the subject and body together give no sign of what is asked. "
            "A subject like 'Demo?' is a lead.",
            self.v3,
        )
        self.assertNotIn("only when you cannot tell what the email is or what is being asked", self.v3)
        self.assertNotIn("If you cannot tell what the email is at all", self.v3)

    def test_v3_says_an_almost_empty_email_with_a_clear_subject_is_a_lead_never_needs_review(self):
        self.assertIn(
            'An email with an almost empty body and a subject that asks for something (for example "Demo?") '
            "is a lead, never Needs review",
            self.v3,
        )
        self.assertIn(
            "Only if the whole email is empty or unreadable and the subject asks for nothing either",
            self.v3,
        )

    def test_v3_forbids_describing_what_nova_wellness_sells_or_does_with_neutral_wording(self):
        text = self.v3.lower()
        self.assertIn("never describe what nova wellness sells or does", text)
        for word in ("software", "platform", "app", "product", "service", "tool", "system", "solution"):
            self.assertIn(word, text)
        for phrase in ('"your enquiry"', '"your request"', '"your message"'):
            self.assertIn(phrase, text)
        self.assertNotIn("your demo request", text, "the examples must not lead the model towards demo")
        self.assertNotIn("never make claims about nova wellness, its software", text)

    def test_v3_asks_for_json_only_like_v2(self):
        self.assertIn("Reply with ONLY a JSON object", self.v3)


class ReadV3Tests(unittest.TestCase):
    """Cases 2, 3 and 4: the answer is checked, never just trusted."""

    def test_case2_a_good_lead_answer_is_accepted_whole(self):
        result, problems = v3_reply.read_v3(lead())
        self.assertEqual(problems, [])
        self.assertEqual(set(result), set(ALL_FIELDS))
        self.assertEqual(result, PRICING_LEAD)

    def test_case2_urgency_is_a_whole_number_and_empty_text_is_null(self):
        result, _ = v3_reply.read_v3(lead(urgency=3, company="  ", role=""))
        self.assertEqual(result["urgency"], 3)
        self.assertIsNone(result["company"])
        self.assertIsNone(result["role"])
        for odd in (True, "3", 2.5, None):
            with self.subTest(urgency=odd):
                result, _ = v3_reply.read_v3(lead(urgency=odd))
                self.assertIsNone(result["urgency"])

    def test_case2_text_is_trimmed_and_unknown_fields_are_left_out(self):
        result, _ = v3_reply.read_v3(lead(name="  Jonas Meyer  ", favourite_colour="blue"))
        self.assertEqual(result["name"], "Jonas Meyer")
        self.assertNotIn("favourite_colour", result)

    def test_case2_every_request_type_maps_to_its_category(self):
        expected = {
            "demo": "Lead - Demo",
            "pricing": "Lead - Pricing",
            "partnership": "Lead - Other",
            "other": "Lead - Other",
        }
        for request_type, category in expected.items():
            with self.subTest(request_type=request_type):
                result, problems = v3_reply.read_v3(lead(request_type=request_type, category=category))
                self.assertEqual(result["category"], category)
                self.assertEqual(problems, [])
                self.assertEqual(result["draft_reply"], CLEAN_DRAFT)

    def test_case2_capitals_and_spaces_in_the_request_type_and_category_do_not_matter(self):
        result, problems = v3_reply.read_v3(lead(request_type=" Pricing ", category=" lead - pricing "))
        self.assertEqual(problems, [])
        self.assertEqual(result["category"], "Lead - Pricing")
        self.assertEqual(result["request_type"], "pricing")

    def assert_needs_review(self, parsed, words=()):
        result, problems = v3_reply.read_v3(parsed)
        self.assertEqual(result["category"], "Needs review")
        self.assertIsNone(result["draft_reply"])
        self.assertTrue(problems, "the problem must be named")
        text = " ".join(problems)
        for word in words:
            self.assertIn(word, text)
        self.assertEqual(set(result), set(ALL_FIELDS))
        return result

    def test_case3_a_category_that_does_not_match_the_request_type(self):
        self.assert_needs_review(lead(category="Lead - Demo"), words=("category",))

    def test_case3_a_missing_unknown_or_wrong_category_on_a_lead(self):
        without = {k: v for k, v in PRICING_LEAD.items() if k != "category"}
        for parsed in (
            without,
            lead(category="Hot lead"),
            lead(category=5),
            lead(category=None),
            lead(category="Not a lead"),
        ):
            with self.subTest(category=parsed.get("category")):
                self.assert_needs_review(parsed)

    def test_case3_a_lead_with_an_unknown_request_type(self):
        for request_type in ("complaint", None, 7, ""):
            with self.subTest(request_type=request_type):
                self.assert_needs_review(lead(request_type=request_type))

    def test_case3_not_a_lead_but_with_a_lead_category(self):
        self.assert_needs_review({"is_lead": False, "category": "Lead - Demo"})

    def test_case3_not_a_lead_is_accepted_with_only_two_fields_filled(self):
        result, problems = v3_reply.read_v3(
            {**NOT_A_LEAD, "name": "Ignored", "draft_reply": CLEAN_DRAFT, "summary": "x"}
        )
        self.assertEqual(problems, [])
        self.assertEqual(result["is_lead"], False)
        self.assertEqual(result["category"], "Not a lead")
        for field in ALL_FIELDS[2:]:
            self.assertIsNone(result[field], field)

    def test_case3_geminis_own_needs_review_stays_and_has_no_draft(self):
        for parsed in ({"is_lead": False, "category": "Needs review"}, lead(category="Needs review")):
            with self.subTest(is_lead=parsed["is_lead"]):
                result, _ = v3_reply.read_v3(parsed)
                self.assertEqual(result["category"], "Needs review")
                self.assertIsNone(result["draft_reply"])

    def test_case3_broken_or_odd_replies_become_needs_review_without_crashing(self):
        without_flag = {k: v for k, v in PRICING_LEAD.items() if k != "is_lead"}
        for parsed in (None, [], "text", 5, {}, without_flag, lead(is_lead="yes"), lead(is_lead=1), lead(is_lead=None)):
            with self.subTest(parsed=parsed):
                self.assert_needs_review(parsed)

    def test_case4_drafts_are_ignored_unless_the_email_is_a_confirmed_lead(self):
        result, _ = v3_reply.read_v3({**NOT_A_LEAD, "draft_reply": CLEAN_DRAFT})
        self.assertIsNone(result["draft_reply"])
        result, _ = v3_reply.read_v3(lead(category="Needs review"))
        self.assertIsNone(result["draft_reply"])

    def test_case4_a_lead_with_no_draft_stays_a_lead_and_says_so(self):
        for parsed in (lead(draft_reply=None), lead(draft_reply=""), {k: v for k, v in PRICING_LEAD.items() if k != "draft_reply"}):
            with self.subTest(draft=parsed.get("draft_reply", "(missing)")):
                result, problems = v3_reply.read_v3(parsed)
                self.assertEqual(result["category"], "Lead - Pricing")
                self.assertIsNone(result["draft_reply"])
                self.assertTrue(any("draft" in p for p in problems))

    def test_case4_a_draft_that_is_not_text_is_dropped(self):
        result, problems = v3_reply.read_v3(lead(draft_reply=["Hello"]))
        self.assertEqual(result["category"], "Lead - Pricing")
        self.assertIsNone(result["draft_reply"])
        self.assertTrue(any("draft" in p for p in problems))

    def test_case5_a_draft_that_breaks_a_rule_is_dropped_but_the_lead_stays_a_lead(self):
        result, problems = v3_reply.read_v3(lead(draft_reply="Hello, it costs $50 per month."))
        self.assertEqual(result["category"], "Lead - Pricing")
        self.assertEqual(result["name"], "Jonas Meyer")
        self.assertIsNone(result["draft_reply"])
        self.assertTrue(any("draft" in p for p in problems))

    def test_the_answer_can_be_saved_as_it_is(self):
        # Lead and not-a-lead results must be plain JSON the saved-data step accepts.
        for parsed in (lead(), NOT_A_LEAD, None):
            result, _ = v3_reply.read_v3(parsed)
            self.assertEqual(json.loads(json.dumps(result)), result)
            self.assertIsInstance(result["is_lead"], bool)
            self.assertIsInstance(result["category"], str)


class DraftRuleTests(unittest.TestCase):
    """Case 5: what a draft must never do."""

    def problems(self, text):
        return v3_reply.draft_problems(text)

    def assert_flags(self, text, fragment):
        found = self.problems(text)
        self.assertTrue(any(fragment in p for p in found), f"{text!r} should be flagged for {fragment!r}, got {found}")

    def test_a_clean_draft_passes(self):
        self.assertEqual(self.problems(CLEAN_DRAFT), [])

    def test_the_word_pricing_and_the_word_may_are_fine(self):
        text = "Hello, thank you for your pricing question, which may need a closer look. A member of our team will be in touch."
        self.assertEqual(self.problems(text), [])

    def test_empty_or_too_short(self):
        for text in ("", "   ", None, "Hello there."):
            with self.subTest(text=text):
                self.assert_flags(text, "short")

    def test_too_long(self):
        text = "Hello, thank you for getting in touch. " + "Thanks again for your message. " * 20
        self.assert_flags(text, "too long")
        just_fine = " ".join(["word"] * 90)
        self.assertFalse(any("long" in p for p in self.problems(just_fine)))
        self.assert_flags(" ".join(["word"] * 91), "too long")

    def test_any_digit(self):
        for text in (
            "Hello Jonas, thank you for your message about 3 studios. A member of our team will be in touch.",
            "Hello Jonas, thank you for your message, we are open 24/7. A member of our team will be in touch.",
            "Hello Jonas, thank you for the message sent at 3pm. A member of our team will be in touch.",
            "Hello Jonas, thank you for the message about 50% growth. A member of our team will be in touch.",
        ):
            with self.subTest(text=text):
                self.assert_flags(text, "number")

    def test_money(self):
        for text in (
            "Hello Jonas, thank you for asking about pricing. It is $ low. A member of our team will be in touch.",
            "Hello Jonas, thank you for asking about pricing, which is in euros. A member of our team will be in touch.",
            "Hello Jonas, thank you for asking about pricing, we charge per month. A member of our team will be in touch.",
            "Hello Jonas, thank you for asking about pricing in pounds. A member of our team will be in touch.",
            "Hello Jonas, thank you for asking about pricing, it is a fee per user. A member of our team will be in touch.",
            "Hello Jonas, thank you for asking about pricing at £ rates. A member of our team will be in touch.",
        ):
            with self.subTest(text=text):
                self.assert_flags(text, "money")

    def test_dates_and_times(self):
        for word in (
            "Monday", "friday", "January", "december", "today", "tomorrow", "tonight", "yesterday",
            "this week", "next week", "this month", "next month", "the weekend", "within the hour",
            "ASAP", "immediately", "right away", "as soon as possible", "by the end", "end of the day",
            "end of week", "noon", "midnight",
        ):
            with self.subTest(word=word):
                text = f"Hello Jonas, thank you for getting in touch about a demo, we will see {word}. A member of our team will be in touch."
                self.assert_flags(text, "date or a time")

    def test_offers_and_promises(self):
        for word in ("free", "a discount", "discounts", "a trial", "complimentary", "a guarantee", "guaranteed", "I promise", "promised", "definitely"):
            with self.subTest(word=word):
                text = f"Hello Jonas, thank you for getting in touch about a demo, you get {word}. A member of our team will be in touch."
                self.assert_flags(text, "offer or a promise")

    def test_commitments_for_us(self):
        for phrase in ("We will send a link.", "We'll send a link.", "I will call you.", "I'll call you.",
                       "We can help with that.", "We offer a booking tool.", "We provide a booking tool.",
                       "We have a booking tool."):
            with self.subTest(phrase=phrase):
                text = f"Hello Jonas, thank you for getting in touch about a demo. {phrase} A member of our team will be in touch."
                self.assert_flags(text, "commitment")

    def test_we_have_received_your_request_is_fine(self):
        for sentence in (
            "We have received your request regarding pricing.",
            "we have received your request.",
            "WE HAVE RECEIVED YOUR REQUEST.",
            "We have  received your request for a demo.",
        ):
            with self.subTest(sentence=sentence):
                text = f"Hello Jonas, thank you for getting in touch. {sentence} A member of our team will be in touch."
                self.assertEqual(self.problems(text), [])

    def test_we_have_in_any_other_form_is_still_banned(self):
        for sentence in (
            "We have received your enquiry.",
            "We have received your message.",
            "We have received your requests.",
            "We have your request.",
            "We have a booking tool.",
            "We have received it.",
            "We have received your request and we have a plan.",
            "We have received your request. We have a tool for that.",
        ):
            with self.subTest(sentence=sentence):
                text = f"Hello Jonas, thank you for getting in touch. {sentence} A member of our team will be in touch."
                self.assert_flags(text, "commitment")

    def test_the_exception_does_not_hide_other_problems(self):
        text = "Hello Jonas, we have received your request for 3 studios. A member of our team will be in touch."
        self.assert_flags(text, "number")

    def test_the_one_allowed_commitment_is_fine_in_any_capitals(self):
        for sentence in ("A member of our team will be in touch.", "a member of our team will be in touch."):
            text = f"Hello Jonas, thank you for getting in touch about a demo. {sentence}"
            self.assertEqual(self.problems(text), [], sentence)

    def test_describing_what_nova_wellness_sells_or_does(self):
        for phrase in (
            "I hope you enjoy a demo of our software.",
            "Our platform can be shown later.",
            "Our booking system is popular.",
            "Thanks for your interest in our products.",
            "Thanks for your interest in our services.",
            "Our tool is simple.",
            "Our solution fits studios.",
            "Our features are broad.",
            "Our integrations are broad.",
            "Our app is quick.",
            "Our application is quick.",
            "Booking is what we do.",
            "Scheduling is easy.",
            "We help studios grow.",
            "We are a software company.",
            "We specialise in studios.",
            "We specialize in studios.",
            "Our company is friendly.",
            "Our business is friendly.",
            "Our customers love it.",
        ):
            with self.subTest(phrase=phrase):
                text = f"Hello Jonas, thank you for getting in touch about a demo. {phrase} A member of our team will be in touch."
                self.assert_flags(text, "sells or does")

    def test_referring_to_the_senders_own_request_in_general_words_is_fine(self):
        for phrase in (
            "Thank you for your demo request.",
            "Thank you for your pricing question.",
            "Thank you for your partnership proposal.",
            "Thank you for your enquiry.",
            "Thank you for getting in touch about a demo for your studio.",
        ):
            with self.subTest(phrase=phrase):
                text = f"Hello Jonas, {phrase} A member of our team will be in touch."
                self.assertEqual(self.problems(text), [])

    def test_links_and_contact_details(self):
        for text in (
            "Hello Jonas, thank you for getting in touch, see http://example.test for more. A member of our team will be in touch.",
            "Hello Jonas, thank you for getting in touch, see www.example.test for more. A member of our team will be in touch.",
            "Hello Jonas, thank you for getting in touch, write to someone@example.test please. A member of our team will be in touch.",
            "Hello Jonas, thank you for getting in touch, see example.com for more. A member of our team will be in touch.",
            "Hello Jonas, thank you for getting in touch, see example.org for more. A member of our team will be in touch.",
        ):
            with self.subTest(text=text):
                self.assert_flags(text, "link or contact detail")

    def test_its_own_sign_off_or_ai_line(self):
        base = "Hello Jonas, thank you for getting in touch about a demo. A member of our team will be in touch."
        for ending in ("Kind regards", "Best regards", "Warm regards", "Sincerely", "Yours faithfully",
                       "Best wishes", "Cheers"):
            with self.subTest(ending=ending):
                self.assert_flags(f"{base}\n\n{ending},\nNova", "sign-off")
        self.assert_flags("AI draft - please review before sending\n\n" + base, "AI draft")
        self.assert_flags(base + " (this is an AI draft)", "AI draft")

    def test_more_than_one_question(self):
        base = "Hello Jonas, thank you for getting in touch about a demo."
        self.assertEqual(self.problems(base + " Which studio is it? A member of our team will be in touch."), [])
        self.assert_flags(base + " Which studio is it? How many staff? A member of our team will be in touch.", "question")

    def test_every_problem_is_plain_words_and_several_can_be_named(self):
        found = self.problems("It is free for 3 months, Monday!")
        self.assertGreaterEqual(len(found), 3)
        for message in found:
            self.assertIsInstance(message, str)
            self.assertNotIn("Traceback", message)
            self.assertNotIn("re.", message)

    def test_it_never_raises_on_odd_input(self):
        for odd in (None, 5, [], {}, b"bytes", "\x00"):
            with self.subTest(odd=odd):
                self.assertIsInstance(self.problems(odd), list)


class AssembleTests(unittest.TestCase):
    """Case 6: the finished draft."""

    def test_the_finished_draft_has_the_ai_line_the_text_and_the_fixed_sign_off(self):
        self.assertEqual(
            v3_reply.assemble_draft(CLEAN_DRAFT),
            "AI draft - please review before sending\n\n"
            + CLEAN_DRAFT
            + "\n\nKind regards,\nNova Wellness Team",
        )

    def test_the_constants_are_exactly_as_decided(self):
        self.assertEqual(v3_reply.AI_DRAFT_LINE, "AI draft - please review before sending")
        self.assertEqual(v3_reply.SIGN_OFF, "Kind regards,\nNova Wellness Team")

    def test_extra_space_around_the_text_is_trimmed(self):
        text = v3_reply.assemble_draft("\n  " + CLEAN_DRAFT + "  \n\n")
        self.assertEqual(text.split("\n\n")[1], CLEAN_DRAFT)

    def test_the_saved_result_keeps_only_geminis_text(self):
        result, _ = v3_reply.read_v3(lead())
        self.assertEqual(result["draft_reply"], CLEAN_DRAFT)
        self.assertNotIn("AI draft", result["draft_reply"])
        self.assertNotIn("Kind regards", result["draft_reply"])
        self.assertNotIn("Nova Wellness Team", result["draft_reply"])


class ExpectedCategoryTests(unittest.TestCase):
    """Case 7: the category an answer-key entry implies."""

    def test_the_key_implies_the_category(self):
        cases = [
            ({"is_lead": False}, "Not a lead"),
            ({"is_lead": True, "request_type": "demo"}, "Lead - Demo"),
            ({"is_lead": True, "request_type": "pricing"}, "Lead - Pricing"),
            ({"is_lead": True, "request_type": "partnership"}, "Lead - Other"),
            ({"is_lead": True, "request_type": "other"}, "Lead - Other"),
        ]
        for entry, category in cases:
            with self.subTest(entry=entry):
                self.assertEqual(v3_reply.expected_category(entry), category)


class ReportTests(unittest.TestCase):
    """Case 7: the v3 part of the accuracy report."""

    EXPECTED = [
        {"id": 1, "is_lead": True, "request_type": "pricing"},
        {"id": 2, "is_lead": False},
        {"id": 3, "is_lead": True, "request_type": "demo"},
        {"id": 4, "is_lead": True, "request_type": "other"},
        {"id": 5, "is_lead": True, "request_type": "demo"},
    ]

    def answers(self):
        return {
            1: lead(),
            2: dict(NOT_A_LEAD),
            3: lead(request_type="demo", category="Lead - Demo", draft_reply="Hello, it costs $5. Thanks again for writing."),
            4: lead(request_type="other", category="Lead - Demo"),   # wrong category: becomes Needs review
            5: None,                                                 # broken reply
        }

    def test_category_score_counts_the_checked_category(self):
        report = v3_reply.v3_report(self.EXPECTED, self.answers())
        self.assertEqual(report["category"], (3, 5))

    def test_draft_violations_name_the_email_and_the_rule(self):
        report = v3_reply.v3_report(self.EXPECTED, self.answers())
        self.assertEqual([email_id for email_id, _ in report["violations"]], [3])
        self.assertTrue(any("money" in p or "number" in p for p in report["violations"][0][1]))

    def test_counts_of_drafts(self):
        report = v3_reply.v3_report(self.EXPECTED, self.answers())
        self.assertEqual(report["drafts_ok"], 1)
        self.assertEqual(report["leads_checked"], 2)    # emails 1 and 3 ended as a lead category

    def test_a_clean_run_has_zero_violations(self):
        expected = self.EXPECTED[:2]
        report = v3_reply.v3_report(expected, {1: lead(), 2: dict(NOT_A_LEAD)})
        self.assertEqual(report["violations"], [])
        self.assertEqual(report["category"], (2, 2))

    def test_samples_are_finished_drafts_spread_across_the_leads_and_at_most_four(self):
        expected = [{"id": i, "is_lead": True, "request_type": "pricing"} for i in range(1, 11)]
        answers = {i: lead() for i in range(1, 11)}
        report = v3_reply.v3_report(expected, answers)
        self.assertEqual(len(report["samples"]), 4)
        ids = [email_id for email_id, _, _ in report["samples"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids[0], 1)
        self.assertGreater(ids[-1], 6)
        for _, category, text in report["samples"]:
            self.assertEqual(category, "Lead - Pricing")
            self.assertTrue(text.startswith("AI draft - please review before sending"))
            self.assertTrue(text.endswith("Kind regards,\nNova Wellness Team"))

    def test_fewer_than_four_leads_gives_all_of_them(self):
        report = v3_reply.v3_report(self.EXPECTED[:1], {1: lead()})
        self.assertEqual(len(report["samples"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
