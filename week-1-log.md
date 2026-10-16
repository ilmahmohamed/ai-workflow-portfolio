# Week 1 log (Days 1 to 5)

Practice company: Nova Wellness (fictional). All data in this repo is made up. No passwords, keys or real lead details are included.

## Summary

- Set up the full practice stack: Outlook, Slack, Attio, GitHub, Zapier, and Claude, ChatGPT, Gemini and Grok.
- Built and tested a 15-prompt library across three AIs.
- Built an Attio pipeline with 30 fake leads, plus a baseline test of 10 leads entered by hand.
- Built 3 Outlook rules and a working Outlook-to-Slack Zap.

## Day by day

### Day 1 (Mon 12 Oct): set up the sandbox
- Set up the Nova Wellness identity (one company name used across all tools).
- Created the Outlook account, a Slack workspace with a #leads channel, and an Attio workspace.
- Created accounts for Claude, ChatGPT and Gemini, and set up my GitHub account and public repo with a README.
- Connected Outlook, Slack and Attio to the automation tool.
- **What went wrong:** Google sign-in clashed with an older account I had used before, so I had to sign out and sign in again with the new one.

### Day 2 (Tue 13 Oct): prompting basics
- Wrote 5 executive assistant prompts: inbox triage, meeting preparation brief, email thread summary, Slack update and follow-up email.
- Ran each prompt in ChatGPT, Claude and Gemini with identical wording, and saved the results in the `prompts/` folder.
- My choices: Gemini won inbox triage (used exactly the five columns I asked for). Claude won the meeting brief, Slack update and follow-up email. ChatGPT won the email thread summary.
- Set up Gemini access for later work and checked its rate limits.
- Practised Git on my computer (commit and push).
- **What went wrong:** I fixed a garbled line in prompt 2. I also created an empty repository by mistake while adding a file, then deleted it carefully, making sure I was not deleting my real project.

### Day 3 (Wed 14 Oct): comparing models
- Ran the same 5 tasks in Grok.
- Researched a small Dutch startup (AgentsLab) in all four AIs, and checked each claim against the company website to catch made-up facts.
- Wrote `model-comparison.md` (a table of task x model x verdict) and `research-agentslab.md`.
- **Lesson:** a small, little-known company is the best test for invented facts, because the AIs have less to go on.

### Day 4 (Thu 15 Oct): Attio fundamentals
- Generated 30 fake leads with AI and imported them into Attio as People, linked to 30 fake Companies.
- Built the Deals pipeline (New, Qualified, Meeting booked, Proposal, Won, Lost) with a sample deal in each stage.
- Built 3 views and 1 list: All leads, Qualified leads, New leads, and an Inbound leads list. Took screenshots for `attio/`.
- Wrote `attio/data-model.md`.
- **Baseline test:** entered 10 leads by hand (People, Companies and Deals) in **2 hours 35 minutes**, about 15 minutes per lead.
- **What went wrong:** The first import left Lead source and Lead status empty because the dropdown options did not exist. I created the missing options and re-imported with no duplicates. During the baseline I also expected Lead status to move the deal stage, but they are separate fields.

### Day 5 (Fri 16 Oct): Outlook, Slack, Zapier and prompts
- Added descriptions, countries and founded dates to the 30 fake companies by exporting, editing and re-importing a CSV matched on Record ID.
- Built 3 Outlook rules (Leads, Attio, GitHub) and tested them with 10 emails.
- Set up Slack channel descriptions, a pinned message and an #automation-log channel.
- Built and published a Zapier automation: new Outlook email posts to Slack with the subject.
- Extended the prompt library to 15 prompts (6 to 15 for an executive assistant role), tested in Claude, ChatGPT and Gemini.
- **What went wrong:** Outlook test batch 1 got 2 of 5 correct and batch 2 got 1 of 5, even after adding exceptions. An internal "demo" email and a newsletter went to Leads, and a real lead with none of my keywords was missed. My first Zapier message also had no email subject. I fixed that by adding the Subject field.

## Baseline test

Entering 10 leads by hand in Attio took **2 hours 35 minutes**. This includes learning how to link records and fixing mistakes. I will compare this against automation later, using the same fields and status rules.

## What I learned

- **Prompts:** Give the AI a role, context, rules and a format. Fix the prompt, not the output.
- **Models:** Claude followed strict formats best. ChatGPT gave ready-to-send drafts. Gemini needed checking, because it invented a number I never gave it. Always check an AI's output for details I did not provide.
- **Attio:** Lead status and deal stage are different fields, and imports fail quietly if dropdown options do not exist yet.
- **Outlook:** Word rules cannot understand meaning. The same word can appear in a real lead and a non-lead. Exceptions help but do not solve it, so the real fix is an AI step that reads each email, which I will build in Week 2.
- **Zapier:** A Zap only runs on real emails once it is published. On the free plan it checks for new emails on a schedule, so the Slack message arrives a few minutes late.
