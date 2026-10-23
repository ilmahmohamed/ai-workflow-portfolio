# Test cases: the failure alert in #automation-log (reason, how many are waiting, the first one waiting)

Written before any code. Each case says what goes in and what should happen.
All emails here are pretend, with unmistakable fake markers. No real address, key, link or lead appears in this file.
The checks are in `tests/test_alert_message.py` (the message) and `tests/test_workflow.py` (the workflow's alert step).

Why: the alert used to say only which step failed. To know what to do, a person also wants to know why, how much is stuck, and what is stuck first. The alert is a live-run alert only (a dry run posts nothing), and it must never be the thing that fails.

## Case A1: the message, one item per line

- Expected: five lines, in this order: `Pipeline step failed: <step>.`, `Reason: <reason>`, `Waiting: <n> in the Inbox` (after a failed Triage) or `Waiting: <n> in Leads` (after a failed Attio sync), `First waiting: <sender's name>, "<subject>"`, `Run: <link>`.
- Expected: a name or subject with `&`, `<` or `>` is written the safe way for Slack, line breaks become spaces, a long subject is cut with `...`, a missing name shows `(no name)`, a missing subject shows `(no subject)`.

## Case A2: the reason comes from a fixed list

- Input: the filtered log of the failed step, holding one of the plain sentences the scripts print (for example "Gemini did not accept the key", "I couldn't reach Attio", "Your saved sign-in no longer works").
- Expected: the reason is the matching entry of a fixed list, such as `Gemini rejected the key`, `Attio could not be reached` or `Outlook sign-in expired`. Capitals are ignored, and the more specific entry wins.
- Expected: a log with anything else, an empty log, a missing or unreadable log file: `Reason: Unknown error, see the run`.
- Expected: no word of the log is ever copied into the alert, however much surrounds the known sentence. A failed install or set-up has its own fixed reason and no lookup.

## Case A3: how many are waiting, and which one is first

- Expected: after a failed Triage, the Inbox emails that are not handled yet (not tagged, not system or security emails, within the 7-day look-back) are counted, and the oldest is named. After a failed Attio sync, the emails in Leads (system emails left out) are counted, and the oldest is named.
- Expected: only the sender's display name and the subject are taken. Never an address, never any body text.
- Expected: the lookup only reads the mailbox. It makes no change in Outlook, posts nothing, writes no file, and anything it prints is thrown away.

## Case A4: nothing private, and it always posts

- Expected: a key, token, Slack webhook link or address found in a name or subject is replaced by `[removed]`, and so is the value of any secret that happens to appear. Values shorter than six characters are not used for this, so ordinary words are not wiped out.
- Expected: if the lookup fails (Outlook down, sign-in expired, bad answer, anything), the alert still posts, without the Waiting and First waiting lines. If the helper itself gives nothing, the workflow posts the old plain message (`Pipeline step failed: <step>. Run: <link>`).
- Expected: the helper's error output is never shown in the run log.

## Case A5: the workflow's alert step

- Expected: each of the two steps keeps a copy of its filtered log in the runner's temporary folder (after the filter, so without addresses). The copy is not saved anywhere else and is gone when the run ends.
- Expected: the alert step runs only after a failure in a live run, as before. It gets only the secrets it needs (the #automation-log link and the Outlook sign-in) and posts what the helper printed.
- Expected: a manual dry run still posts nothing.
