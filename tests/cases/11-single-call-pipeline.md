# Test cases: one Gemini call per email, from the Inbox to Attio

Written before any code. Each case says what goes in and what should happen.
All emails and answers here are pretend. A pretend Outlook, Gemini and Attio are used for every test, so no real mailbox, AI call or Attio workspace is touched, and no real key appears.

How it works now:
- The triage asks Gemini ONE question per email (prompt v3) and gets everything back (is_lead, category, name, email, company, role, request_type, urgency, summary and a draft reply).
- For a lead, the triage saves that answer on the email itself in Outlook (a small hidden "open extension", see 08-saved-results.md), adds the category, and moves the email to Leads. The saved data travels with the email.
- The Attio step reads the saved answer from the email in Leads and uses it. It never calls Gemini. An email with no usable saved answer is marked "needs a person".
- Nothing is saved in a local file, so this also works where local files are not kept (GitHub Actions).
- Nothing in this file ever sends, replies to or forwards an email. Creating the reply draft is a later step and is not part of this file.

## Part A: the triage

### Case T1: exactly one Gemini request per new email

- Input: new emails in the Inbox. Run live.
- Expected: the default prompt is v3, and exactly one request is sent to Gemini for each new email.
- Expected: a lead that only needs its move retried sends no request. An email handled on an earlier run sends none either.

### Case T2: a lead's full answer is saved on the email

- Input: a new email that Gemini calls a lead (with a clean draft reply).
- Expected: the full answer is saved on that email, using the saved-data rules in 08-saved-results.md: is_lead, category, name, email, company, role, request_type, urgency, summary and draft_reply.
- Expected: the saved answer holds only those fields. It never holds the email body, the subject, the sender's display name or anything else from the email.
- Expected: the draft is saved as Gemini wrote it, without the "AI draft" first line or the sign-off (those are added later, when a draft is made).

### Case T3: the order for a lead is save, then category, then move

- Expected: the three changes happen in this order: the answer is saved, the category is added, the email is moved to Leads. Then the state file says the email is done.
- Input: Outlook refuses to save the answer, or the connection is lost.
- Expected: the run stops with a plain message (sign-in may need redoing, or couldn't reach Outlook). That email gets no category, is not moved, and is not written in the state file, so the next run tries it again.
- Input: the category change fails after the answer was saved.
- Expected: the next run handles the email again. It asks Gemini again for it, finds an answer is already saved, keeps the first one (no second copy), and carries on.
- Input: the move fails after everything else worked.
- Expected: the next run only retries the move, with no Gemini request and no second save.

### Case T4: only leads get a saved answer

- Input: Gemini says "not a lead", or the answer is broken, or the category check (Case T5) gives Needs review.
- Expected: the category is added and the email stays in the Inbox. Nothing is saved on it.

### Case T5: the category is checked by code

- Input: Gemini calls it a lead but the category does not match the request type (for example pricing with Lead - Demo), or the category is missing or unknown.
- Expected: the email becomes Needs review, stays in the Inbox, and nothing is saved on it. (The checks are in 10-single-call-v3.md.)

### Case T6: a lead whose draft broke a rule is still a lead

- Input: Gemini calls it a lead with a draft that breaks a draft rule (for example it mentions a price).
- Expected: the email is still a lead: saved, categorised and moved. The saved answer has no draft (empty), and the run's output says plainly that no usable draft was written. The output never contains the draft's text.

### Case T7: a dry run changes nothing

- Expected: a dry run sends no request to Gemini, saves nothing, and changes nothing in Outlook, as before.

### Case T8: no personal details in logs or files

- Expected: the output, the state file and any other file the triage writes never contain a lead's name, company, role, summary or draft text, and never the email body. (The sender's address and the subject already appear in the output, as before.)
- Expected: no token or key is ever printed or saved.

### Case T9: the triage stays send-proof

- Expected: the only changes the triage can ask Outlook for are: add a category, save the answer (one extension of the fixed name and shape), and move a lead to Leads. It never sends, replies to, forwards, copies or deletes anything.
- Expected: the permission stays exactly Mail.ReadWrite.

## Part B: the Attio step

### Case A1: the saved answer is used and Gemini is never called

- Input: emails in Leads that have a saved answer.
- Expected: the Attio step reads each saved answer from its email, and creates or updates the person, company, deal and note exactly as before.
- Expected: it sends no request to Gemini at all, needs no Gemini key, and needs no prompt or model setting.
- Expected: the old --prompt option is gone.

### Case A2: no saved answer means "needs a person"

- Input: an email in Leads with no saved answer (for example one that came before this change).
- Expected: nothing is created in Attio. The output says it needs a person and why ("no usable answer was saved on the email"; a damaged answer reads the same as a missing one). The email stays in Leads. No Gemini request is made.
- Expected: later normal runs leave it alone (they only mention it), so it is not looked at again and again.

### Case A3: a damaged or unusable saved answer needs a person

- Input: a saved answer that is damaged, says it is not a lead, says Needs review, or has no usable email address.
- Expected: needs a person, with the reason named in plain words ("the saved answer says it is not a lead", "the saved answer says it needs review", "no usable answer was saved on the email", "no usable email address"). Nothing is created in Attio. No Gemini request.

### Case A4: --retry-attention looks again, without Gemini

- Input: an email marked as needing a person, and now a usable saved answer is on it.
- Expected: a normal run leaves it alone; a run with --retry-attention reads the saved answer again and handles it if it is now usable. If it is still unusable it stays marked, with the new reason. Done emails are never touched. No Gemini request in either case.

### Case A5: a problem reading is not mistaken for "needs a person"

- Input: Outlook refuses the read of a saved answer (sign-in not accepted, an error, or no connection).
- Expected: the run stops with the usual plain message. The email is NOT marked as needing a person, and nothing is created for it.

### Case A6: the request type and category come from the saved answer

- Expected: the deal name and note use the saved request_type. The triage's local state file is no longer read by the Attio step.

### Case A7: no lead details are written to the state file

- Expected: the Attio step's state file keeps only technical progress (ids, status, a short reference for the note). It never holds a lead's name, email address, company, role or summary.
- Expected: nothing is saved in any other local file.

### Case A8: the Attio step stays send-proof

- Expected: the only requests the Attio step sends to Outlook are: reads (the folders, the Leads folder's emails, the saved answer on each email) and moving a handled email to Leads/Processed. Nothing else, and never anything that sends, replies to, forwards, copies or deletes.

### Case A9: everything else the Attio step did before still holds

- Expected: the existing behaviour is kept (see 05-attio-sync.md): people, companies, deals and notes without duplicates; retries after failures; the maximum per run; system and security emails skipped; --only-subject and --only-from; dry run by default; the owner and keys never shown. Only the parts about asking Gemini (05 Cases 34, 35 and 57 to 62) are replaced by Part B above.
