# Test cases: the reply draft in Drafts

Written before any code. Each case says what goes in and what should happen.
All emails and answers here are pretend. A pretend Outlook is used for every test, so no real mailbox is touched, and no real key or address appears.

What it does: for a lead, it creates ONE reply draft in the Drafts folder, addressed only to the person who wrote the email. The text comes from the answer saved on the email (see 08-saved-results.md and 11-single-call-pipeline.md). The draft is never sent: nothing in this file ever sends, replies to everyone, forwards, moves, copies or deletes anything.

The finished draft text is, in this order: the line "AI draft - please review before sending", the greeting, the body, then "Kind regards," and "Nova Wellness Team" on the next line, each part in its own paragraph (see Case R2). The code adds the first line and the sign-off, never Gemini.

Microsoft's "create reply" needs only the Mail.ReadWrite permission, which is the only permission this project asks for. The pretend Outlook in the tests is strict: it answers only the request forms Microsoft documents and refuses anything else with an error 400, as the real one does.

## Part A: the reply-draft code (tests written first, in step 1)

### Case R1: a clean lead gets exactly one reply draft

- Input: an email in the mailbox, and a saved answer for a lead with a clean draft whose email address is the sender's.
- Expected: exactly one "create reply" request is sent for that email, carrying only the finished draft text (nothing else: no recipients, no other fields).
- Expected: the only changes made are that one reply draft and one small marker on the email saying a draft was made. Nothing else is changed, and no other email is touched.
- Expected: the answer is "created".

### Case R2: the finished text is exact

- Expected: the text sent is exactly: the AI line, the greeting, the body and the sign-off, each in its own paragraph, and "Kind regards," and "Nova Wellness Team" on two lines inside the last one.
- Why it is sent as simple HTML: Outlook's "create reply" puts the text into an HTML page, where plain line breaks collapse and everything runs together in one paragraph (seen in the first live draft). So each part goes in as a paragraph (`<p>...</p>`), with a line break (`<br>`) only inside the sign-off. Nothing else is used: no links, no images, no styles.
- Expected: the greeting is the start of Gemini's text up to its first comma when the text starts with "Hello" (for example "Hello Sam," or just "Hello,"). The rest is the body, and its first letter is made a capital because it now starts a paragraph. A text with no such greeting stays together as one body, unchanged. If the body already has its own blank lines, each becomes a paragraph, and a single line break inside a paragraph becomes a line break.
- Expected: any HTML or symbols in Gemini's text are written safely (shown as plain text, never as HTML), so Gemini can never add a link or a picture.
- Expected: the text is built by the code from the saved draft. A saved draft that contains its own AI line or sign-off is not used (it breaks the draft rules).

### Case R3: the draft is addressed only to the original sender

- Expected: after creating it, the code reads the draft back and checks it is a draft, is in the Drafts folder, has exactly one recipient who is the original sender (capitals and spaces in the address do not matter), has no cc or bcc, and starts with the AI line.
- Input: the draft comes back wrong in any one of those ways (another recipient, two recipients, a cc, a bcc, not a draft, not in Drafts, no AI line).
- Expected: the run stops with a plain message: the confirm step failed, the draft may be in the Drafts folder, please look there and delete it by hand. The marker is NOT written, and nothing is deleted, sent or changed. No address or draft text appears in the message.

### Case R4: no draft is made when it would be wrong

For each of these, nothing is sent to Outlook at all, and the answer is "skipped" with a plain reason that contains no address, name or draft text:
- The lead's email address is not the email's sender (a forwarded enquiry): the reply would go to the forwarder.
- The email has a Reply-To address that is not the sender: "create reply" would address the Reply-To, not the sender. (A Reply-To that is the sender itself is fine.)
- The saved answer has no draft (empty or missing), or the draft breaks a draft rule (checked again here).
- The saved answer is not a lead: not a lead, Needs review, or is_lead false.
- The sender has no usable email address.
- A person then writes the reply by hand.

### Case R5: a second draft is never made

- Input: the email already has the marker.
- Expected: nothing is created; the answer is "exists". The only request is the read of the marker.
- Input: no marker, but Drafts already holds a draft in the same conversation (for example the program stopped after creating a draft and before writing the marker, or you started your own reply).
- Expected: nothing is created, the marker is written, and the answer is "exists".
- Input: the program stops after creating the draft (writing the marker fails), then runs again.
- Expected: the second run finds the draft in Drafts, creates no second one, and writes the marker. In total, exactly one "create reply" request was ever sent.
- Input: the same email handled twice in a row.
- Expected: the second time sends no change at all.
- Input: you delete the draft by hand later.
- Expected: it is not made again (the marker stays).
- Input: a draft in a different conversation.
- Expected: it does not block a new draft.

### Case R6: when Outlook has a problem

- Input: Outlook answers "not allowed" or "sign-in not accepted" to the create request.
- Expected: a plain message says the sign-in may need redoing (run outlook_signin.py again), with no code number. Nothing is created, no marker is written, and it does not try again.
- Input: any other Outlook error (for example 500), or the connection is lost, at any step.
- Expected: a plain message ("Outlook refused the request" or "couldn't reach Outlook"). A failed read (the marker, the Drafts check, or the check of the new draft) is never taken to mean "nothing there": it stops, and nothing is created after it.
- Input: the connection is lost or Outlook refuses while checking the new draft.
- Expected: the message is the confirm-step message of Case R3 (the draft may be in Drafts), and no marker is written.

### Case R7: the guard

- Expected: every request goes through one guard that allows only these: read the Drafts folder id; read Drafts for one conversation (only the id); read the marker by its fixed name; read the new draft with its few fixed fields; create the reply with only the text; add the marker (fixed name and shape).
- Expected: the guard refuses everything else before it leaves the computer: sending, the sending "reply", reply-all, create reply-all, forwarding, create forward, moving, copying, deleting, updating (patch or put), any other create, a create-reply with anything besides the finished text (a recipient, a "message", extra fields, text that does not start with the AI line or does not end with the fixed sign-off, text that is not text, the old plain-text form, or any HTML other than paragraphs and the one line break in the sign-off: no link, picture, script or style), any other path, and any request with a query string other than the fixed ones.
- Expected: a scan of the code's text finds no send, sending-reply, reply-all, forward, move, copy or delete address, and no Mail.Send. The permission list stays exactly Mail.ReadWrite.
- Expected: the draft step makes no move at all.

### Case R8: nothing leaks and nothing is written locally

- Expected: the code prints nothing and opens no file, so no lead detail, draft text, address or token can reach a log or a file.
- Expected: no problem message contains the token, the draft text, the sender's address or the lead's name.

### Case R9: planning without sending anything

- Expected: a pure check says, without any request to Outlook, whether a draft would be made or why not (the same reasons as Case R4). A dry run uses it.

### Case R10: looking without changing anything

- Expected: a read-only question says whether a draft was already made for an email (a marker on it, or a draft in Drafts in the same conversation). It sends only the two reads, never writes the marker and changes nothing. A failed read is never taken to mean "no". The dry run of the one-off check (Part C) uses it.

## Part B: wiring it into the triage (tests written first, in tests/test_outlook_triage.py)

### Case W0: the switch

- Expected: the triage has a switch `--drafts off|on`, off by default, in the same way as `--slack`. With it off (or not given) the triage does exactly what it did before: no request about drafts at all and nothing about drafts in its output. Any other value is refused before any request. So the scheduled runs are unchanged until it is switched on.

### Case W1: the order for a lead

- Expected: with `--drafts on`, a lead is handled in this order: save the answer, announce it in Slack once (only when `--slack` is test or live), make the reply draft once, add the category, then move the email to Leads. The email still goes to Leads, never straight to Leads/Processed: the Attio step reads the Leads folder and moves it to Processed itself.
- Expected: only leads get a draft. Not a lead and Needs review get none.
- Expected: the draft is made from the same answer, with the finished text of Case R2, addressed only to the sender, as in Part A.
- Input: making the draft fails (Outlook refuses, the sign-in is refused, the connection is lost, or the draft comes back wrong).
- Expected: the run stops with the plain message of Part A. That email is left untouched (no category, not moved, not in the state file), so the next run tries it again, and the emails after it are left untouched too. The saved answer is kept, and a lead already announced in Slack is not announced again. The marker and the Drafts check stop a second draft: if the draft was made but the confirm step failed, the next run finds it in Drafts and makes no second one.
- Input: the draft was made but a later step failed (the category change or the move).
- Expected: the next run makes no second draft and no second Slack post, and finishes the lead. A move retry does not touch drafts at all.
- Expected: a second run over the same emails makes nothing more.

### Case W2: a lead that gets no draft carries on

- Input: a lead for which Case R4 says "skipped" (forwarded, a Reply-To that is somebody else, no usable draft).
- Expected: it is still saved, categorised and moved. The output says plainly that no reply draft was made and why, with no address, name or draft text.
- Expected: the end of the run says how many reply drafts were made and how many were not (and that a person writes those by hand). A draft that was already made earlier is said so, not counted as new.

### Case W3: dry run and logs

- Expected: a dry run with `--drafts on` says drafts are on and that nothing is made in a dry run. It sends nothing to Gemini and makes no change and no request about drafts. (The reason a lead gets no draft is only known after Gemini has answered, so a dry run cannot say it.)
- Expected: the output, the state file and any local file never contain the draft text, a lead's details or an address beyond what the triage already printed (the sender's address and subject).

### Case W4: the triage stays send-proof

- Expected: the only changes the triage can ask Outlook for are: add a category, save the answer, make the reply draft and its marker (and the Slack marker), and move a lead to Leads. Nothing sends, replies to everyone, forwards, copies or deletes, and the permission stays exactly Mail.ReadWrite.

## Part C: the one-off live check (its own cases are written in step 3)

- A small script, dry run by default, that makes one reply draft from the answer saved on one chosen test email, so the first live draft can be looked at before anything is wired in.
