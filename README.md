# AI Workflow Portfolio

Practice AI automation projects for a fictional company, Nova Wellness. All data in this repo is made up.

## Who I am

Hi, I'm Ilmah Mohamed. I'm learning to build AI and automation workflows for startups, with a focus on the work an executive assistant does every day: inbox triage, replies, meeting notes and daily briefings. This is a 3-week training project.

## What I've built so far (Week 1)

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

## How to read this repo

Start with `weekly-updates/week-1-log.md` for the story, then `prompts/` for examples of the work.
