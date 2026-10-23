# Speed test: pipeline vs doing it by hand (Day 10)

Practice company: Nova Wellness (fictional). All data here is made up. Test run on Fri 23 Oct 2026. Times are Sri Lanka time (UTC+5:30).

## Result

| | By hand (Day 4 baseline) | Pipeline (this test) |
|---|---|---|
| Leads or emails | 10 leads | 10 emails (9 leads, 1 newsletter) |
| Total time | 2 h 35 min (155 min) | **3 min 8 s** |
| Time per lead | about 15.5 min | about 21 s |
| My hands-on time | all of it | **0** |

The pipeline was about **50 times faster** (155 min / 3.13 min), with no hands-on time.

## How it was measured

1. Sent 10 test emails to the Nova Wellness mailbox. First sent at 3:11:25 PM, last at 3:11:34 PM.
2. Did nothing else. The pipeline ran by itself on GitHub Actions.
3. Read the times from Slack, Outlook and the Attio notes.

| Moment | Time |
|---|---|
| First email sent | 3:11:25 PM |
| Pipeline run started (scheduled) | 3:11:58 PM |
| First Slack alert and draft | 3:12 PM |
| Last Slack alert and draft | 3:13 PM |
| Last Attio note created | 3:14:33 PM |

## What the pipeline did with each email

| # | Subject | Result |
|---|---|---|
| 1 | Demo request for our barre studio | Lead - Demo, Slack alert, draft, Attio note |
| 2 | Pricing for our climbing gym | Lead - Pricing, Slack alert, draft, Attio note |
| 3 | Question about member reminders | Lead - Other, Slack alert, draft, Attio note |
| 4 | Partnership with our nutrition clinic | Lead - Other, Slack alert, draft, Attio note |
| 5 | Need a booking system for our dance school | Lead - Demo, Slack alert, draft, Attio note |
| 6 | Can we book a demo? Urgent | Lead - Demo, Slack alert, draft, Attio note |
| 7 | Class pack pricing question | Lead - Pricing, Slack alert, draft, Attio note |
| 8 | Info for our physio practice | Lead - Demo, Slack alert, draft, Attio note |
| 9 | Demo aanvragen voor onze yogastudio (Dutch) | Lead - Demo, Slack alert, draft, Attio note |
| 10 | Wellness Weekly: 5 trends this month | Not a lead: skipped, no Attio record |

All 9 leads were handled, the newsletter was skipped, and no draft was sent.

## Limits of this comparison

- **Not like for like.** All 10 emails came from one Gmail address, so Attio matched the existing person and added a note to the existing deal. On Day 4 I created a person, a company and a deal for every lead by hand. A fairer repeat would use ten different senders.
- **One run.** This is one test, not an average. GitHub's schedule can be delayed, so a run could take longer.
- **The Dutch lead got an English draft reply.** The extraction handled the Dutch email correctly, but the reply was not written in Dutch. Open question for Dasun: should drafts match the sender's language?

## Screenshots

Slack #leads alerts, the Outlook Processed folder and Drafts, and the Attio activity list for this test are in this folder.
