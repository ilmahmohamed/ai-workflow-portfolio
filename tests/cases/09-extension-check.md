# Test cases: one-off check that saved data survives a move

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched.
No real keys or tokens appear in this file.

Question this check answers: when the triage saves Gemini's answer on an email and then moves the email into Leads, is the saved answer still on the email afterwards? Microsoft says a move "creates a new copy of the message", and does not say whether the saved data comes along. This check finds out for real, safely.

Why a fake draft and not an Inbox email: the Inbox is triaged every 2 minutes, so a test email there would be picked up by the live triage. A draft in Drafts is left alone.

What the check does, with --live:
1. Makes one fake draft in Drafts (same subject idea as the draft check, no recipient).
2. Saves a pretend answer on it (the same kind of extension the real code will use, about the size of a real answer).
3. Reads it back and checks it is the same.
4. Moves the draft to Deleted Items (a normal move; the draft ends up in the bin).
5. Reads it back from its new place and checks it is still there and the same.
It never sends anything and never deletes anything.

The fake draft: subject "FAKE EXTENSION CHECK - delete me - not for sending", a one-line fake body, no recipient.

## Case 1: dry run changes nothing

- Input: no leftover fake draft. Run without --live.
- Expected: it prints "Dry run", the subject, and the five things it would do. It says nothing is sent.
- Expected: only read requests are sent. Drafts is exactly as it was.

## Case 2: the data survives the move

- Input: a live run, and a mailbox where moved messages keep their extensions.
- Expected: the requests, in order, are: read Drafts, create the draft, add the extension, read it back, move to Deleted Items, read it back at the new place.
- Expected: each read-back asks for the one extension by its fixed name on that one email (the new email id for the second one), with no query string. A first live run showed Outlook refuses the "email with all its extensions" form with a 400, so the pretend Outlook in the tests is strict and answers only the documented form.
- Expected: it says the check passed: the saved data stayed on the email after the move, and that the fake draft is now in Deleted Items.

## Case 3: the data is lost in the move

- Input: a live run, and a mailbox where a moved message comes back without its extension (Outlook answers "not found" to the read).
- Expected: it says plainly that the saved data did NOT survive the move, so another place to keep it is needed. It is not a pass, and the exit code says failure.
- Input: the read-back fails for any other reason (Outlook error 500, sign-in not accepted).
- Expected: that is NOT reported as "did not survive". It is reported as a plain problem, with where the fake draft may be. Nothing is moved after a failed read.

## Case 4: the saved data comes back changed

- Input: what is read back (before or after the move) is not the same as what was saved.
- Expected: not a pass. A plain message says the data came back different.

## Case 5: a leftover fake draft stops the check

- Input: Drafts already holds a draft with exactly that subject (extra spaces around it still count). Run with and without --live.
- Expected: a plain message says to delete it by hand first. Nothing is created.
- Input: drafts with similar subjects.
- Expected: they do not count and are left alone.

## Case 6: Outlook refuses a step

- Input: Outlook answers "not allowed" or "sign-in not accepted" when the draft is created, when the extension is added, or when the draft is moved.
- Expected: a plain message names the step, says the sign-in may need redoing, and says where the fake draft may be (Drafts, or Deleted Items), so it can be removed by hand. No technical error text, no retry.
- Input: the connection is lost partway.
- Expected: a plain message says it couldn't reach Outlook, and where the fake draft may be.

## Case 7: nothing is ever sent, replied to, forwarded, copied or deleted

- Expected: the only change requests are: create one draft in Drafts, add one extension to it, and move that one draft to Deleted Items.
- Expected: a guard refuses everything else before it leaves the computer. A move may only go to Deleted Items. An extension may only have the fixed name and shape. Reading is allowed only for Drafts and for our one extension by its fixed name with no query string, so reading a whole email, listing extensions, reading another extension or adding a query string is refused. Another email is never touched.
- Expected: a scan of the script's text finds no send, reply, forward, copy or delete address in it, and no Mail.Send.
- Expected: the permission list stays exactly Mail.ReadWrite.

## Case 8: the token is never shown

- Input: any run, including runs that fail.
- Expected: no token or sign-in value is printed. .env is left alone, except that a refreshed sign-in may be written back to the MS_TOKEN_CACHE line, as the other scripts already do.

## Case 9: sign-in missing or Outlook unreachable

- Input: MS_CLIENT_ID or MS_TOKEN_CACHE is missing, or the saved sign-in no longer works.
- Expected: a plain message says to run outlook_signin.py. No request is sent.
- Input: no internet.
- Expected: a plain message says it couldn't reach Outlook, and nothing is created.

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- It reuses the sign-in settings and saved sign-in from outlook_signin.py, the same way the other scripts do.
