# Attio data model

I set up Attio for the fictional company Nova Wellness. All data is made up.

## Objects I used

- **People:** 30 fake leads imported from a CSV that I generated with AI.
- **Companies:** 30 fake companies, one per lead, linked to each person.
- **Deals:** 6 sample deals, one in each stage of the pipeline.

## Attributes I added to People

| Attribute | Type | Values |
|---|---|---|
| Lead source | Select | Website, Referral, LinkedIn, Event |
| Lead status | Select | New, Contacted, Qualified, Lost |
| Country | Text | |
| Notes | Text | |

Attio already had Name, Email addresses, Company and Job title.

## How the objects connect

- Each person is linked to one company.
- Each deal is linked to a person and a company, and has me as the deal owner.

## Lists and views

- **Inbound leads (list):** a hand-picked group of the 8 people whose Lead source is Website.
- **All leads (view):** every person, with all my columns.
- **Qualified leads (view):** a saved filter, Lead status is Qualified (7 people).
- **New leads (view):** a saved filter, Lead status is New (9 people).

A list holds records I choose myself. A view is a saved filter that updates by itself.

## Deals pipeline

New, Qualified, Meeting booked, Proposal, Won, Lost.

## What went wrong and how I fixed it

My first import left Lead source and Lead status empty, because Attio had not created the dropdown options. I imported the same file again, matched on email address, and turned on "Create missing select options". Attio updated the 30 people and created no duplicates.

## Baseline test

(To fill in after the test: number of lead emails, time taken, mistakes.)
