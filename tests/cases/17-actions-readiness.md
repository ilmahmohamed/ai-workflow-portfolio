# Test cases: ready to run on GitHub Actions (no saved state, a clean log, pinned libraries)

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend, with unmistakable fake markers. No real address, key or lead appears in this file.

Why: on GitHub Actions every run starts on a fresh machine, so nothing a run saves to a file is there for the next one. The mailbox itself has to be the memory: the categories on the emails, the hidden markers on them (the saved answer, "posted to Slack", "draft made"), the Leads and Processed folders, and what Attio already holds. Three small changes make that safe, plus a filter for the log and a pinned list of libraries.

## Case S1: a "no state" switch for both steps

- Expected: `--stateless` on the triage and on the Attio step means: no state file is read, none is written (not even a temporary one), and no "starting fresh" note is printed. A state file that happens to exist is left exactly as it is, and is not used.
- Expected: without the switch, nothing changes: the state file is used and written as before.
- Expected: in a dry run the switch changes nothing either (a dry run never wrote state).

## Case S2: a lead stuck in the Inbox is moved without any state

- Why: the triage adds the category and then moves a lead to Leads. If the move fails, the email sits in the Inbox with its lead category. Until now only the state file knew to retry the move, so without it that email would be skipped for ever.
- Input: an email in the Inbox that already carries one of the three lead categories (Lead - Demo, Lead - Pricing or Lead - Other, capitals and spaces ignored), and no state record for it.
- Expected: it is moved to Leads without asking Gemini, without a second category, and without posting to Slack or making a draft again (those were done before the category, and their markers stop a second one). It is counted under its category, and the line is `e-1a2b3c: moved to Leads (retry)`. A dry run says "would retry moving to Leads".
- Expected: Not a lead and Needs review emails that carry their category are still left alone, and so is an email whose state record says it is finished.
- Expected: the whole round trip works with no state file: a first run where the move is refused, then a second run in a new process, which moves the email and changes nothing else.

## Case S3: system emails are counted, not listed

- Expected: the triage and the Attio step print only `System or security emails skipped: N`. They no longer list each skipped email, and the triage no longer remembers which ones it has reported (so no "already reported" note). The emails are still left completely untouched.
- Expected: an old triage state file that still holds a list of reported system emails has that list dropped the next time the state is saved, and is left alone by a dry run.

## Case S4: the Attio step needs no state either

- Input: `--stateless`, a lead whose saved answer is usable.
- Expected: person, company, deal and note are made as usual and the email is moved to Leads/Processed, with no state file written.
- Input: a run that stopped part-way (Attio refused the note, or Outlook refused the move), then a new run with no state.
- Expected: the new run finds the person, company and deal that already exist, adds the note only if it is not there yet (the note carries a reference to the email), and moves the email. Nothing is made twice.
- Input: `--stateless`, a lead with no usable saved answer.
- Expected: it is not listed email by email (that would repeat every 15 minutes): the output says only `Needs a person: N`, and the emails stay in Leads. A failure still says which email was being handled, by ID.
- Known small gap: if a run stops after creating a new person and before linking the person to the company, the new run finds the person and does not link them (it only links people it has just created). The deal is still linked to the company. This used to be repaired by the state file.

## Case S5: the log filter

- Expected: `log_filter.py` reads text and writes it back line by line with anything shaped like an email address replaced by `[removed]`: plain addresses, addresses with plus signs, dots, capitals or sub-domains, addresses in angle brackets or after "mailto:", and addresses with accented letters. Everything else is passed through unchanged, byte for byte: IDs, counts, categories, plain words, a lone "@" or an @handle.
- Expected: running the filter twice changes nothing more. It never stops or fails, whatever it is given (empty input, very long lines, damaged text), and it always ends successfully, so it can never hide the real result of a run. Lines appear as they come, not all at the end.

## Case S6: the pinned list of libraries

- Expected: `requirements.txt` at the top of the project lists exactly what the scripts need, each pinned to one exact version: `msal`, `requests` and the libraries they need. No web addresses, no options, no unpinned line.
- Expected: the versions pinned are the ones installed here, so the tests run on the same libraries as the Actions machine will.
