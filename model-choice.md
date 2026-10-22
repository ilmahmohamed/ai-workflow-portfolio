# Model choice for the AI steps

Written 22 Oct 2026. The accuracy figures come from our saved test runs. The cost, token and speed figures come from a 10-email comparison made on the same day. Where something could not be measured, this file says so instead of guessing.

## The short version

One Gemini call per email does both AI steps: it reads the email and returns the lead details and the category (step 1), and it writes the short draft reply saved in Outlook (step 2). So there is one model, one cost and one speed for both.

**Pick for both steps: stay on gemini-3.5-flash-lite.** It is free, the fastest and the cheapest, it was accurate enough, and all 10 of its calls in the comparison worked. The bigger models we tried cost 6 to 9 times more on a paid plan, were slower, and Google refused 3 of their 6 calls as "too busy". We could not show that a bigger model does better on the one email we get wrong.

The model name lives in one place, `workflows/02-email-to-attio/config.json`. Changing the model is a one-line change there.

## The 10-email comparison

**This is a 10-email comparison, and for the bigger models it is not a full one.** We ran the same 10 saved test emails, the same prompt (v3) and the same settings (temperature 0, seed 42) on three models. Only the current model finished all 10. The two bigger models were refused by Google with a "503: this model is currently experiencing high demand" error. As agreed, we stopped at the first error each time and did not retry in a loop. So the bigger-model columns are partial, and a few calls cannot show which model is more accurate.

The 10 emails: 3 pricing leads (including the one we know we miss, "Still waiting for the quote", and one sender on a subdomain), 2 demo leads, 2 "other" leads (one in Dutch), and 3 non-leads (a newsletter, an invoice copy, and a sales pitch that mentions a demo). They are emails 1, 2, 3, 4, 6, 8, 12, 22, 29 and 30 of `tests/cases/lead-emails.md`.

| | gemini-3.5-flash-lite (in use) | gemini-3.6-flash | gemini-3.8-flash |
|---|---|---|---|
| Calls that worked | **10 of 10** | **1 of 10** (then 503, twice) | **2 of 10** (then 503) |
| Time per call | average 3.8 s, median 1.7 s, from 1.2 to 13.9 s | 9.1 s (one call) | 7.4 s and 9.9 s |
| Tokens per email: in / out / thinking | 1,718 / 91 / 0 | 1,697 / 146 / 1,352 | 1,696 / 140 / 727 (average of 2) |
| Free tier | yes | yes | yes |
| Paid cost per email | $0.00074 | $0.0069 | $0.0045 |
| Paid cost per 1,000 emails | **$0.74** | **$6.89**, $13.78 from 1 Jan 2027 | **$4.52**, $9.05 from 1 Jan 2027 |
| Fields right | 44 of 52 (84.6%) | 7 of 7 (email 1 only) | 14 of 14 (emails 1 and 2 only) |
| Category right | 9 of 10 | 1 of 1 | 2 of 2 |
| The known miss (email 30) | missed it | not reached | not reached |
| Draft rule breaks | 0 (6 drafts, about 23 words) | 0 (1 draft, 27 words) | 0 (2 drafts, 21 to 26 words) |
| Calls refused by Google | 0 | 2 | 1 |

How to read it:
- **Accuracy is only comparable for flash-lite.** The other two answered the easy emails (1 and 2) correctly, but never reached the hard ones. We do not know whether a bigger model would catch email 30.
- **The 84.6% is lower than the 95.4% on all 30 on purpose.** These 10 were picked to include the known miss and other hard cases. That one email accounts for 7 of the 8 fields lost.
- **Thinking tokens are what make the bigger models cost more.** They "think" before answering (727 to 1,352 tokens), and Google bills thinking as output. Flash-lite used none. So a bigger model costs about 6 times (3.8) to 9 times (3.6) as much per email on paid prices, and both prices double on 1 Jan 2027.
- **Cost of the free tier is $0 for all three.** Google's price page lists all three as "Free of charge" on the free tier, which is what we use. The paid figures are what it would cost if we ever had to pay.
- **Speed:** flash-lite's median call was under 2 seconds. The two bigger models took 7 to 10 seconds on every call we got through.
- **Prices** are from Google's own price page, checked 22 Oct 2026: flash-lite $0.30 per million tokens in and $2.50 out; gemini-3.6-flash and gemini-3.8-flash $0.75 in and $3.75 out until 31 Dec 2026, then $1.50 and $7.50. `gemini-3.5-flash` is not on that page, so we did not use it.
- **Our tokens figure is now measured, not estimated.** The earlier estimate of about 1,700 in and 90 out was close.

## Step 1: lead details and category

**What we use now.** gemini-3.5-flash-lite with prompt v3, temperature 0 and a fixed seed (42), so the same email gets the same answer as far as the service allows.

**Cost per email.** $0 on the free tier. On Google's paid price it would be $0.00074 (74 cents per 1,000 emails): about 1,718 tokens in and 91 out. The 11 saved test runs covered 176 emails and cost nothing. For comparison, entering 10 leads by hand in Attio took 2 hours 35 minutes (about 15 minutes per lead, week 1 log).

**Accuracy on our 30 test emails** (`accuracy.md`):

| Prompt | Accuracy | Broken replies |
|---|---|---|
| v1 | 80.6% (150 of 186) | 0 |
| v2 | 96.0% (167 of 174) | 0 |
| v3 (in use) | 95.4% (166 of 174) | 0 |

- Per field for v3: is a lead 29/30, category 29/30, name, email, company, request type and urgency 23/24 each, role 22/24. Those other fields are scored on 24 of the 30 emails.
- The one is-a-lead and category miss is "Still waiting for the quote": the key says it is a pricing lead, Gemini called it not a lead. It missed it in the accuracy runs, in the live Day 8 run and again in the 10-email comparison.
- On 6 fresh emails the prompt had never seen, v3 scored 100% (30 of 30). In the live Day 8 run, 28 of the 29 emails handled got the category in the key (the 30th was skipped on purpose, a no-reply sender).
- The prompt, not the model, did the improving: v1 to v2 added about 15 points on the same model. (v1 was scored against an older answer key, so that gap is a little rough.)

**Speed.** Measured on 10 calls: median 1.7 s, average 3.8 s, slowest 13.9 s. The program also waits 6 seconds between emails to stay inside the free per-minute limit, so 30 emails take at least about 3 minutes from the pauses alone.

**What we would try if accuracy were not good enough**, cheapest first:
1. **Improve the prompt.** It worked before and costs nothing. The one miss is a judgement call, which a prompt rule may fix better than a bigger model.
2. **Second opinion on doubtful emails only.** Send only the emails the small model marks Needs review to a bigger model such as gemini-3.8-flash. That is a small share of emails, so the extra cost stays small: about 0.5 cent per email on paid prices now, 0.9 cent from 1 Jan 2027.
3. **A bigger model for every email.** About $4.5 to $6.9 per 1,000 emails now and $9 to $14 per 1,000 from 1 Jan 2027, slower per email, and its calls can be refused when Google is busy. Free on the free tier, but we could not finish a test on it.

**What we picked, and why.** gemini-3.5-flash-lite for step 1:
- It is free, and we work on the free tier only.
- It is accurate enough (95.4% on the 30, 100% on the 6 fresh ones, no broken replies), and the bigger models showed no proof of doing better where it fails.
- It is the fastest and cheapest, and all 10 calls worked. Both bigger models were refused for being too busy.
- Its mistakes are caught by safety nets: the category is checked against the request type, doubtful emails become Needs review, and a person reads every reply before anything is sent.
- Honest note: we did not choose it by comparing every Gemini model. We started with it and improved the prompt until it was good enough. This comparison tested two bigger ones, only partly.

## Step 2: the draft reply saved in Outlook

**What we use now.** The same gemini-3.5-flash-lite call writes a `draft_reply` field in the same answer, under strict rules in prompt v3. Our code adds the fixed first line ("AI draft - please review before sending") and the fixed sign-off, and saves the draft in Outlook's Drafts folder, addressed only to the sender. It is never sent.

**Cost per email.** Nothing extra: it is the same call as step 1. A draft is about 23 words.

**Accuracy.** We measure rule breaks, not "correctness". The rules, checked by code: no numbers, prices, dates or promises other than "A member of our team will be in touch", no description of what we sell, no links or contact details, no sign-off or AI line of its own, at most one question, between 5 and 90 words.
- Final v3 run on the 30: 22 of 30 replies carry a draft. 1 rule break was recorded, and it was 0 once the saved replies were re-checked after allowing "we have received your request" (no new Gemini calls).
- On the 6 fresh emails: 0 rule breaks. In the 10-email comparison: 0 rule breaks on every model, and drafts of about the same length (21 to 27 words).
- A draft that breaks a rule is not used: the lead is still handled and a person writes the reply by hand.
- In the live tests, the draft you checked in Outlook looked right.
- The code does not judge tone or helpfulness, and we did not compare how the drafts read across models. That is for the person who reviews the draft.

**Speed.** Written inside the same call as step 1, so the same timings.

**What we would try if accuracy were not good enough:**
1. Tighten the draft rules in the prompt.
2. Write the draft with a bigger model in a second call, only for emails that are leads. That is about 0.5 cent per lead on paid prices now (gemini-3.8-flash), 0.9 cent from 1 Jan 2027, and slower.
3. Stop using AI drafts and keep a few fixed template replies per request type, which is free and cannot invent anything.

**What we picked, and why.** gemini-3.5-flash-lite, in the same call:
- One call keeps the cost and the wait the same as step 1.
- The rule checks and the "AI draft" line make a weak draft harmless, because a person reviews and sends it.
- The draft rules held: 0 breaks on the fresh emails and in the 10-email comparison, and none left in the final run after the re-check. The bigger models gave no sign of safer drafts.

## What we still have not measured

| Missing | Why it matters | What measuring needs |
|---|---|---|
| A bigger model on all 10 emails, especially the known miss (email 30) | Whether moving up fixes our one miss | Retrying when Google is less busy: one try per email, stopping at the first error |
| Free-tier request limits for the bigger models | Whether the 2 minute schedule would fit | Reading Google's separate rate-limits page, which we have not seen |
| How the draft wording differs across models | Whether a bigger model writes better drafts | A person reading drafts side by side |

## Where the numbers come from

- Accuracy on 30: `workflows/02-email-to-attio/accuracy.md` and the saved runs in `workflows/02-email-to-attio/results/`. Day 8 live result: `tests/cases/05-attio-sync.md`.
- The 10-email comparison: a one-off measuring script run on 22 Oct 2026 (not kept in the repo), using the same request settings as `workflows/02-email-to-attio/extract_leads.py`. Tokens come from the counts Google returns on each call, and times are measured around each call.
- Prices: Google's Gemini API price page, https://ai.google.dev/gemini-api/docs/pricing, read on 22 Oct 2026.
- The by-hand baseline: `weekly-updates/week-1-log.md`.
- The older `model-comparison.md` compares chat tools on assistant tasks. It is not about this extraction step.
