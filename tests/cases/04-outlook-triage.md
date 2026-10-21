# Test cases: Outlook Inbox triage with Gemini

Written before any code. Each case says what goes in and what should happen.
All emails are pretend. A pretend Outlook and a pretend Gemini are used for every test, so no real mailbox is touched and no real AI call is made.
No real keys or tokens appear in this file.

What the script does: it reads new emails in the Outlook Inbox, asks Gemini (prompt v2, model from config.json) whether each one is a lead, what it asks for and how urgent it is, then writes the answer back to Outlook. It adds one category (Lead - Demo, Lead - Pricing, Lead - Other, Not a lead or Needs review) and moves real leads into the Leads folder. Not a lead and Needs review emails stay in the Inbox. It remembers handled emails in a small state file so nothing is handled twice. It never sends or replies to anything.

Some expected results depend on the "Decisions for Ilmah" at the end of this file. Those decisions were settled on 21 Oct, and the cases use them.

## Case 1: a demo lead

- Input: one new email in the Inbox asking for a demo. The pretend Gemini says it is a lead with request type demo.
- Expected: the category "Lead - Demo" is added to the email, and any categories it already had are kept.
- Expected: the email is moved to the Leads folder.
- Expected: the email is written in the state file as handled.

## Case 2: a pricing lead

- Input: one new email asking for prices. The pretend Gemini says lead, request type pricing.
- Expected: the category "Lead - Pricing" is added and the email is moved to the Leads folder.
- Expected: it is written in the state file as handled.

## Case 3: a partnership or other lead

- Input: one new email proposing a partnership (or asking something else real). The pretend Gemini says lead, request type partnership or other.
- Expected (Decision 6): the category "Lead - Other" is added and the email is moved to the Leads folder, like the other leads.
- Expected: it is written in the state file as handled.

## Case 4: a non-lead (newsletter)

- Input: one new newsletter email. The pretend Gemini says it is not a lead.
- Expected: the category "Not a lead" is added.
- Expected (Decision 5): the email stays in the Inbox.
- Expected: it is written in the state file as handled.

## Case 5: an existing customer's support email

- Input: one new email from an existing customer asking for an invoice copy. The pretend Gemini says it is not a lead.
- Expected: the category "Not a lead" is added and the email stays in the Inbox.
- Expected: it is not moved to the Leads folder.

## Case 6: Gemini is unsure

- Input: the pretend Gemini answers with valid JSON that is incomplete or does not make sense (for example a lead with no request type, or a request type that is not demo, pricing, partnership or other). See Decision 4.
- Expected: the category "Needs review" is added and the email stays in the Inbox.
- Expected: it is not moved and is written in the state file as handled.

## Case 7: Gemini returns a broken reply

- Input: the pretend Gemini answers with text that is not valid JSON.
- Expected: the category "Needs review" is added and the email stays in the Inbox.
- Expected: the script does not crash and carries on with the next email.
- Expected: the final report says how many emails had a broken reply.

## Case 8: the same email seen twice

- Input: the same email shows up again, either twice in one run or in a second run later.
- Expected: it is handled once only. Gemini is asked once, the category is added once, and it is moved at most once.
- Expected: the second time it is skipped because the state file says it was handled.

## Case 9: the state file is missing

- Input: there is no state file (for example the very first run).
- Expected: the script starts safely. It looks only at emails newer than the start date and handles at most the maximum per run (Decisions 1 and 2).
- Expected: it says in plain words that it is starting fresh and from which date.
- Expected: it does not process the whole old Inbox, and it creates the state file after handling emails.

## Case 10: the state file is damaged

- Input: the state file exists but cannot be read (empty, cut off or not valid).
- Expected: the script starts safely, in the same way as when the file is missing, with the start date and the maximum per run.
- Expected: the damaged file is set aside under a new name, not overwritten or deleted, and a plain message says so.
- Expected: emails that were already categorised are still skipped (Case 18), so nothing gets a second category.

## Case 11: a large old Inbox on the first run

- Input: an Inbox with hundreds of emails, most of them older than the start date, and no state file.
- Expected: only emails newer than the start date are considered.
- Expected: at most the maximum per run are handled. The rest are left for the next run, and the report says how many are still waiting.
- Expected: older emails are never read by Gemini, categorised or moved.
- Expected (Decision 8): when there are more than the maximum, the oldest of the new emails go first.

## Case 12: the Outlook sign-in has expired

- Input: the saved sign-in no longer works. Run with --live.
- Expected: a plain message says to run outlook_signin.py again.
- Expected: nothing is changed in Outlook, no call is made to Gemini and the state file is not changed.

## Case 13: Outlook refuses the write

- Input: Outlook says "not allowed" or "sign-in not accepted" when the category is added. Run with --live.
- Expected: a plain message says Outlook refused the change and the sign-in may need redoing.
- Expected: that email is left as it was and is not written in the state file. The script stops.
- Expected: emails handled earlier in the same run stay handled.

## Case 14: the category is added but the move is refused

- Input: Outlook accepts the category but refuses to move the email to the Leads folder.
- Expected: a plain message says the email was categorised but could not be moved.
- Expected: the state file records it as categorised and not yet moved.
- Expected: on the next run only the move is tried again. Gemini is not asked again and the category is not added a second time.

## Case 15: Gemini is down or cannot be reached

- Input: no internet, a timeout or a Gemini server error, part way through a live run.
- Expected: the script stops with a plain message, like "I couldn't reach the AI service. Check your internet and try again."
- Expected: the email being handled is left untouched and is not written in the state file. Emails handled earlier stay handled.

## Case 16: the free limit is reached

- Input: Gemini says "too many requests".
- Expected: the script waits briefly and tries once more. If it still fails, it stops with a plain message saying the free limit was reached.
- Expected: the email being handled is untouched. Emails handled earlier stay handled.

## Case 17: Gemini rejects the key, or the key is missing

- Input: GEMINI_API_KEY is wrong or expired. Run with --live.
- Expected: a plain message says Gemini did not accept the key. The email is left untouched and the key is never shown.
- Input: GEMINI_API_KEY is missing. Run with --live.
- Expected: a plain message says it can't find GEMINI_API_KEY. No call is made, nothing in Outlook is changed and no part of a key is shown.

## Case 18: an email that already has one of our four categories is skipped

- Input: an Inbox email that already has "Lead - Demo", "Lead - Pricing", "Not a lead" or "Needs review".
- Expected: it is not sent to Gemini, nothing is written to it, and it is not moved.
- Expected: the report counts it as skipped.
- Input: an email with only the person's own categories (for example "Blue").
- Expected: it is not skipped, and its own categories are kept when ours is added.

## Case 19: an email already in the Leads folder is not moved again

- Input: an email that is already in the Leads folder, including one that is in the state file as moved.
- Expected: the script only reads the Inbox, so it never sees it. No request is made to move it, categorise it or send it to Gemini.

## Case 20: the Leads folder is missing

- Input: a lead is found but there is no folder called Leads.
- Expected: a plain message says it couldn't find the Leads folder. The script does not create folders.
- Expected: nothing is moved. The email is not written in the state file as handled.

## Case 21: a dry run changes nothing

- Input: new emails in the Inbox. Run without --live.
- Expected: it shows which emails it would send to Gemini (sender and subject) and says it would then add a category and move leads. It cannot say which category, because that needs Gemini's answer.
- Expected: it reads the Inbox with read-only requests. It makes no call to Gemini and sends no change request to Outlook.
- Expected: it needs no GEMINI_API_KEY and does not change the state file.

## Case 22: nothing is ever sent, replied to or deleted

- Input: a live run on several emails.
- Expected: the only change requests are adding a category and moving a lead to the Leads folder.
- Expected: no request sends, replies to, forwards, copies or deletes anything.

## Case 23: the token and key are never shown

- Input: any run, including runs that fail.
- Expected: no Outlook token, no Gemini key and no part of either is printed or saved in the state file.
- Expected: .env is not changed, except that a refreshed Outlook sign-in may be written back to the MS_TOKEN_CACHE line, as the other scripts already do.

## Case 24: --only-subject touches only matching emails

- Input: --only-subject "TRIAGE-TEST", with an Inbox that has two emails whose subject contains it and several that do not.
- Expected: only the two matching emails are sent to Gemini, categorised and (if leads) moved.
- Expected: capital letters do not matter, and the text only has to appear somewhere in the subject.
- Expected: the other emails are not sent to Gemini, not changed and not written in the state file.

## Case 25: --only-subject finds nothing

- Input: --only-subject "TRIAGE-TEST" and no new email has it in the subject.
- Expected: a plain message says no matching email was found.
- Expected: nothing is changed and no call is made to Gemini.

## Case 26: --only-subject in a dry run and a live run

- Input: --only-subject "TRIAGE-TEST" without --live.
- Expected: it shows only the matching emails and changes nothing.
- Input: the same with --live.
- Expected: only the matching emails are handled, using the same rules as any other run.

## Case 27: --only-subject and the state file

- Input: a live run with --only-subject "TRIAGE-TEST", then a normal live run without it.
- Expected: after the first run only the matching emails are in the state file.
- Expected: the second run handles the other new emails and skips the ones already handled.

## Case 28: --only-subject never widens the search

- Input: --only-subject "TRIAGE-TEST", and a matching email that is older than the start date or already has one of our four categories.
- Expected: it is not touched. The option only narrows what would be handled, never widens it.

## Case 29: an empty --only-subject is refused

- Input: --only-subject "" or only spaces.
- Expected: the script stops with a plain message that the text must not be empty, because empty text would match every email.
- Expected: nothing is read or changed.

## Case 30: when Gemini cannot be reached, the message says why

- Input: during a live run Gemini times out, there is no connection, or Gemini answers with a server error such as 503.
- Expected: the stop message names the reason, for example "(the request timed out)", "(no connection)" or "(Gemini answered with code 503)".
- Expected: the email being handled is left untouched and is not written in the state file. Emails handled earlier stay handled.
- Expected: the message never contains the Gemini key, the Outlook token, any email text, or the technical error text from the failure.

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- It uses prompt v2 and the model named in config.json.
- It reads only the Inbox, never the Leads folder.
- The only changes it makes in Outlook are adding one category and moving a lead to the Leads folder. It never sends, replies, forwards, copies or deletes.
- It never prints, logs or opens a token or key. The state file never holds any email text or secret.
- The state file is git-ignored.

## Decisions for Ilmah

Settled by Ilmah on 21 Oct. Decisions 1, 2, 3, 4, 5, 7 and 8 were accepted exactly as recommended. Decision 6 uses the alternative. The cases above use all of them.

1. **How far back does the first run look, and what counts as "new"?**
   Recommended: emails received in the last 7 days that are not in the state file, whether or not they are unread.
   Reason: it covers your recent test emails without touching old mail, and unread status changes when you open an email.
2. **What is the maximum number of emails per run?**
   Recommended: 25.
   Reason: it matches the Slack script, and with a short pause between calls it stays inside Gemini's free limit.
3. **Where does the state file live?**
   Recommended: a file called triage-state.json next to the script, added to .gitignore before it is ever created.
   Reason: it stays with the script it belongs to, and it holds only message IDs and outcomes, so it must never be committed.
4. **What counts as "unsure" (so it gets Needs review)?**
   Recommended: a reply that is not valid JSON, is missing fields, or has a request type that is not demo, pricing, partnership or other.
   Reason: Gemini gives no confidence score, so only a reply we can't trust counts as unsure.
5. **Do Not a lead emails stay in the Inbox or move somewhere?**
   Recommended: stay in the Inbox with the category "Not a lead".
   Reason: nothing is hidden, a wrong guess is easy to spot and fix, and you can sweep them by category.

Gaps I found while writing the cases:

6. **Which category do partnership and other leads get?**
   Decided: a fifth category, "Lead - Other", and those emails are moved to the Leads folder like the other leads. The five categories exist in Outlook with the exact names Lead - Demo, Lead - Pricing, Lead - Other, Needs review and Not a lead.
   (The recommended default was "Needs review" in the Inbox. Ilmah chose the alternative.)
7. **What happens with urgency?** Gemini returns it, but none of the four categories uses it.
   Recommended: record it in the state file only, and write nothing about it to Outlook in this version.
   Reason: setting importance or flags would be a third kind of write that you have not asked for.
8. **If more emails are waiting than the maximum, which go first?**
   Recommended: the oldest of the new ones first.
   Reason: every email gets its turn, and a long backlog can't hide an older lead behind newer mail.
