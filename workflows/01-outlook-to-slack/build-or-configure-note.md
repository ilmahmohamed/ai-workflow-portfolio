# Build or configure - Nova Wellness email-to-Attio pipeline

Written on Day 6 as practice. Before building anything, I checked what Attio can already do on its own.

## What Attio does by itself

- Email sync can create people and companies from emails, but only for Microsoft 365 mailboxes. It does not work with a free outlook.com mailbox like the Nova Wellness one.
- It also creates records for newsletters, auto-replies and people who are only cc'd.
- It cannot judge whether an email is really a lead. It only has blocklists.
- Workflows can create a deal and post to Slack, but any AI step uses credits.
- It cannot save a draft reply in Outlook.

## What is left to build

| Step | Attio alone | Needs a script |
|---|---|---|
| Read the Leads folder | Not with a free mailbox | Yes |
| Skip auto-replies and newsletters | Blocklists only | Yes |
| Pull out name, company, request, urgency | AI step, costs credits | Yes |
| Create or update person, company, deal | Yes, with a workflow | Could stay in Attio |
| Post to Slack #leads | Yes | Could stay in Attio |
| Save a draft reply in Outlook | No | Yes |

## Cost (estimates from the Week 2 plan, to be checked on Day 9)

- Attio credits: Free 250 a month, Plus 1,500, Pro 10,000.
- Example: 200 emails, 80 of them leads is about 680 credits a month, which fits Plus (about $105 a month).
- With research on each lead it is about 2,280 credits, which needs a higher plan (about $175 to $237 a month).
- The script: about $2 to $5 a month for AI use, plus about an hour a month of upkeep.

## Recommendation

Build the script for the filter, the data extraction and the Outlook draft, because Attio cannot do these well. Let Attio keep storing the records and checking for duplicates. If a client is already on Attio Plus with a Microsoft 365 mailbox, an Attio workflow may be the better choice.
