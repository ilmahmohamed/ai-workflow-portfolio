# Test cases: one-off Outlook write check

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched.
No real keys or tokens appear in this file.

What the check does: finds the email in the Inbox whose subject is exactly "WRITE-CHECK test". With --live it adds the category "Needs review" to that one email, confirms Outlook accepted it, then removes the category again so the email ends up as it was before.

## Case 1: exactly one match works

- Input: one email in the Inbox with the subject "WRITE-CHECK test". Run with --live.
- Expected: the category "Needs review" is added to that email, and the script confirms Outlook accepted it.
- Expected: the category is then removed, and the email's categories are exactly what they were before.
- Expected: a short plain message says the write check worked and the email is back as it was.

## Case 2: no match

- Input: no email in the Inbox with that subject. Run with and without --live.
- Expected: a plain message says no email with the subject "WRITE-CHECK test" was found.
- Expected: nothing is changed.

## Case 3: more than one match

- Input: two or more emails in the Inbox with the subject "WRITE-CHECK test". Run with --live.
- Expected: the script refuses and says how many it found and that it needs exactly one.
- Expected: nothing is changed on any email.

## Case 4: only the exact subject counts

- Input: emails with subjects like "write-check TEST", "WRITE-CHECK test 2" and "Re: WRITE-CHECK test", and none that is exactly "WRITE-CHECK test".
- Expected: none of them counts as a match, so it behaves as in Case 2.
- Input: one email whose subject is exactly "WRITE-CHECK test" with extra spaces at the start or end.
- Expected: it counts as a match.

## Case 5: Outlook refuses the write

- Input: one matching email, but Outlook answers "not allowed" or "sign-in not accepted" when the category is added. Run with --live.
- Expected: a plain message says Outlook refused the change and that the sign-in may need redoing (run outlook_signin.py again).
- Expected: nothing is changed on the email.
- Expected: no technical error text.

## Case 6: the category is removed again even if the confirm step fails

- Input: the category is added, but the confirm step fails or does not show the category (Outlook errors, or the category is missing when read back).
- Expected: the script still removes the category.
- Expected: a plain message says the confirm step failed, so the check did not pass, and that the email was put back.
- Expected: the email's categories end up exactly as before.

## Case 7: the category cannot be removed

- Input: the category is added and confirmed, but Outlook refuses to remove it.
- Expected: a plain message says "Needs review" may still be on the email and how to remove it by hand.
- Expected: the script stops with a failure and does not pretend it worked.

## Case 8: other categories are kept

- Input: the matching email already has other categories (for example "Blue" and "Leads").
- Expected: while the check runs, "Needs review" is added next to them and they are not lost.
- Expected: at the end the email has exactly the same categories, in the same order, as before.

## Case 9: the email already has "Needs review"

- Input: the matching email already has the category "Needs review". Run with --live.
- Expected: the script stops with a plain message saying it can't test a category that is already there, because it could not put the email back exactly as it was.
- Expected: nothing is changed.

## Case 10: dry run changes nothing

- Input: one matching email. Run without --live.
- Expected: it prints what it would do ("would add the category Needs review to 1 email, confirm it, then remove it again") and shows the email's subject.
- Expected: it makes only read requests to Outlook. It sends no change request at all.
- Expected: it needs no Slack link.

## Case 11: nothing is ever moved, deleted, sent or replied to

- Input: a live run on one matching email.
- Expected: the only change requests sent are two category updates on that one email (add, then remove).
- Expected: no request moves, deletes, sends, replies to or forwards anything, and no other email is touched.

## Case 12: the token is never shown

- Input: any run, including runs that fail.
- Expected: no token, sign-in value or part of one is printed on screen.
- Expected: no token is saved anywhere, except that a refreshed sign-in may be written back to the MS_TOKEN_CACHE line in .env, as the Slack script already does.

## Case 13: sign-in is missing or Outlook cannot be reached

- Input: MS_CLIENT_ID or MS_TOKEN_CACHE is missing, or the saved sign-in no longer works.
- Expected: a plain message says to run outlook_signin.py. No request is sent.
- Input: no internet.
- Expected: a plain message says it couldn't reach Outlook, and nothing is changed.

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- It reuses the sign-in settings and saved sign-in from outlook_signin.py, the same way the Slack script does.
- It never prints, logs or opens a token or key, and it never changes .env except the refreshed sign-in line.
