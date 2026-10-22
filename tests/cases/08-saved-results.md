# Test cases: saving Gemini's full answer on the email itself

Written before any code. Each case says what goes in and what should happen.
All emails and answers here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched.
No real keys, tokens or lead details appear in this file.

Why: the triage asks Gemini ONE question per email (prompt v3) and gets everything back: is_lead, category, name, email, company, role, request type, urgency, summary and a draft reply. That answer is saved on the email itself in Outlook (a small "open extension" attached to the message, which only your mailbox holds). The Attio step later reads that saved answer and never calls Gemini. Nothing is saved in a local file, so it also works on GitHub Actions, where local files are not kept.

The saved answer has these fields: is_lead, category, name, email, company, role, request_type, urgency, summary, draft_reply. For an email that is not a lead only is_lead and category are filled in and the rest are empty (null).

## Case 1: a full answer is saved on the email

- Input: an email in the mailbox and a complete answer for it (a lead, with every field filled in, including a draft reply).
- Expected: exactly one change request is sent: add one extension to that one email.
- Expected: the extension has the fixed name, a version number (1), and the answer stored as one piece of JSON text. Nothing else is in the request.
- Expected: no other email is touched.

## Case 2: reading it back gives the same answer

- Input: an email that has a saved answer.
- Expected: reading it returns exactly the answer that was saved, including empty (null) fields, a summary in a non-English language with accents, and urgency as a whole number.
- Expected: only read requests are sent, and there is exactly one: it asks for the one extension by its fixed name on that one email, with no query string. (A live check showed Outlook refuses the request "give me this email with all its extensions" with an error 400. Reading one extension by name is the form Microsoft documents.)
- Expected: the pretend Outlook in the tests is strict about this, answering only that form and refusing anything else with a 400, as the real one does.

## Case 3: nothing saved yet

- Input: an email with no saved answer, or an email that has only some other program's extension.
- Expected: reading returns "nothing saved" (no answer, no error). The other program's extension is ignored and left alone.
- Input: Outlook answers "not found" (404) to the read.
- Expected: that also means "nothing saved", and saving then goes ahead.
- Expected: the Attio step can then say "needs a person" instead of calling Gemini.

## Case 4: a damaged saved answer counts as nothing saved

- Input: an extension of ours whose text is not JSON, or is JSON that is not an answer (a list, a number), or has no is_lead, or has an is_lead that is not true or false, or has the wrong version number.
- Expected: reading returns "nothing saved". It never crashes and never guesses.

## Case 5: saving twice does not change the first answer

- Input: an email that already has a saved answer, and a second, different answer for it.
- Expected: no change request is sent, and the first answer is kept.
- Expected: it reports that an answer was already saved.

## Case 6: an incomplete or oversized answer is refused

- Input: an answer with a field name that is not in the list, or without is_lead or category, or with is_lead that is not true or false, or with a category that is not text.
- Expected: it is refused with a plain message and nothing is sent.
- Input: an answer whose saved text would be longer than the limit (8,000 characters).
- Expected: refused with a plain message that says it is too long, and nothing is sent.

## Case 7: when Outlook has a problem

- Input: Outlook answers "not allowed" or "sign-in not accepted" when saving or reading.
- Expected: a plain message says the sign-in may need redoing (run outlook_signin.py again). No technical error text.
- Input: no internet.
- Expected: a plain message says it couldn't reach Outlook.
- Expected: when reading fails (any problem other than "not found"), it does NOT pretend nothing was saved. It stops with the problem, so an outage is never mistaken for "needs a person".
- Expected: if the check-before-saving read fails, nothing is saved.

## Case 8: only the allowed requests can ever be sent

- Expected: the only requests this part can send are: read our one extension, by its fixed name and with no query string, on one email; and add one extension of our fixed name, with exactly the fixed shape, to one email.
- Expected: a guard refuses everything else before it leaves the computer: sending, replying, forwarding, moving, copying, deleting, updating, reading a whole email, listing an email's extensions, reading another extension name, reading with any query string, other paths, another extension name, an extension with extra fields.
- Expected: a scan of the code's text finds no send, reply, forward, move, copy or delete address in it, and nothing asks for the Mail.Send permission.

## Case 9: no files, no printing, no token on screen

- Expected: saving and reading never read or write any local file (so an empty folder, like on GitHub Actions, works the same).
- Expected: nothing is printed by it at all, so no lead detail can reach a log.
- Expected: no token is printed, and .env is not touched.
