# Test cases: AI extraction of lead details from emails

Written before any code. Each case says what goes in and what should happen.
All emails are fake (see lead-emails.md). The answer key is expected-results.json.
No real keys appear in this file.

## Case 1: a normal lead email returns valid JSON

- Input: email 9 (Jonas Meyer, Blue Reed Yoga, asks for pricing). Run with --live.
- Expected: the model's reply is valid JSON with is_lead, name, email, company, role, request_type, urgency and summary.
- Expected: it matches the answer key: is_lead true, name Jonas Meyer, company Blue Reed Yoga, role null, request_type pricing, urgency 1.
- Expected: all 7 scored fields for this email count as correct.

## Case 2: a non-lead email

- Input: email 5 (an out-of-office reply). Run with --live.
- Expected: the model says is_lead is false.
- Expected: that email scores 1 field (is_lead), and it counts as correct.
- Expected: any extra fields the model adds for a non-lead are ignored, not scored.

## Case 3: the model gets is_lead wrong on a real lead

- Input: email 4 (urgent demo request), but the model says is_lead is false.
- Expected: is_lead counts as wrong.
- Expected: the other 6 fields for that email also count as wrong, because they were expected for a real lead.

## Case 4: the model returns broken JSON

- Input: the model replies with text that is not valid JSON (for example, a chatty sentence or a cut-off answer).
- Expected: that email counts as wrong on every scored field.
- Expected: the script does not crash. It carries on with the next email.
- Expected: the broken reply is saved in the results file so I can read it later, and the final report says how many emails had broken replies.

## Case 5: the model or internet cannot be reached

- Input: no internet, or the Gemini service is down. Run with --live.
- Expected: the script stops straight away with a plain message, like "I couldn't reach the AI service. Check your internet and try again."
- Expected: no technical error text, and no half-finished score is saved to accuracy.md.

## Case 6: the free daily or per-minute limit is reached

- Input: Gemini says "too many requests" part-way through a live run.
- Expected: the script waits briefly and tries again once. If it still fails, it stops with a plain message saying the free limit was reached and to try later.
- Expected: no half-finished score is saved to accuracy.md.

## Case 7: GEMINI_API_KEY is missing

- Input: no GEMINI_API_KEY in .env or the terminal. Run with --live.
- Expected: the script stops straight away with a plain message, like "I can't find GEMINI_API_KEY. Add it to your .env file and try again."
- Expected: no API call is made, no key or part of a key is shown, and .env is not changed.

## Case 8: Gemini rejects the key

- Input: GEMINI_API_KEY is present but wrong or expired. Run with --live.
- Expected: the script stops with a plain message, like "Gemini did not accept the key. Check it and try again."
- Expected: the key is never shown, not even partly, on screen or in any saved file.

## Case 9: scoring maths on a tiny example

- Input: three emails.
  - A: a real lead. All 7 scored fields are correct.
  - B: a real lead. is_lead is right and 4 of the 6 other fields are right (urgency and role are wrong).
  - C: a non-lead. is_lead is right.
- Expected: A has 7 scored fields (7 correct). B has 7 scored fields (5 correct). C has 1 scored field (1 correct).
- Expected: 15 scored fields in total, 13 correct, so accuracy is 13 / 15 = 86.7%.
- Expected: summary is never counted.

## Case 10: how many fields are scored on the full set

- Input: the full answer key of 30 emails (24 real leads and 6 non-leads).
- Expected: 30 is_lead fields, plus 6 fields for each of the 24 real leads (144), so 174 scored fields in total.
- Expected: perfect answers give 174 / 174 = 100%.

## Case 11: small differences in capitals and spaces

- Input: the model returns "PRIYA RAMAN " (capitals, extra space) where the answer key says "Priya Raman".
- Expected: it counts as correct. Only capitals and spaces at the start or end are ignored.
- Expected: a different spelling (for example "Priya Ramen") counts as wrong.
- Expected: null in the answer key is only matched by null. The word "null", "none" or an empty string counts as wrong.

## Case 12: each prompt version keeps its own score

- Input: I run the script with prompts/v1.md, then again with prompts/v2.md.
- Expected: accuracy.md has one row per run, with the prompt version, the model name, the date, the total score and the score for each field.
- Expected: the v1 row is still there after the v2 run.

## Case 13: switching models is a one-line change

- Input: I change the model name in the config file in workflows/02-email-to-attio/.
- Expected: the next run uses the new model, and the dry run output shows its name.
- Expected: nothing else needs to change.

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- In a dry run it shows what it would send (the prompt version, the model name and the first email), makes no API calls and saves nothing to accuracy.md.
- A dry run works even if GEMINI_API_KEY is missing, because nothing is sent.
- The script never prints, logs or opens GEMINI_API_KEY, and never changes .env.
