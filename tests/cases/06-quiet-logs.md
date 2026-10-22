# Test cases: quiet logs and the Attio scheduler file

Written before any code. Each case says what goes in and what should happen.
These cover the small helper that keeps a log file short (trim_log.py) and the two batch files that Windows Task Scheduler runs (run_triage.bat and run_leads_to_attio.bat). The triage's own "report a system email only once" rule is Cases 44 and 45 in 04-outlook-triage.md.
No real keys, tokens or personal folder names appear in this file.

## Case 1: a short log is left alone

- Input: a log file with fewer lines than the limit (for example 500 lines kept, 100 lines of slack, and the file has 300 lines).
- Expected: the file is not changed at all.

## Case 2: a long log is cut to its latest lines

- Input: a log file with more lines than the limit plus the slack (for example 800 lines).
- Expected: only the last 500 lines are kept, in the same order, and the first kept line is a whole line.
- Expected: the newest lines (the end of the file) are the ones kept.

## Case 3: the file is only rewritten once it has grown well past the limit

- Input: a log file that is over the limit but within the slack (for example 550 lines).
- Expected: it is not rewritten yet, so the file is not rewritten on every run. At 601 lines it is cut back to 500.

## Case 4: nothing breaks

- Input: the log file does not exist, is empty, or has no final line ending.
- Expected: no error. A missing or empty file is left as it is, and a last line without a line ending is kept as it is.
- Input: the file cannot be rewritten (for example it is locked).
- Expected: the helper quietly does nothing, never stops the run, and leaves no half-written copy behind.

## Case 5: the text is kept exactly

- Input: a log with characters that are not plain text (for example odd bytes from another code page).
- Expected: the kept lines are byte for byte the same as before. Nothing is decoded, changed or lost.

## Case 6: the helper can be started from the batch file

- Input: `python trim_log.py LOGFILE` and `python trim_log.py LOGFILE 200`.
- Expected: the first keeps 500 lines, the second keeps 200. It prints nothing and always ends successfully, even with a missing file, a missing argument, or a number that is not a number.

## Case 7: the batch files are safe to share

- Input: run_triage.bat and run_leads_to_attio.bat.
- Expected: each finds the project folder from its own location, with no user name or personal folder written in it.
- Expected: each runs its script with --live, adds a dated heading and the output to its own log file (triage-log.txt or leads-to-attio-log.txt), and records the exit code.
- Expected: each trims its own log (keeping the latest lines) before it starts writing, and never trims the other one's log.
- Expected: the Attio batch file runs leads_to_attio.py and writes only leads-to-attio-log.txt.

## Case 8: the logs are never committed

- Input: .gitignore.
- Expected: it covers triage-log.txt and leads-to-attio-log.txt, including the temporary copy the helper uses while trimming.
