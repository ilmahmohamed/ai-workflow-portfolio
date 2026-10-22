# Test cases: one-off Outlook draft check

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched.
No real keys or tokens appear in this file.

What the check does: creates ONE draft email in the Drafts folder, with obviously fake text and no recipient, then reads it back to confirm it is really a draft sitting in Drafts. It never sends anything. The draft is left in Drafts for you to delete by hand.

The fake draft:
- Subject: "FAKE DRAFT - delete me - not for sending"
- Text: one line saying it is a fake draft made by a test and should be deleted
- Recipient: none at all (so it could not be sent by accident)

## Case 1: dry run changes nothing

- Input: Drafts folder with no leftover fake draft. Run without --live.
- Expected: it prints "Dry run", the subject it would use, that it would create 1 draft in Drafts with no recipient, and that nothing is sent.
- Expected: it makes only read requests to Outlook. It sends no change request at all.
- Expected: the Drafts folder is exactly as it was.

## Case 2: live creates exactly one draft in Drafts

- Input: Drafts folder with no leftover fake draft. Run with --live.
- Expected: exactly one draft is created, in the Drafts folder.
- Expected: it reads the draft back and confirms it is a draft, is in Drafts, and has no recipient.
- Expected: a short plain message says the draft was created in Drafts and was NOT sent, and that it should be deleted by hand.
- Expected: the script does not delete the draft.

## Case 3: the draft is obviously fake and has no recipient

- Input: a live run.
- Expected: the subject is "FAKE DRAFT - delete me - not for sending" and the text says it is fake.
- Expected: the request carries only a subject and a plain-text body. It has no To, Cc or Bcc, and nothing else.

## Case 4: a leftover fake draft stops the check

- Input: Drafts already holds a draft whose subject is exactly "FAKE DRAFT - delete me - not for sending". Run with and without --live.
- Expected: a plain message says a fake draft is already in Drafts, to delete it by hand first, and that nothing was created.
- Expected: nothing is created or changed.
- Input: drafts with similar subjects (different capitals, extra words, "Re:" in front) and none exactly matching.
- Expected: they do not count, and the check goes ahead. Those drafts are left alone.
- Input: the exact subject with extra spaces at the start or end.
- Expected: it counts as a leftover.

## Case 5: Outlook refuses to create the draft

- Input: Outlook answers "not allowed" or "sign-in not accepted" when the draft is created. Run with --live.
- Expected: a plain message says Outlook refused, that the sign-in may need redoing (run outlook_signin.py again), and that nothing was created.
- Expected: no technical error text, and the script does not try again.
- Input: the connection is lost while the draft is created.
- Expected: a plain message says it couldn't reach Outlook.

## Case 6: the confirm step fails

- Input: the draft is created, but reading it back fails (Outlook error, lost connection), or Outlook did not give back an id, or what comes back is not a draft, is not in Drafts, or has a recipient.
- Expected: a plain message says the confirm step failed, the check did not pass, and the draft may be in Drafts, so look there and delete it by hand.
- Expected: it does not pretend it worked, and it does not delete or send anything.

## Case 7: nothing is ever sent, moved, deleted or changed

- Input: a live run, with other drafts and emails in the pretend mailbox.
- Expected: the only change request sent is one request to create a draft in the Drafts folder. No other email is touched.
- Expected: no request sends, replies to, forwards, moves, copies, updates or deletes anything.
- Expected: the script's own request guard refuses any other kind of request before it leaves the computer. It refuses: sending a draft, sending directly, replying, forwarding, moving, deleting, updating an email, creating a message anywhere but Drafts, a create request with a recipient or any extra field, and reading any address outside the Drafts folder and single emails.
- Expected: a scan of the script's text finds no send, reply, forward, move or delete address in it.

## Case 8: the permission stays "read and write mail", never "send"

- Expected: the permission list in outlook_signin.py is exactly Mail.ReadWrite.
- Expected: no script in the workflows folder asks for the Mail.Send permission or names a send address.
- Expected: the script uses the same saved sign-in as the other scripts. It does not ask for a new sign-in.

## Case 9: the token is never shown

- Input: any run, including runs that fail.
- Expected: no token, sign-in value or part of one is printed on screen.
- Expected: .env is left alone, except that a refreshed sign-in may be written back to the MS_TOKEN_CACHE line, as the other scripts already do.

## Case 10: sign-in is missing or Outlook cannot be reached

- Input: MS_CLIENT_ID or MS_TOKEN_CACHE is missing, or the saved sign-in no longer works.
- Expected: a plain message says to run outlook_signin.py. No request is sent.
- Input: no internet.
- Expected: a plain message says it couldn't reach Outlook, and nothing is created.

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- It reuses the sign-in settings and saved sign-in from outlook_signin.py, the same way the other scripts do.
- It never prints, logs or opens a token or key.
