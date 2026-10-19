# Test cases: Outlook Leads folder to Slack #leads

Written before any code. Each case says what goes in and what should happen.
All emails are fake. No real names, keys or customer data.

## Case 1: one lead email

- Input: one email in the Leads folder. Sender: sam.fake@example.com. Subject: "Pricing for your wellness plan".
- Expected: one message in Slack #leads showing the sender and the subject.
- Expected: the email is not changed or moved yet.

## Case 2: the Leads folder is empty

- Input: no emails in the Leads folder.
- Expected: nothing is posted to Slack.
- Expected: the script finishes without an error.

## Case 3: Slack cannot be reached

- Input: one email in the Leads folder, but the Slack address does not work.
- Expected: the script reports the failure clearly and does not pretend it worked.
- Expected: nothing is lost, so the email is picked up again on the next run.

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- In a dry run it prints what it would post and sends nothing to Slack.

