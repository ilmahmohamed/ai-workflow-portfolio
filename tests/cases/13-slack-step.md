# Test cases: the Slack step in the triage

Written before any code. Each case says what goes in and what should happen.
All emails and Slack messages here are pretend. A pretend Outlook and a pretend Slack are used for every test, so nothing is really posted, no real mailbox is touched, and no real webhook link appears.

What it does: when the triage accepts a lead, it posts one short message to Slack, once, before the email is categorised and moved to Leads. Which Slack channel is chosen by a switch on the triage: --slack off (the default, nothing is posted), --slack test (the test channel) or --slack live (the real #leads channel). The channel links are secrets kept in .env and are never printed or saved (SLACK_TEST_WEBHOOK_URL for the test channel, SLACK_WEBHOOK_URL for #leads).

The order for a lead is now: Gemini answers, the answer is saved on the email, (the reply draft will come here later), the Slack post, the category, the move to Leads. Because the post comes before any filing, it also comes before the Attio step's move to Processed.

The message says only: the category, the sender's address and the subject. Never the body, the draft, the lead's name, company, role, summary or any other detail.

## Part A: the Slack step itself (lead_slack.py)

### Case S1: one post, then a marker

- Input: a lead email with no marker.
- Expected: one message is posted to Slack, then a small marker (a second hidden property on the email, fixed name and shape) is saved on the email saying it was posted. The answer is "posted".
- Expected: the message text is "category: sender, subject" on one line. The characters & < > are written the safe way for Slack, a very long subject is shortened, and line breaks in the subject become spaces.

### Case S2: never twice

- Input: the email already has the marker.
- Expected: nothing is posted, and the answer is "exists". The only request is the read of the marker.
- Expected: a read of the marker that fails for any reason other than "not found" never counts as "no marker": it stops, and nothing is posted.

### Case S3: Slack has a problem

- Input: Slack answers with an error (for example 400, 403, 404, 410, 429 or 500), or cannot be reached.
- Expected: a plain message ("Slack refused the message (code 500)" or "I couldn't reach Slack"). Nothing is marked. The channel link never appears in the message.

### Case S4: Outlook has a problem with the marker

- Input: Outlook refuses or cannot be reached when the marker is read: nothing is posted, and the message is plain (sign-in may need redoing, or the usual Outlook messages).
- Input: the post worked, but saving the marker fails.
- Expected: a plain message that says the lead was posted but could not be marked, and that it could appear twice if tried again. (This one small gap cannot be closed, because Slack cannot be asked what it already holds.)

### Case S5: only safe requests

- Expected: the only requests to Outlook are: read the marker by its fixed name (no query string), and add the marker (fixed name and shape). A guard refuses everything else before it leaves the computer: sending, replying, reply-all, forwarding, moving, copying, deleting, updating, reading other things, another extension name or shape, any query string.
- Expected: the only place a message is posted is a Slack webhook address that starts with https://hooks.slack.com/services/. Any other address is refused before a request is made, with a plain message that does not show the address.
- Expected: a scan of the code finds no send, reply, forward, move, copy or delete address in it. The permission list stays exactly Mail.ReadWrite.

### Case S6: no leaks

- Expected: the code prints nothing and opens no file. No problem message holds the token, the channel link, the body, the draft or any lead detail.

## Part B: the switch and the order in the triage

### Case S7: off by default

- Expected: without --slack, or with --slack off, nothing is posted and no marker is saved, even if both channel links are set. Nothing about Slack is printed.

### Case S8: test and live use different links

- Expected: --slack test posts only to the link named SLACK_TEST_WEBHOOK_URL and never to the other. --slack live posts only to the link named SLACK_WEBHOOK_URL and never to the other.
- Input: the setting for the chosen mode is missing.
- Expected: the run stops before anything is read or changed, in a dry run too, and the plain message names the setting (not its value) and says nothing was changed.
- Expected: a --slack value other than off, test or live is refused.

### Case S9: leads only, one post each

- Expected: each lead gets exactly one post. Not a lead and Needs review emails get none, and get no marker.
- Expected: a lead that is retried is not posted again (the marker stops it): a retry of the move, a retry after a failed category change (which asks Gemini again), and a whole second run all post nothing new.

### Case S10: the order

- Expected: for a lead the changes happen in this order: the answer is saved, the Slack post, the category is added, the email is moved to Leads. The marker travels with the email when it is moved.

### Case S11: if Slack is down, the run stops

- Input: the post fails.
- Expected: the run stops with the plain message. That email gets no category, is not moved, and is not written in the state file. The saved answer stays. Nothing is posted for later emails in the run.
- Expected: the next run, with Slack working, posts that lead once and finishes it, with no second saved answer.

### Case S12: output and files

- Expected: the output says plainly when a lead was posted ("posted to Slack (test)") or was already posted earlier, and ends with the number posted. It never shows the channel link, the message text or any lead detail beyond what the triage already prints (the sender's address and subject).
- Expected: no file written by the triage holds the channel link or the Slack text.

### Case S13: a dry run posts nothing

- Expected: a dry run with --slack test or live says which channel it would use, checks the setting exists, and posts nothing, marks nothing and changes nothing.

### Case S14: pauses between posts

- Expected: the triage waits a moment between two posts in the same run, to stay inside Slack's rate limit. It does not wait before the first.
