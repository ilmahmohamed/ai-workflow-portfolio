# Test cases: the Day 8 loader (30 test emails into the Inbox)

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched.

Why: the 30 test emails (tests/cases/lead-emails.md) exist only as text. To run them through the whole pipeline they must be in the Outlook Inbox with their own senders, subjects and bodies. Real mail cannot have 30 different senders, so a small one-off loader creates them directly in the Inbox. Outlook's "create message" always makes the message a draft, so they will look like drafts in the Inbox; that is expected, and the pilot proves the triage handles them.

The loader never sends, replies, forwards, moves, copies or deletes anything. It only adds messages to the Inbox, and only ones whose subject starts with the tag [DAY8]. It is never scheduled. It is dry run by default.

## Case L1: reading the 30 emails

- Expected: the loader reads the 30 emails from lead-emails.md, each with its From line (a name and address, or an address alone), its subject and its body, exactly as written.
- Expected: it refuses to start if the file is damaged or an email has no From or Subject.

## Case L2: what is created

- Expected: each email becomes one message with: the same From as the file, the subject with "[DAY8] " put in front, the same body text (accented letters kept), and a made-up recipient on a .example address.
- Expected: no copy or blind-copy recipient, no attachment, nothing else.
- Expected: the message is sent to Outlook as MIME text (the form Microsoft documents), encoded as it should be.

## Case L3: dry run by default

- Expected: without --live it only reads (the Inbox, Leads and Leads/Processed for already-loaded emails) and prints a line for each email it would create, with the From address and the tagged subject. It creates nothing.

## Case L4: --live creates exactly the chosen emails

- Expected: with --live, one create request per chosen email, sent to the Inbox folder only, and a line saying it was created. The end says how many were created and that they look like drafts in the Inbox.
- Expected: --pilot picks exactly two emails: email 9 (a pricing lead) and email 5 (an out-of-office reply, not a lead). --only 9,5 picks the same two. Neither option can pick an email that is not in the file; an unknown number is refused before any request.
- Expected: without --only or --pilot it picks all 30.

## Case L5: never twice

- Input: an email with the same tagged subject is already in the Inbox, Leads or Leads/Processed.
- Expected: it is skipped and reported as "already loaded". Running --live twice creates nothing the second time.

## Case L6: when Outlook has a problem

- Input: Outlook refuses ("not allowed" or "sign-in not accepted") or cannot be reached while reading or creating.
- Expected: a plain message (sign-in may need redoing, or couldn't reach Outlook), no technical text, and it stops. The ones already created stay; the next run skips them (Case L5). No retry of the failed one inside the same run.

## Case L7: only safe requests

- Expected: the only requests are: read the tagged emails of a folder (the filter, the fields and the count are fixed), and create one MIME message in the Inbox. A guard refuses everything else before it leaves the computer: sending, replying, forwarding, moving, copying, deleting, updating, creating anywhere but the Inbox, other reads, any other query.
- Expected: a scan of the code finds no send, reply, forward, move, copy or delete address. The permission list stays exactly Mail.ReadWrite.

## Case L8: no leaks

- Expected: no token is printed. The loader reads no .env itself (only the normal sign-in helper does), writes no file, and prints no email body.
