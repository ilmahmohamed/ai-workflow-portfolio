# Week 1 log (Days 1 to 5)

Practice company: Nova Wellness (fictional). All data in this repo is made up.

## What I did, day by day

- **Day 1:** Set up my GitHub repo and README.
- **Day 2:** Practised Git on my computer (commit and push), and wrote my first 5 prompts.
- **Day 3:** Wrote a model comparison (Claude, ChatGPT, Gemini) and a research note on AgentsLab.
- **Day 4:** Built the Attio data model (People, Companies, Deals) with 30 fake leads and 6 sample deals. Then did the baseline test by entering 10 real leads by hand.
- **Day 5:** Built 3 Outlook rules (Leads, Attio, GitHub), set up Slack channels, built a Zapier automation (new Outlook email posts to Slack), and finished the prompt library (15 prompts, tested in 3 AIs).

## Baseline test

Entering 10 leads by hand in Attio (People, Companies and Deals) took **2 hours 35 minutes**. That is about 15 minutes per lead. This includes learning how to link records and fixing mistakes.

I will compare this against automation later, using the same fields and status rules.

## What went wrong

- **Baseline test:** I expected Lead status to update the Deals pipeline, but they are separate fields. I also treated a reply as "Qualified", which is not always right.
- **Attio import:** My first import left Lead source and Lead status empty because the dropdown options did not exist yet. I re-imported the same file and no duplicates were created.
- **Outlook rule, batch 1:** 2 of 5 test emails were sorted correctly.
- **Outlook rule, batch 2:** Even after adding exceptions, only 1 of 5 was correct. An internal meeting email and a newsletter went to Leads because of the words "demo" and "pricing", and a real lead with none of my keywords was missed.
- **Zapier:** My first test message had no email subject. I fixed it by inserting the Subject field and testing again.
- **Prompt tests:** Gemini invented a number I never gave it ("saves 45 minutes a day"). Claude and Gemini both guessed meeting purposes in the morning briefing until I added a rule.

## What I learned

- Word rules cannot understand meaning, so the same word can appear in a real lead and a non-lead. Exceptions help but do not solve it. An AI step that reads each email is the real fix, which I will build on Day 8 using my classify-lead prompt.
- Lead status and deal Stage are different things, and I need to manage them separately.
- A Zap only runs on real emails once it is published.
- AI tools differ. Claude followed strict formats best. ChatGPT gave ready-to-send drafts. Gemini needed more checking. I should always check AI output for details I never gave it.

## What I am proud of

- I got the Zapier automation working from Outlook to Slack.
- I built a prompt library of 15 prompts and tested them in three AIs.
- I can now explain, in simple words, why my Outlook rule makes mistakes.
- I know how to add attributes and link them on Attio
