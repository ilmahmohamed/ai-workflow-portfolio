# Rules for the expected answers

These rules say how each of the 30 emails in `lead-emails.md` should be read.
The answers that follow from them are in `expected-results.json`.

## Fields for every email

- `id`: the email number, 1 to 30.
- `is_lead`: true for a genuine enquiry. False for a non-lead, such as an out-of-office reply, a newsletter, an auto-reply or a sales pitch to us.

## Not a lead

- An existing customer asking for support (for example an invoice copy) is not a lead.
- An invitation or a media request (for example a podcast interview) is not a lead.

## Real leads

- A partnership proposal from another business is a lead.
- If the body is almost empty but the subject states a clear request (for example "Demo?"), it is a lead.

## If is_lead is false

- Give only `id` and `is_lead`. Do not add any other fields.
- Getting `is_lead` wrong still counts as an error.

## If is_lead is true

Also give these fields:

- `name`
- `email`
- `company`
- `role`
- `request_type`
- `urgency`
- `summary`

## request_type

One of: `demo`, `pricing`, `partnership`, `other`.

- If both demo and pricing are asked, use the first thing asked.
- A quote counts as pricing.

## urgency

Urgency is based on when a response or action is needed.

- **3** = a named date or time, or words like "urgent" or "ASAP". Examples: "by 3 pm today", "by Friday".
- **2** = a loose period. Examples: "this month", "next week", "in the next few weeks", "before quarter end".
- **1** = no time pressure, or no hint at all.
- A suggested meeting time on its own (for example "Would sometime next week work?") counts as 1.

## role

- role = the job title or profession, if stated. 'I run X' with no title is null.

## Duplicates

- The same person emailing twice stays as two separate entries. Merging duplicates is handled later, in the Attio step.

## Missing information

- Use `null` for anything not stated. Do not guess.
- `company` comes only from the text or the signature. Never work it out from Gmail, Outlook, example.com or any email address.

## Forwarded emails

- Use the original enquirer's details when the enquiry is in the forwarded message.
- If it is unclear who the lead is, use `null`. Do not guess.

## Dutch emails

- Write the `summary` in English.

## summary

- One short line.
- It is included in the answer but is not scored by exact match.

## How these rules were applied to the answer file

The rules above are yours. The points below are small choices I made where the rules were silent. Change any you disagree with and the answer file should follow.

- `name` and `email` come from the sender's name and address on the email (or from the forwarded message for forwards). If there is no sender name, the name in the signature is used.
- `role` is filled only when a job title or profession is stated. A person who only says "I run X" gets `null`.
- Names and companies are written in normal capitalisation, even if the email is in capitals.
- Two emails from the same person (for example 9 and 16) are two separate entries, each with its own answer.
- A meeting time the writer only offers or asks about counts as 1. A period the writer says they want the action done in counts as 2.

## Key corrections

20 Oct: after the first live run (prompt v1, 80.6% against the old key), emails 22 and 24 were changed from lead to not a lead, because they broke the rules in this file. The v1 score was measured against the old key.
