# Zapier vs my own pipeline: which should send the Slack alert, and why

Practice company: Nova Wellness (fictional). Written Fri 23 Oct 2026.

## My choice

**The script sends the Slack alert, not Zapier.** The Zap is still in my account, but "Send Channel Message?" is set to No, so it posts nothing.

## Why I chose the script

| Question | Zapier Zap | My script (GitHub Actions) |
|---|---|---|
| What starts the alert? | Any new email in the Leads folder | Only emails the AI accepted as leads, after labelling and checks |
| Can it skip junk? | No. A newsletter that lands in Leads gets an alert | Yes. Newsletters and out-of-office replies are skipped quietly |
| What does the alert say? | Subject, sender and a body preview | Category, sender name and address, subject and a 200-character preview. It can grow as needed |
| If something fails | Nothing tells me | A message goes to #automation-log with the step, the reason and what is waiting |
| One alert per email? | Zapier's own checks | Yes. A marker is saved on the email, so a retry cannot post it twice |
| Speed | Checks about every 15 minutes on the free plan | Every 5 minutes while my laptop is on, and GitHub's own schedule when it is off |
| Cost | Free plan has 100 tasks a month. Each email uses at least one task, so a busy month would run out | Free. GitHub Actions is free for a public repo |
| Privacy | The email text passes through Zapier's servers | The text only goes to Gemini and Slack. The logs hide email addresses |
| Testing | I cannot run automated tests on a Zap | 855 automated tests, including failure cases |
| Who can change it? | A non-technical person, by clicking | Someone who can read Python |

## Where Zapier would be the better choice

- A client's team has no one technical and needs to edit the alert themselves.
- A very simple alert is all that is needed, with no AI step, no Attio and no draft reply.
- It must be set up in minutes. My Zap took a few minutes to build, the script took days.

## The honest trade-offs of my choice

- **More to look after.** If a secret expires or GitHub changes something, I have to fix it. A Zap would mostly look after itself.
- **A schedule that can drift.** GitHub's schedule runs late (every 30 to 45 minutes instead of 15). The 5-minute laptop trigger only helps while my laptop is on.
- **Hard for a non-coder to change.** The alert wording lives in code, so changing it needs a developer or Claude Code.

## Conclusion

For a pipeline that also classifies, extracts, writes to Attio and saves drafts, one place that does everything and reports its own failures is safer than a Zap bolted on the side. For a simple "tell me when an email arrives" alert, I would use Zapier.
