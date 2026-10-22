# Test cases: the one-off reply draft check

Written before any code. Each case says what goes in and what should happen.
All emails and answers here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched.

Why: before the reply draft is wired into the triage (see 12-reply-drafts.md, Part B), one real draft is made from the answer saved on one chosen Day 8 test email, so it can be looked at in the Drafts folder first. This small script does that. It is a test tool: it is never scheduled and the triage does not use it.

It makes the same draft as the real step (it calls the same code), addressed only to the person who wrote the email. It never sends, replies to everyone, forwards, moves, copies or deletes anything. It is a dry run unless --live is given.

## Case K1: choosing the one email

- Input: --only-subject with some text from the subject (capitals do not matter), and optionally --only-from with the sender's address (capitals and spaces do not matter).
- Expected: only emails whose subject starts with the tag [DAY8] are ever looked at, in the Inbox, Leads and Leads/Processed. A real email can never be chosen, even if its subject matches.
- Expected: exactly one email must match. If none matches, a plain message says so and nothing is changed. If more than one matches, a plain message says so (and to narrow it with more of the subject or with --only-from), and nothing is changed. It never picks one for you.
- Expected: --only-subject is required. Empty text (or only spaces) is refused before any request, because empty text would match every email.

## Case K2: dry run by default

- Expected: without --live it only reads (the tagged emails, the answer saved on the chosen one, the marker, and Drafts) and changes nothing.
- Expected: it prints the sender's address and the tagged subject (as the triage does), the category from the saved answer, and what it would do: create one reply draft addressed only to the sender, made of the AI line, the greeting, the body (Gemini's saved text; only its word count is shown) and the fixed sign-off, each on its own line, then read it back to check it, then mark the email so a second draft is never made. It ends by saying nothing was sent and that --live makes it for real.
- Expected: the draft text itself is never printed.
- Input: a draft was already made for this email (it has the marker, or Drafts already holds a draft in the same conversation).
- Expected: it says so and that nothing would be created.
- Input: the email is one for which no draft would be made (not a lead, forwarded, a Reply-To that is somebody else, no usable draft).
- Expected: it says no draft would be made and gives the plain reason from 12-reply-drafts.md, Case R4.
- Input: no answer is saved on the email.
- Expected: a plain message says the triage has to run on it first, and it stops.

## Case K3: --live makes exactly one draft

- Expected: with --live, exactly one reply draft is created for the chosen email and one marker is added to it. Nothing else changes and no other email is touched.
- Expected: it says the draft was created, that it was read back and checked, that it is in the Drafts folder, that nothing was sent, and that the draft should be deleted by hand after it has been looked at.
- Expected: the draft is addressed only to the sender, with no cc or bcc (the code behind it checks this, see Case R3).

## Case K4: never twice

- Input: --live again for the same email, or for an email that already has a draft in the same conversation.
- Expected: it says a draft was already made and sends no change at all.

## Case K5: no draft when it would be wrong

- Input: --live for an email where Case R4 says no draft would be made.
- Expected: nothing is changed, and a plain reason is shown. The script reports that no draft was made, as a stop (exit code 1), because the check did not produce a draft.

## Case K6: when something goes wrong

- Input: Outlook refuses the request ("not allowed" or "sign-in not accepted"), or cannot be reached, or the draft comes back wrong.
- Expected: a plain message (the sign-in may need redoing, or couldn't reach Outlook, or the confirm-step message of Case R3 saying the draft may be in Drafts and should be deleted by hand), with no code number and no technical text. It stops and does not try again.
- Input: the saved sign-in does not work.
- Expected: its plain message is shown and nothing is requested.

## Case K7: only safe requests

- Expected: the script's own requests are only the read of the tagged emails of a folder (the filter, the fields and the count are fixed). Everything else goes through the guards of the reply draft code and of the saved-answer code, which allow only what their own cases say. Nothing here can send, reply to everyone, forward, move, copy or delete. The permission list stays exactly Mail.ReadWrite.
- Expected: a scan of the script's text finds no send, reply, forward, move, copy or delete address and no Mail.Send.
- Expected: nothing else uses this script. (The triage uses the reply draft code itself, behind its own `--drafts` switch, see 12-reply-drafts.md Part B; the Attio and Slack steps do not make drafts.)

## Case K8: no leaks

- Expected: no token is printed, no draft text, no lead name. The script reads no .env itself (only the normal sign-in helper does) and writes no file.
