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

<img width="648" height="429" alt="Deals tab with example for each catergory " src="https://github.com/user-attachments/assets/dc4f864c-296a-4861-b4fc-bbf38a294f52" />
<img width="653" height="437" alt="30 Fake Companies" src="https://github.com/user-attachments/assets/4ca82332-91d4-4e6c-9b7d-1bdb9f0bbb2b" />
<img width="673" height="425" alt="30 Fake People" src="https://github.com/user-attachments/assets/d80fa98d-4342-401e-bee8-68e3f134ad3b" />


## Baseline test - Manual lead entry

### What I did

I used 10 leads from Reachify. I manually entered their details under People, linked them to Companies and created corresponding Deals in Attio.

This baseline only covers those 10 leads. It excludes the fictional Nova Wellness records and other practice deals.

### Time taken

Approximately **2 hours and 35 minutes (155 minutes)**.

This includes entering information, learning how to connect the records, checking statuses and making corrections.

### How I interpreted the statuses

| Status | How I used it or what it means |
|---|---|
| New | I used this deal stage where there was no reply or confirmed progress. |
| Contacted | I used this lead status where Reachify showed that outreach had been sent. A Contacted lead could still have a deal in New. |
| Qualified | I initially treated a reply as Qualified. This needs reviewing because a reply alone does not confirm interest or suitability. |
| Meeting booked | A meeting has been confirmed. |
| Proposal | A proposal has been sent for the opportunity. |
| Won | The customer has confirmed acceptance or agreed to proceed. |
| Lost | The opportunity has been declined or confirmed as not proceeding. No reply alone does not mean Lost. |

The existing Won and Lost practice deals were not part of these 10 baseline leads.

### Mistakes, difficulties and corrections

- **I expected Lead status to update the Deals pipeline.** I later realised that Lead status and deal Stage were separate attributes and updated them separately.
- **I struggled to link People directly to Deals.** I later figured out how to add a relationship attribute and link the person to the deal. I also linked People to their Companies.
- **I confused contact activity with deal progress.** I learned that sending an email means the lead has been contacted, but does not automatically make the opportunity qualified.
- **I was unsure which deal stages to use.** I initially used replies as a shortcut for Qualified. I still need to review the responses to confirm whether that classification is appropriate.
- **Switching between Reachify, People, Companies and Deals took time.** I had to work out where each detail belonged and check that I was updating the correct record.

### Checks completed

- Checked that all 10 deals are linked to the correct company and person.
- Checked and completed the missing Lead source and Lead status values for the 10 records.

### Still to review

- Review the replies for leads marked Qualified to confirm interest and suitability.
- Verify company details against the source where automatically populated information appears inconsistent.

### What I learned

I now understand how People, Companies and Deals connect, and how to use a relationship attribute to link records. I also learned that Lead status and deal Stage need to be managed separately in my setup.

For a future comparison with automation, I will use the same fields and status rules, and record setup time separately from data entry time.

<img width="641" height="426" alt="Baseline test - Deals " src="https://github.com/user-attachments/assets/b0fbf73c-cf97-4cd9-904a-a123b1fc0814" />
<img width="480" height="229" alt="Baseline test - People" src="https://github.com/user-attachments/assets/676acf5b-f23c-4e21-a310-a0855ba9a44a" />
<img width="761" height="247" alt="Baseline test - Companies" src="https://github.com/user-attachments/assets/21e02c4f-b338-4b07-8003-f5d8e8a639fa" />
