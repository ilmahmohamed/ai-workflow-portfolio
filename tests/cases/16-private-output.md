# Test cases: nothing about an email is printed or logged

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend. Every test uses unmistakable fake markers (a sender at "secret-company.example", "PRIVATE-NAME-MARKER", "PRIVATE-SUBJECT-MARKER" and so on), so a leak cannot be missed. No real address or lead appears in this file.

Why: before the pipeline runs on GitHub Actions, its logs may be seen by more people than the Outlook mailbox. So every line the pipeline prints (and the scheduler logs, which are only those lines) must say nothing about a sender, a person, a subject or an email's text. They use only counts, categories, step names and a short ID.

Covered: the triage (`outlook_triage.py`, including its Slack and reply-draft steps), the Attio step (`leads_to_attio.py`) and the older Outlook-to-Slack script (`outlook_to_slack.py`).

Not covered, on purpose:
- What the pipeline is FOR still holds the details: the Slack post (category, sender, subject), the reply draft, the answer saved on the email, and everything written into Attio (people, companies, deals, notes).
- The one-off tools you run by hand on test emails (the Day 8 loader, the reply-draft check, the write, draft and extension checks, the accuracy script, the sign-in) still show an email's sender and subject, because their whole job is to point at one email.
- What is printed is the point of this file, but the state files are kept private too (Case P6): the triage state holds only Outlook IDs, categories, stages, dates and each email's short ID. The Attio state holds only Outlook IDs, status, a short reference and our own plain reason.

## Case P1: the short ID

- Expected: an email is named in the output by a short ID such as `e-1a2b3c`: "e-" and six letters or digits, made from the email's own Message-ID header (or from its Outlook ID if it has none). It says nothing about the email: it cannot be turned back into the sender or the subject.
- Expected: the same email always gets the same ID, and different emails get different IDs.
- Expected: the ID stays the same when the email moves from the Inbox to Leads (the Outlook ID changes on a move, the Message-ID header does not). So the triage and the Attio step name the same email the same way, and a retry can be followed across runs.

## Case P2: the triage prints only IDs, categories and counts

- Expected: each handled email is one line: its ID, its category, and "moved to Leads" for a lead (for example `e-1a2b3c: Lead - Pricing, moved to Leads`). A retry says `e-1a2b3c: moved to Leads (retry)`.
- Expected: a dry run lists `would send to Gemini: e-1a2b3c`, and a system or security email is `Skipped (system or security email): e-1a2b3c`.
- Expected: the lines about Slack, the reply draft and the counts at the end are as before (they never held a detail), and a lead that gets no draft still says why in the plain words of 12-reply-drafts.md, with no address or name.
- Expected: when moving an email fails, the message names it by ID.
- Expected: `--only-subject` text is not echoed back. A run that finds nothing says "No new email matching the --only-subject filter was found. Nothing was changed."

## Case P3: the Attio step prints only IDs, steps and counts

- Expected: each lead starts with `Email: e-1a2b3c`, followed by steps in plain words that name nobody: "created person" (or "found person"), "created company" (or "company already exists", or "no company: free-mail domain"), "created deal (stage New, owner set)" (or "adding a note to the existing New deal"), "added a note to the new deal", "moved the email to Leads/Processed". A dry run says "would ..." in the same way.
- Expected: no person's name or address, no company name or domain, no deal name.
- Expected: "Already handled earlier, left alone", "Still needs a person" and "Needs a person to look at it" give the ID and the reason in our own words (for example "no usable answer was saved on the email").
- Expected: the ID is the same one the triage printed for that email.
- Expected: `--only-subject` and `--only-from` text is not echoed back. A run that finds nothing says "No lead matching the --only-subject text was found" (or the --only-from address, or both) "Nothing was changed."
- Expected: the leads are listed together with their Message-ID header, so the ID can be made.
- Expected: what is written into Attio and into the notes there does not change.

## Case P4: the older Outlook-to-Slack script prints only a number

- Expected: `would post: message 1 of 3` and `posted: message 2 of 3`, never the sender or the subject. The Slack post itself still says who wrote and what about.

## Case P5: failures say nothing about anyone

- Input: Slack refuses or cannot be reached, a reply draft is refused or comes back wrong, a category change or a saved answer is refused, Outlook cannot be reached, Gemini times out, is busy, refuses or answers with broken text, Attio refuses or is busy or slow or runs out of requests, a saved answer cannot be read, or a move is refused. In each, the error text from the other service contains pretend private words.
- Expected: the message is one of our own plain sentences. It never contains the other service's text, a sender, a name, a company, a deal, a subject or a draft, and nothing is saved to a file.

## Case P6: the state file keeps no Message-ID header

- Why: an email's Message-ID header can contain the sender's mail domain, and the state file may one day be kept somewhere others can see it.
- Expected: the triage state holds for each email only: the outcome (the category), the stage, the urgency, the date and the short ID. No sender, name, subject, Message-ID header, email text or answer, and nothing with an "@" or a "<".
- Expected: a state file from before this change that still holds Message-ID headers has each one replaced by the short ID made from it the next time the state is saved. The email keeps the name it always had, and nothing else about its record changes. A dry run never rewrites the file.
- Expected: the checks of Case P7 cover the files too: after every run, none of the private words may be in any file the script wrote.

## Case P7: every test checks this

- Expected: in every test of these three scripts, whatever is printed is checked against all the senders, names, reply-to addresses and subjects of the pretend emails, the details in Gemini's pretend answers, the details saved on the emails and the names Attio holds. None may appear, and nothing shaped like an email address may appear. The files the script writes are checked too.
- Expected: the scheduler batch files add nothing but the run's own printed lines, a time and an exit code to the logs.
