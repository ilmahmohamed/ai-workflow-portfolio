# Week 2 log (Days 6 to 10)

## Summary

- Built an email-to-Attio pipeline: Outlook Leads folder, Gemini reads each email once, Attio gets a person, a deal at New and a note, Slack #leads gets an alert, and a reply draft is saved in Outlook (never sent).
- Moved it from my laptop to GitHub Actions, so it runs with the laptop closed.
- Accuracy on the 30 test emails: 95.4% of fields correct (prompt v3, gemini-3.5-flash-lite). Target was 90%.
- Every failed run posts to Slack #automation-log. The pipeline can be switched off in one step (`PIPELINE_ENABLED`). No secret is in the repo.

## Day by day

### Day 6 (Mon 19 Oct): first script
- Wrote a build-or-configure note: what Attio already does natively, what is left to build, and the monthly cost.
- Set up the Outlook sign-in through Microsoft Graph and an Azure app.
- First script reads the Leads folder and posts sender and subject to Slack #leads. Tests were written before the code.

### Day 7 (Tue 20 Oct): extraction and accuracy
- Made 30 test emails (25 AI-written, 5 of my own) and wrote the expected result for each.
- Built the extraction step with the model set in a config file. Gemini returns JSON: name, email, company, role, request type, urgency.
- Prompt v1 scored 80.6%. After fixing the answer key (two emails were not leads) and improving the prompt, v2 scored 96.0%. Results are in `accuracy.md`.

### Day 8 (Wed 21 Oct): Attio step
- Built the Attio step: find or create the person, create the company from the email domain (not for free-mail domains such as gmail.com), add a deal at New with a note, and move handled emails to Leads/Processed.
- Wrote the tests first, did a dry run, then went live.
- Added the Gemini labels (Lead - Demo, Pricing, Other, Needs review, Not a lead).
- **What went wrong:** the scheduler ran only on my laptop, so nothing happened when it was closed.

### Day 9 (Thu 22 Oct): models and deployment
- Compared models for each AI step and wrote `model-choice.md`.
- Merged the two AI reads into one Gemini call (prompt v3) that classifies, extracts and writes the draft reply. Accuracy 95.4%.
- Saved reply drafts in Outlook with a "never sends" rule.
- Deployed on GitHub Actions: the mailbox is the only memory (no saved files), logs hide email addresses, and keys live in GitHub Secrets.
- Proof it works: a scheduled run handled a test email with my laptop closed (label, Processed folder, draft, Attio person, deal and note, Slack alert).
- Wrote the hosting options write-up (`hosting-options.docx`) and chose GitHub Actions.
- **What went wrong:**
  - Gemini timed out once, so the run stopped safely and changed nothing. I added a longer timeout, one retry, and skipping a failing email so the others still run.
  - My Slack webhook secrets were broken, which stopped a run. I re-set them.
  - I ran the workflow as a dry run by mistake several times. Manual runs default to dry run; scheduled runs are always live.

### Day 10 (Fri 23 Oct): making it automatic and testing
- **Faster trigger.** GitHub's schedule runs about every 30 to 45 minutes, not every 15. While my laptop is on, a Windows task now asks GitHub to run the pipeline every 5 minutes. When the laptop is off, GitHub's own schedule still runs it.
- **What went wrong:** the Windows task never started because Windows does not run scheduled tasks on battery by default. I allowed it to start on battery, and it then ran on time.
- **Better Slack lead alert:** it now shows the category, the sender's name and address, the subject and a 200-character preview. The preview goes only to Slack and is never printed in the GitHub logs.
- **Better failure alert in #automation-log:** it now says which step failed, the reason in plain words, how many emails are waiting, and the first one waiting (sender name and subject only, no address). 855 tests pass.
- **Failure tests on the real system.** I broke a key on purpose and watched the alert arrive:
  - wrong Gemini key: the run stopped at Triage and nothing in Outlook, Slack or Attio changed. The alert said "Gemini rejected the key".
  - wrong Attio key: the lead alert still posted, then the alert said "Attio rejected the key, Waiting: 2 in Leads". After I restored the key, the next run retried the email and added the Attio note. Nothing was lost or duplicated.
- **Failure cases covered by tests:** broken AI output, empty body, same email twice, expired Outlook sign-in, Gemini unreachable, Attio failing part way.
- **One-click switch-off.** I set `PIPELINE_ENABLED` to false and the next runs were skipped, with no changes in Outlook, Slack or Attio. Setting it back to true restarted normal runs.
- **Surprise email test:** a test email I had not prepared ("Interest in newest product", from Naut Space) arrived at 4:29 PM. With no hands-on work, it got a Slack alert, the Lead - Pricing label, a saved draft and an Attio note on the existing deal, all within a minute. No duplicates. See `surprise-email-test.md`.
- **Speed test:** see below.
- **No secrets in git.** I scanned all 73 commits for keys, webhook links and passwords. Only obviously fake test values were found.
- **Reading other automations:** reviewed my own Zapier Zap and a public Make template (see `automation-reviews.md`) and wrote why I chose a script over Zapier for the Slack alert (`zapier-vs-custom.md`).

## Findings

- Attio matches people by email. Emails from the same address become notes on the same person and deal. 
- Gmail senders get a person and a deal but no company, because gmail.com is not a real company. The studio name is saved in the deal note. Creating companies from names in the email body risks duplicates.
- A Dutch lead got an English draft reply. The extraction handled the Dutch email correctly, but the draft was not in Dutch.
- GitHub pauses scheduled workflows after 60 days with no repo activity. A daily "Pipeline alive" message in #automation-log shows the schedule is still running.

## Speed test

| | By hand (Day 4 baseline) | Pipeline (Day 10) |
|---|---|---|
| Emails or leads | 10 leads | 10 emails (9 leads, 1 newsletter) |
| Total time | 2 h 35 min | **3 min 8 s** |
| Time per lead | about 15.5 min | about 21 s |
| My hands-on time | all of it | **0** |

About 50 times faster, with no hands-on time. All 9 leads got a Slack alert, a reply draft and an Attio note, and the newsletter was skipped. Caveat: all 10 emails came from one address, so Attio added notes to one existing person instead of creating 10 people, companies and deals as I did by hand. Details are in `day-10-pipeline-comparison`.

## What I learned

- A schedule on my laptop is not automation. It must run somewhere that stays on.
- A GitHub schedule is a target, not a promise, so a faster trigger helps when timing matters.
- Test the unhappy paths: a failed email should be skipped and reported, not break the run. Breaking a key on purpose proved the alerts work.
- A scheduled task on Windows will not start on battery unless I allow it.
- Reading other people's automations (a Zap and a Make template) shows how much a simple automation leaves out: filtering, failure alerts and a CRM record.
- One AI call that does three jobs is cheaper and faster than three separate calls.
