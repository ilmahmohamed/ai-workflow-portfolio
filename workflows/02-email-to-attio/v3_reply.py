"""Checks on Gemini's single answer from prompt v3 (one call per email gives everything).

Gemini returns is_lead, category, name, email, company, role, request_type, urgency, summary and
draft_reply. This file decides what to trust:
  - read_v3 turns the answer into a tidy result and a list of plain-words problems. The category is
    checked against the request type, never just trusted, and the draft reply is kept only for a
    confirmed lead and only if it follows the draft rules.
  - draft_problems lists which draft rules a reply text breaks.
  - assemble_draft adds the fixed first line and the fixed sign-off. Those are added by this code,
    never written by Gemini.
  - v3_report adds the v3 numbers to the accuracy run.

It never uses the internet or any file, and nothing here ever sends an email.
"""

import re

NEEDS_REVIEW = "Needs review"
NOT_A_LEAD = "Not a lead"
CATEGORIES = ("Lead - Demo", "Lead - Pricing", "Lead - Other", NEEDS_REVIEW, NOT_A_LEAD)
LEAD_CATEGORIES = ("Lead - Demo", "Lead - Pricing", "Lead - Other")
REQUEST_TO_CATEGORY = {
    "demo": "Lead - Demo",
    "pricing": "Lead - Pricing",
    "partnership": "Lead - Other",
    "other": "Lead - Other",
}

RESULT_FIELDS = (
    "is_lead", "category", "name", "email", "company", "role",
    "request_type", "urgency", "summary", "draft_reply",
)
TEXT_FIELDS = ("name", "email", "company", "role", "summary")

AI_DRAFT_LINE = "AI draft - please review before sending"
SIGN_OFF = "Kind regards,\nNova Wellness Team"

MIN_DRAFT_WORDS = 5
MAX_DRAFT_WORDS = 90
SAMPLE_LIMIT = 4

DRAFT_PROBLEM_PREFIX = "the draft "
NO_DRAFT = "no draft was written"


# ---- The draft rules --------------------------------------------------------------

def _rule(pattern):
    return re.compile(pattern, re.IGNORECASE)


MONEY = _rule(
    r"[$£€¥]|\b(?:dollars?|pounds?|euros?|usd|gbp|eur)\b|\bper\s+(?:month|year|user|seat|location)\b"
)
DATE_OR_TIME = _rule(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:january|february|march|april|june|july|august|september|october|november|december)\b"
    r"|\b(?:today|tomorrow|tonight|yesterday|weekend|asap|immediately|noon|midnight)\b"
    r"|\b(?:this|next|last)\s+(?:week|month|year|quarter)\b"
    r"|\bwithin\b|\bright\s+away\b|\bas\s+soon\s+as\b|\bby\s+the\s+end\b"
    r"|\bend\s+of\s+(?:the\s+)?(?:day|week|month)\b"
)
OFFER_OR_PROMISE = _rule(
    r"\b(?:free|discount\w*|trial|complimentary|guarantee\w*|promis\w*|definitely)\b"
)
COMMITMENT = _rule(
    r"\b(?:we|i)(?:['’]ll|\s+will|\s+shall)\b|\bwe\s+(?:can|offer|provide|have)\b"
)
# The one place "we have" is fine: saying the sender's request arrived. It is cut out before the
# commitment check, so "we have" in any other form (even next to this sentence) is still banned.
REQUEST_RECEIVED = _rule(r"\bwe\s+have\s+received\s+your\s+request\b")
# What the company sells or does is never described. A draft may only refer to the sender's own
# request in general words ("your demo request", "your enquiry").
SELLS_OR_DOES = _rule(
    r"\b(?:software|platforms?|apps?|applications?|products?|services?|tools?|systems?|solutions?"
    r"|features?|integrations?|booking|scheduling)\b"
    r"|\bwe\s+(?:help|are|specialis\w+|specializ\w+)\b"
    r"|\bour\s+(?:company|business|customers|clients)\b"
)
LINK_OR_CONTACT = _rule(r"https?:|www\.|@|\.(?:com|org)\b")
SIGN_OFF_WORDS = _rule(
    r"\bregards\b|\bsincerely\b|\byours\s+(?:truly|faithfully|sincerely)\b|\bbest\s+wishes\b|\bcheers\b"
)
AI_LINE_WORDS = _rule(r"\bAI\s+draft\b")


def draft_problems(text):
    """The draft rules a reply text breaks, as short plain phrases ("contains a number").

    An empty list means the text follows every rule. Never raises.
    """
    if not isinstance(text, str) or len(text.split()) < MIN_DRAFT_WORDS:
        return [f"is empty or too short (fewer than {MIN_DRAFT_WORDS} words)"]
    problems = []
    if len(text.split()) > MAX_DRAFT_WORDS:
        problems.append(f"is too long (more than {MAX_DRAFT_WORDS} words)")
    if re.search(r"\d", text):
        problems.append("contains a number")
    if MONEY.search(text):
        problems.append("mentions money")
    if DATE_OR_TIME.search(text):
        problems.append("mentions a date or a time")
    if OFFER_OR_PROMISE.search(text):
        problems.append("makes an offer or a promise")
    if COMMITMENT.search(REQUEST_RECEIVED.sub(" ", text)):
        problems.append("makes a commitment for us")
    if SELLS_OR_DOES.search(text):
        problems.append("describes what Nova Wellness sells or does")
    if LINK_OR_CONTACT.search(text):
        problems.append("contains a link or contact detail")
    if SIGN_OFF_WORDS.search(text):
        problems.append("has its own sign-off")
    if AI_LINE_WORDS.search(text):
        problems.append("has its own AI draft line")
    if text.count("?") > 1:
        problems.append("asks more than one question")
    return problems


def assemble_draft(body):
    """The finished draft: the fixed first line, Gemini's text, then the fixed sign-off."""
    return f"{AI_DRAFT_LINE}\n\n{body.strip()}\n\n{SIGN_OFF}"


# ---- Reading the answer -----------------------------------------------------------

def _clean_text(value):
    """Trimmed text, or None for anything that is not text or is empty."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _clean_category(value):
    """The category's exact name if it is one of the five (capitals and spaces ignored), else None."""
    text = _clean_text(value)
    if text is None:
        return None
    for category in CATEGORIES:
        if text.lower() == category.lower():
            return category
    return None


def _blank(category, is_lead=False):
    result = {field: None for field in RESULT_FIELDS}
    result["is_lead"] = is_lead
    result["category"] = category
    return result


def read_v3(parsed):
    """Turn Gemini's parsed answer into (result, problems). Never raises.

    result always has every field in RESULT_FIELDS. problems is a list of plain-words sentences;
    an empty list means nothing was wrong.
    """
    if not isinstance(parsed, dict):
        return _blank(NEEDS_REVIEW), ["Gemini's reply could not be used"]
    flag = parsed.get("is_lead")
    if flag is not True and flag is not False:
        return _blank(NEEDS_REVIEW), ["the reply does not say clearly whether it is a lead"]

    category = _clean_category(parsed.get("category"))

    if flag is False:
        if category in (NOT_A_LEAD, NEEDS_REVIEW):
            return _blank(category), []
        return _blank(NEEDS_REVIEW), [
            "the email is not a lead but the category is missing or says it is a lead"
        ]

    result = _blank(NEEDS_REVIEW, is_lead=True)
    for field in TEXT_FIELDS:
        result[field] = _clean_text(parsed.get(field))
    request_type = _clean_text(parsed.get("request_type"))
    request_type = request_type.lower() if request_type else None
    result["request_type"] = request_type
    urgency = parsed.get("urgency")
    result["urgency"] = urgency if isinstance(urgency, int) and not isinstance(urgency, bool) else None

    expected = REQUEST_TO_CATEGORY.get(request_type)
    problems = []
    if category == NEEDS_REVIEW:
        return result, problems
    if expected is None:
        problems.append("the request type is not demo, pricing, partnership or other")
    elif category is None:
        problems.append("the category is missing or is not one of the five")
    elif category != expected:
        problems.append(f"the category ({category}) does not match the request type ({request_type})")
    else:
        result["category"] = category

    if result["category"] in LEAD_CATEGORIES:
        body = _clean_text(parsed.get("draft_reply"))
        if body is None:
            problems.append(NO_DRAFT)
        else:
            broken = draft_problems(body)
            if broken:
                problems.extend(DRAFT_PROBLEM_PREFIX + problem for problem in broken)
            else:
                result["draft_reply"] = body
    return result, problems


# ---- The accuracy run's v3 numbers ------------------------------------------------------

def expected_category(entry):
    """The category an answer-key entry implies."""
    if not entry.get("is_lead"):
        return NOT_A_LEAD
    request_type = _clean_text(entry.get("request_type"))
    return REQUEST_TO_CATEGORY.get(request_type.lower() if request_type else None, NEEDS_REVIEW)


def _spread(items, count):
    """Up to `count` items, spread evenly from the first to the last."""
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (count - 1))] for i in range(count)]


def v3_report(expected_list, answers):
    """The v3 numbers for a run. answers maps id -> Gemini's parsed answer (or None if broken).

    category: (correct, total) using the checked category.
    violations: [(id, [rule problems])] for leads whose draft broke a rule.
    drafts_ok: leads with a draft that follows every rule. drafts_missing: leads with no draft
    and no rule broken. leads_checked: emails that ended as one of the three lead categories.
    category_problems: [(id, [problems])] for answers whose category or reply could not be trusted.
    samples: [(id, category, finished draft)], at most SAMPLE_LIMIT, spread across the leads.
    """
    correct = 0
    violations, category_problems, pool = [], [], []
    drafts_missing = leads_checked = 0
    for entry in expected_list:
        result, problems = read_v3(answers.get(entry["id"]))
        if result["category"] == expected_category(entry):
            correct += 1
        other = [p for p in problems if not p.startswith(DRAFT_PROBLEM_PREFIX) and p != NO_DRAFT]
        if other:
            category_problems.append((entry["id"], other))
        if result["category"] in LEAD_CATEGORIES:
            leads_checked += 1
            broken = [p[len(DRAFT_PROBLEM_PREFIX):] for p in problems if p.startswith(DRAFT_PROBLEM_PREFIX)]
            if broken:
                violations.append((entry["id"], broken))
            elif result["draft_reply"]:
                pool.append((entry["id"], result["category"], assemble_draft(result["draft_reply"])))
            else:
                drafts_missing += 1
    return {
        "category": (correct, len(expected_list)),
        "violations": violations,
        "drafts_ok": len(pool),
        "drafts_missing": drafts_missing,
        "leads_checked": leads_checked,
        "category_problems": category_problems,
        "samples": _spread(pool, SAMPLE_LIMIT),
    }
