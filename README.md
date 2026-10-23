# AI Workflow Portfolio

Practice AI automation projects for a fictional company, Nova Wellness. All data in this repo is made up.

## Who I am

Hi, I'm Ilmah Mohamed. I'm learning to build AI and automation workflows for startups, with a focus on the work an executive assistant does every day: inbox triage, replies, meeting notes and daily briefings. This is a 3-week training project.

## What I built in Week 1

- **15 tested prompts**, each run in Claude, ChatGPT and Gemini, with my choice of best tool and why
- **An Attio CRM setup** with 30 fake leads, a deals pipeline and saved views
- **A baseline test:** entering 10 leads by hand took 2 hours 35 minutes, which I will compare against automation later
- **3 Outlook rules** and a **Zapier automation** that posts new emails to Slack
- **A weekly log** of what went wrong and what I learned each day

## Screenshots

### Outlook to Slack automation (Zapier)

New emails that land in my Outlook Leads folder trigger a Zapier automation that posts an alert in Slack.

<img width="1568" height="716" alt="Zapier" src="https://github.com/user-attachments/assets/479b1b9d-1871-42d9-9f56-a9f249dca007" />

The alerts appear in the #leads channel, with the email subject included.

<img width="1372" height="887" alt="Slack" src="https://github.com/user-attachments/assets/87f79743-c5f3-4ec8-b62c-75c8c6b95fdf" />


### Outlook rules

I built 3 rules. The Leads rule moves emails with words like "demo", "pricing" and "quote" into a Leads folder, unless they also contain words like "invoice" or "unsubscribe".

<img width="1568" height="725" alt="Outlook Rules" src="https://github.com/user-attachments/assets/f515c365-d27b-435d-97dc-15b0a5b80488" />




<img width="643" height="750" alt="Outlook Lead Rules" src="https://github.com/user-attachments/assets/6ab52dbd-6bf4-4c91-93cb-cf88dc759153" />

The rule is not perfect. In my tests it got some emails wrong, because word rules cannot understand meaning. The Leads folder below shows the results, including emails I marked "Not a lead".

<img width="1444" height="816" alt="Outlook - Lead folder" src="https://github.com/user-attachments/assets/5f2ab386-9057-4855-84f9-c455e3885d4e" />


### Attio pipeline

See the screenshots in [attio/data-model.md](attio/data-model.md).

## What's in this repo

| Folder or file | What it holds |
|---|---|
| `prompts/` | My tested AI prompts, with results from three AIs and my choice |
| `model-comparison.md` | ChatGPT vs Claude vs Gemini vs Grok, by task |
| `research-agentslab.md` | Checking AI claims about a small startup against its website |
| `attio/` | Attio data model, screenshots and notes |
| `weekly-updates/` | Weekly logs: what I did, what went wrong, what I learned |
| `Baseline test` | Notes on the manual baseline test |

## Coming next

- `workflows/`: my automations (Outlook, Slack, Attio, Make, Zapier)
- `sops/`: step-by-step guides for each workflow

## Tools I use

Outlook, Slack, Attio, Zapier, Make (starting Week 2), Google AI Studio (Gemini), ChatGPT, Claude, Grok, GitHub

## Safety

No passwords or API keys are stored in this repo. They live in a private `.env` file that git ignores, and in GitHub Secrets. The tests use fake data only.

## How to read this repo

Start with `weekly-updates/week-1-log.md` for the story, then `prompts/` for examples of the work.



## What I built in Week 2: an email-to-Attio pipeline

When a lead emails the Nova Wellness mailbox, the pipeline handles it with no hands-on work:

1. **Reads** new emails in the Outlook Leads folder.
2. **Asks Gemini once** to decide if it is a lead, pull out the details (name, company, request, urgency) and write a reply draft.
3. **Saves the draft in Outlook.** It never sends anything.
4. **Adds the lead to Attio:** a person, a company (from the email domain), a deal at "New", and a note.
5. **Posts an alert in Slack #leads**, and moves the email to a Processed folder.
6. **Reports any failure in Slack #automation-log.**

| Result | Detail |
|---|---|
| Accuracy | 95.4% of fields correct on 30 test emails (target 90%) |
| Speed | 10 emails in 3 min 8 s, against 2 h 35 min by hand (about 50 times faster) |
| Runs without my laptop | Yes, on GitHub Actions (my laptop adds a faster 5-minute trigger while it is on) |
| Failures | Broken keys were tested on purpose. Each one sent a clear alert and nothing was lost or duplicated |
| Switch-off | One setting (`PIPELINE_ENABLED`) stops the whole pipeline |
| Safety | No secrets in the repo (I scanned the full history). Drafts are saved, never sent |
| Tests | 855 automated tests, written before the code |

### Week 2 day by day

- **Day 6:** build-or-configure note, Outlook sign-in, first script that posts to Slack
- **Day 7:** 30 test emails, Gemini extraction, accuracy 80.6% then 96.0%
- **Day 8:** Attio step, labels, dry run then live
- **Day 9:** model choice per step, one combined Gemini call, reply drafts, GitHub Actions, hosting write-up
- **Day 10:** faster trigger, better Slack alerts, failure tests, switch-off test, speed test, reviews of a Zap and a Make template, and a surprise email test

Full story, including what went wrong, is in [weekly-updates/week-2-log.md](weekly-updates/week-2-log.md).

### Week 2 screenshots

Jomiene sent a surprise test email from Naut Space ("Interest in newest product"). With no hands-on work, it got a Slack alert, the Lead - Pricing label, a saved draft and an Attio note on the existing deal, with no duplicates. Details: [surprise-email-test.md](weekly-updates/Surprise%20Email%20Test%20Results/surprise-email-test.md).

**Slack #leads alert**

<img width="1362" height="280" alt="Slack" src="https://github.com/user-attachments/assets/a0396aa4-aefa-4078-b954-42371ac30a4f" />


**Outlook: label, draft and Processed folder**

<img width="1600" height="791" alt="Email" src="https://github.com/user-attachments/assets/6e5243eb-3720-4f15-b0bd-a99963a02d98" />


**Attio: note on the existing deal**

<img width="1599" height="828" alt="Attio" src="https://github.com/user-attachments/assets/2c7f1344-1d64-4d3f-a3a7-05b1c959c8a4" />


The speed test results and screenshots are in [Day-10-Pipeline-Comparison](Day-10-Pipeline-Comparison/speed-test.md).

[readme-week2-section.md](https://github.com/user-attachments/files/33870400/readme-week2-section.md)

