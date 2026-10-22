# Test cases: Leads folder to Attio

Written before any code. Each case says what goes in and what should happen.
All people and emails are pretend. Company domains use the reserved fake ending ".example" (for instance brightwell.example), so no real company is involved. A pretend Outlook, a pretend Gemini and a pretend Attio are used for every test, so no real mailbox, no real AI call and no real Attio data is touched.
No real keys, tokens or owner details appear in this file.

What the script does: it reads the emails in the Outlook Leads folder. For each one it asks Gemini (the existing extraction) for the name, email, company, role, request type, urgency and summary. Then in Attio it:
1. finds or creates the person, using the email address (one person per address),
2. finds or creates the company, using the email domain (one company per domain; free-mail domains get no company),
3. creates a deal named "<company or person name>: <request type>" at stage New with an owner, linked to the person and company, and adds a note. If the same person already has a deal at stage New, it adds a note to that deal instead.
After everything worked, it moves the email from Leads to Leads/Processed. If anything fails, the email stays in Leads and the next run tries again, without duplicates.

> **Update (22 Oct, privacy).** Wherever a case below says the output names an email's sender and subject, a person, a company, a domain or a deal, it now shows only a short ID such as `e-1a2b3c` and plain steps ("created person", "created deal (stage New, owner set)"), and `--only-subject` and `--only-from` text is not echoed back. What is written into Attio and its notes does not change. See 16-private-output.md.
>
> **Update (Day 8).** The Attio step no longer asks Gemini. The triage asks Gemini once (prompt v3) and saves the answer on the email; this step reads that saved answer, and an email with no usable saved answer is marked "needs a person". So the cases about Gemini in this file (34, 35 and 57 to 62) describe the old behaviour and are kept only for history. They are replaced by Part B of 11-single-call-pipeline.md, and their tests were rewritten. Case 63 was added after the Day 8 run. The live results and one known Attio side effect are at the end of this file.

Some expected results depend on the "Assumptions for Ilmah" at the end of this file. Where they do, the case says which assumption.

## Case 1: a new person at a new company

- Input: one email in Leads from nora@brightwell.example. The pretend Gemini says: name Nora Fischer, company Brightwell Gyms, role Operations Manager, request type demo, urgency 2. Attio has no such person and no such company.
- Expected: a person is created with that name, email address and role.
- Expected: a company is created with the name Brightwell Gyms and the domain brightwell.example.
- Expected: a deal named "Brightwell Gyms: demo" is created at stage New, linked to the person and the company, with the owner set.
- Expected: a note is added to the deal with the summary, request type, urgency and email subject.
- Expected: the email is moved to Leads/Processed and recorded in the state file as done.

## Case 2: an existing person emails again, and their deal is at New

- Input: a second email from nora@brightwell.example. Attio already has her as a person, and she has a deal at stage New.
- Expected: no new person, no new company and no new deal.
- Expected: a note with this email's summary, request type, urgency and subject is added to her existing New deal.
- Expected: the email is moved to Leads/Processed.

## Case 3: an existing person emails again, and her deals are at other stages

- Input: a new email from a person who already exists in Attio and whose deals are all at other stages (for example "Won" or "Lost").
- Expected: no new person and no new company.
- Expected: a new deal at stage New is created, linked to her existing person and company, with the owner set, plus the note.
- Expected: her older deals are not changed.
- Expected: the email is moved to Leads/Processed.

## Case 4: two people from the same company

- Input: emails from nora@brightwell.example and omar@brightwell.example, both at Brightwell Gyms.
- Expected: one company only (found by its domain the second time), two people, and two deals, both linked to that one company.

## Case 5: a free-mail sender

- Input: a lead from rhys.baker@gmail.com. Gemini says company "Baker Yoga Studio".
- Expected: the person is created, but no company record is created, because gmail.com is a free-mail domain.
- Expected: the deal is named after the person, for example "Rhys Baker: pricing", and is linked to the person only.
- Expected (Assumption 7): the note says the company named in the email, "Baker Yoga Studio", because the company name goes in the note for free-mail senders.
- Input: the same, but Gemini gives no company.
- Expected: the note says the company was not stated.

## Case 6: the free-mail list

- Input: senders at gmail.com, googlemail.com, outlook.com, hotmail.com, live.com, yahoo.com, icloud.com, proton.me and protonmail.com, in any capitals.
- Expected: none of them gets a company record.
- Input: a sender at a business domain that only looks similar, such as gmail-fans.example or notoutlook.example.
- Expected: it is treated as a normal business domain and gets a company record.

## Case 7: a forwarded email

- Input: a lead email forwarded by a colleague. Gemini returns the original enquirer's details from the forwarded part.
- Expected: the person, company and deal are created for the original enquirer, not for the colleague who forwarded it. No person is created for the forwarder.
- Expected: the email is moved to Leads/Processed.

## Case 8: a Dutch email

- Input: a lead email written in Dutch. Gemini returns the details with an English summary.
- Expected: it is handled like any other lead. The note and the deal are in English, and the person's name and the company name are kept as written.

## Case 9: the same email twice

- Input: the same email appears twice in the Leads folder during one run.
- Expected: it is handled once only. One person, one company, one deal and one note, and Gemini is asked once.
- Input: an email that the state file says is already done is still in Leads (for example it was moved back by hand).
- Expected: it is left alone and nothing is created or noted a second time. It is reported as already handled.

## Case 10: two emails from the same new person in one run

- Input: two different emails from a person who is not yet in Attio, handled in the same run.
- Expected: the first creates the person, company and deal. The second finds the person and her deal at New, and adds a note to that deal instead of creating a second deal.

## Case 11: a missing name

- Input: a lead where Gemini gives no name.
- Expected: the person is created with the email address and no name. The script does not invent a name.
- Expected: the deal is named after the company if there is one, otherwise after the email address, for example "sam@brightwell.example: demo" (Assumption 7).

## Case 12: a missing company at a business domain

- Input: a lead from jo@maple-physio.example where Gemini gives no company.
- Expected: a company is still created from the domain, with the domain as its name (Assumption 7), and the deal is named after it.

## Case 13: an email address in capital letters

- Input: a lead from "Nora.Fischer@BRIGHTWELL.EXAMPLE". Attio already has nora.fischer@brightwell.example.
- Expected: the address is treated in small letters, the existing person is found, and no duplicate person or company is created.

## Case 14: names with accents and apostrophes

- Input: leads named "Zoë Müller", "Seán O'Brien" and "Renée D'Souza-Łukasz".
- Expected: the names are stored exactly as written in the person, the deal name and the note.
- Expected: apostrophes and accents never break a lookup or a request.

## Case 15: subdomain addresses

- Input: a lead from ana@mail.maple-physio.example, and another from ben@maple-physio.example.
- Expected: both are linked to one company with the domain maple-physio.example. The subdomain "mail." is not part of the company domain.
- Input: a lead from cai@mail.acme.co.uk.
- Expected: the company domain is acme.co.uk (Assumption 6).

## Case 16: Attio cannot be reached

- Input: no internet, a timeout or an Attio server error, part way through a live run.
- Expected: the script stops with a plain message that names the reason, for example "(the request timed out)", "(no connection)" or "(Attio answered with code 503)".
- Expected: the email being handled stays in Leads. Emails finished earlier stay finished.
- Expected: the message never contains the Attio key or any email text.

## Case 17: Attio rejects the key

- Input: ATTIO_API_KEY is wrong or expired.
- Expected: a plain message says Attio did not accept the key. The email stays in Leads and nothing is created. The key is never shown, not even in part.
- Input: Attio answers with code 401 (the key is not valid).
- Expected: the message says which answer it was, for example "Attio did not accept the key (code 401: the key is not valid)".
- Input: Attio answers with code 403 (the key is valid but has no permission for something the script needs).
- Expected: the message says so, for example "(code 403: no permission)", and tells you to check the key's permissions in Attio.
- Expected: the hint is always one of these short phrases. It never contains any text from Attio's reply, and never the key. This is the same in a dry run.
- Input: ATTIO_API_KEY is missing.
- Expected: a plain message says it can't find ATTIO_API_KEY. Nothing is changed in Outlook or Attio, and Gemini is not called.
- Expected: the same happens in a dry run, because a dry run also needs the key to look things up (Case 43).

## Case 18: Attio says "too many requests"

- Input: Attio answers "too many requests".
- Expected: the script waits briefly and tries once more. If it still fails, it stops with a plain message saying the limit was reached (Assumption 14).
- Expected: the email being handled stays in Leads. Emails finished earlier stay finished.

## Case 19: the person is created but the company step fails

- Input: the person is created, then Attio fails while creating the company.
- Expected: the script stops with a plain message. The email stays in Leads.
- Expected: no deal is created, and the person is not deleted (Assumption 2).
- Expected: on the next run the existing person is found, not created again, and the company, deal and note are created. The email is then moved. There is exactly one person.

## Case 20: the company is created but the deal fails

- Input: the person and company exist, then Attio fails while creating the deal.
- Expected: the email stays in Leads. On the next run the existing person and company are found and reused, and one deal is created.

## Case 21: the deal is created but the note fails

- Input: the deal is created, then Attio fails while adding the note.
- Expected: the email stays in Leads.
- Expected: on the next run no second deal is created. The note is added to the deal that exists, once.

## Case 22: the deal is created but the move to Processed fails

- Input: the person, company, deal and note are all created, then Outlook refuses or fails the move to Leads/Processed.
- Expected: a plain message says the lead was saved in Attio but the email could not be moved. The email stays in Leads.
- Expected: on the next run only the move is tried again. No second deal, no second note, no new person or company, and Gemini is not asked again.

## Case 23: the state file is lost part way

- Input: a run fails part way, and then the state file is missing or damaged.
- Expected: the script starts safely. It looks in Attio before creating anything: the person by email address, the company by domain, the deal by the person's New deal, and the note by the email's short reference (Assumption 3).
- Expected: no duplicates are created, and the note is added only once.
- Expected: a damaged file is set aside under a new name, not deleted.

## Case 24: a dry run reads and looks things up, but changes nothing

- Input: emails in the Leads folder. Run without --live.
- Expected: for each email it asks Gemini to read it, and it looks up the person, company and deals in Attio. It uses read-only requests only.
- Expected: it prints exactly what it would do (see Cases 41 and 42), and then stops.
- Expected: it creates or updates nothing in Attio, adds no note, moves no email, and sends no change request to Outlook.
- Expected: it does not change the state file.
- Expected: it needs the same keys and settings as a live run (Case 43, Assumption 13).

## Case 25: a live run really acts

- Input: the same emails, run with --live.
- Expected: the people, companies, deals and notes are created as in Case 1, and the emails are moved to Leads/Processed.
- Expected: the final report counts how many leads were handled, how many people, companies and deals were created, how many notes were added to existing deals, and how many emails were left in Leads and why.

## Case 26: the maximum emails per run

- Input: more emails in Leads than the maximum per run.
- Expected: only the maximum are handled, oldest first. The rest stay in Leads and the report says how many are still waiting (Assumption 12).

## Case 27: the Processed folder is missing

- Input: there is no folder called Processed inside Leads. Run with or without --live.
- Expected: a plain message says it couldn't find Leads/Processed. The script does not create folders.
- Expected: it stops at the start, before anything is created in Attio or changed in Outlook, and Gemini is not called.

## Case 28: the Leads folder is missing

- Input: there is no Leads folder. Run with or without --live.
- Expected: a plain message says it couldn't find the Leads folder. Nothing is changed and Gemini and Attio are not called.

## Case 29: a very long summary

- Input: Gemini returns a summary of many thousands of characters, and a very long company name.
- Expected: the note is still created, with the summary cut at a safe length and marked as shortened (Assumption 11). The deal name is also cut to a safe length.
- Expected: nothing fails because of length, and nothing else in the note is lost.

## Case 30: an empty or unusable email address

- Input: Gemini returns no email address, or something that is not an address, such as "n/a", "unknown", "sam at example" or an address with spaces.
- Expected: nothing is created in Attio. The email stays in Leads.
- Expected: it is reported in plain words as needing a person to look at it, and it is written in the state file so Gemini is not asked about it again on every run (Assumption 5).
- Expected: the script does not guess an address from the sender, because for a forwarded email the sender is the wrong person.

## Case 31: the owner is not set

- Input: the deal owner setting is missing or empty. Run with or without --live.
- Expected: a plain message names the setting that is missing (never any value). It stops at the start. Nothing is created in Attio, nothing is moved, and Gemini is not called.

## Case 32: Attio does not accept the owner

- Input: the owner setting is present, but Attio says there is no such workspace member.
- Expected: a plain message says Attio did not accept the deal owner and to check the setting. No deal is created and the email stays in Leads.
- Expected: a person or company created earlier in the same attempt is kept and reused on the next run (Assumption 2).

## Case 33: the owner and the keys are never shown or committed

- Input: any run, including runs that fail.
- Expected: the deal owner, ATTIO_API_KEY, GEMINI_API_KEY and the Outlook token are never printed, logged or written to the state file or the log file.
- Expected: the owner is read only from .env (which git ignores). It is not written in config.json or in any file that is committed (Assumption 10).
- Expected: .env is not changed, except that a refreshed Outlook sign-in may be written back to the MS_TOKEN_CACHE line, as the other scripts already do.

## Case 34: Gemini says it is not a lead, or cannot be trusted

- Input: Gemini says is_lead is false, or its reply is broken, or the reply is incomplete.
- Expected: nothing is created in Attio. The email stays in Leads.
- Expected: it is reported in plain words, and written in the state file as needing a person, so it is not sent to Gemini again every run (Assumption 5).

## Case 35: Gemini cannot be used

- Input: Gemini is down, the key is rejected, the key is missing, or the free limit is reached.
- Expected: the script stops with the same plain messages as the other scripts, including the reason hint.
- Expected: the email being handled stays untouched in Leads, nothing is created in Attio, and emails finished earlier stay finished.

## Case 36: an existing person only has empty fields filled

- Input: a person who already exists in Attio with a name and a role. Gemini returns a different name spelling and a different role.
- Expected: the existing name and role are not changed. Only fields that are empty are filled in (Assumption 8).
- Expected: the email address and the company link are not changed.

## Case 37: only the Leads folder itself is read

- Input: emails in the Inbox, in Leads and in Leads/Processed.
- Expected: only the emails directly in Leads are read. Inbox emails and emails in Processed are never read, sent to Gemini, or touched.

## Case 38: the stage New does not exist

- Input: the Attio Deals list has no stage called New.
- Expected: a plain message says the stage New could not be found. It stops at the start with nothing created in Attio and nothing moved in Outlook. This is the same in a dry run.

## Case 39: nothing is ever deleted, sent or replied to

- Input: a live run, including runs that fail part way.
- Expected: in Attio the script only looks things up, creates records and adds notes, and fills empty fields. It never deletes or merges anything.
- Expected: in Outlook the only change is moving an email to Leads/Processed. It never sends, replies, forwards, copies or deletes.

## Case 40: --only-subject for a safe first live test (added by me)

- Input: --only-subject "ATTIO-TEST", with two emails in Leads whose subject contains it and several that do not.
- Expected: only the two matching emails are sent to Gemini and handled. The others are not read by Gemini, not changed, not written in the state file and not moved. Capital letters do not matter.
- Input: --only-subject with empty text or only spaces.
- Expected: the script stops with a plain message that the text must not be empty, before reading or changing anything.
- Input: --only-subject "ATTIO-TEST" in a dry run.
- Expected: only the matching emails are sent to Gemini and shown. Nothing is changed.

## Case 41: a dry run shows exactly what it would do

- Input: a dry run with a new person at a new company, an existing person with a deal at New, an existing person with deals only at other stages, a free-mail sender, and a lead whose person exists but whose company does not.
- Expected: for each email it prints plain lines naming what it would do, for example:
  - would create the person (name and email address),
  - would create the company (name and domain), or "no company: free-mail domain",
  - would create the deal (its name, stage New and that an owner is set),
  - would add a note to the existing New deal,
  - would fill in empty fields on the existing person, or "nothing to change",
  - would move the email to Leads/Processed.
- Expected: it prints what it found in Attio ("person already exists", "company already exists", "deal at New found") so you can see why.
- Expected: the lines match exactly what a live run does with the same emails.
- Expected: nothing is written to Attio and no email is moved.

## Case 42: a dry run keeps track of what it would create

- Input: a dry run over two emails from the same new person, and over the same email listed twice.
- Expected: for the second email from the same new person it says it would add a note to the deal that the first email would create, not create a second deal.
- Expected: the same email listed twice is shown once.
- Expected: at the end it prints a total of what it would create and update, and a live run with the same emails produces exactly those numbers.

## Case 43: a dry run needs the same keys and settings as a live run

- Input: a dry run when GEMINI_API_KEY, ATTIO_API_KEY or the deal owner setting is missing.
- Expected: a plain message names the missing setting (never a value) and it stops before reading any email.
- Input: a dry run where Attio does not accept the key, cannot be reached, or does not know the owner.
- Expected: it stops with the same plain messages as a live run. It shows whether the owner was found (yes or no only, never any detail).
- Expected: in every case nothing is changed in Attio or Outlook.

## Case 44: a dry run respects the state file and the maximum

- Input: a dry run where some emails are already done in the state file or marked "needs a person", and there are more emails than the maximum per run.
- Expected: done and "needs a person" emails are skipped and reported, exactly as in a live run.
- Expected: the maximum per run applies, oldest first, and the report says how many are still waiting.
- Expected: the state file is never changed by a dry run.

## Case 45: the Outlook sign-in has expired, or Outlook cannot be reached

- Input: the saved Outlook sign-in no longer works. Run with or without --live.
- Expected: a plain message says to run outlook_signin.py again.
- Expected: the script stops cleanly. No email is moved or changed, nothing is created or changed in Attio, Gemini is not called, and the state file is not changed.
- Input: Outlook cannot be reached (no internet) when the script tries to read the Leads folder.
- Expected: a plain message says it couldn't reach Outlook. Every email stays exactly where it is, nothing is created in Attio, and Gemini is not called.
- Input: Outlook stops being reachable part way through a live run.
- Expected: the script stops with the same plain message. Emails finished earlier stay finished, the email being handled stays in Leads, and the next run carries on without duplicates.

## Case 46: a replay of all the other cases, checking for duplicates

- Input: the situations from the other cases are run one after another against one shared pretend Attio, one Leads folder and one state file, the way a real week would go. They include new people and companies, a second company contact, existing people with deals at New and at other stages, free-mail, forwarded, Dutch, accented names, subdomains, capital letters, a missing name and company, the same email twice, and failures at each step (company, deal, note, move, and a lost state file), each followed by a retry.
- Expected: after the whole replay, no person exists twice (by email address), no company exists twice (by domain), no deal exists twice (the same person, deal name and stage), and no note exists twice on a deal.
- Expected: every email that should be finished has been moved to Leads/Processed, and no email was moved without its Attio work being complete.

## Case 47: system and security emails are skipped (the same rule as the triage)

- Input: emails in the Leads folder that the triage rule counts as system or security emails: a sender address with noreply, no-reply, do-not-reply, donotreply, accountprotection or security before the @; a sender at microsoft.com, microsoftonline.com, anthropic.com, slack.com, github.com or google.com (or a subdomain of them); or a subject containing verification code, confirmation code, one-time, secure link, sign in, sign-in, password, security alert or new app connected as whole words.
- Expected: each one is skipped. The rule is exactly the one in the Outlook triage (Cases 31 to 33 and 40 to 42 of 04-outlook-triage.md), and both scripts use the same rule code.

## Case 48: a skipped email is left completely untouched

- Input: any skipped email, in a live run or a dry run.
- Expected: it is never sent to Gemini, so no email text leaves Outlook for it.
- Expected: nothing is looked up or created in Attio for it, no state entry is written, and it is not moved. It stays in Leads and is skipped again, in the same way, on later runs.
- Expected: it is not marked "needs a person", because it is not a problem email.

## Case 49: skipped emails are reported and counted

- Input: a run with two skipped emails and one ordinary lead, once live and once as a dry run.
- Expected: one plain line for each skipped email: "Skipped (system or security email): <subject>".
- Expected: the final report counts them, for example "System or security emails skipped: 2". The count is also shown when every email was skipped.
- Expected: in a dry run, skipped emails are not listed as emails to handle and Gemini is not asked about them.

## Case 50: real leads that only look similar are not skipped

- Input: a real lead with the subject "Design information", a real lead from a sender named "Acme Security Ltd" with a normal address, and a lead from a sender at notmicrosoft.com.
- Expected: none is skipped. Each is sent to Gemini and handled like any other lead.

## Case 51: TRIAGE-TEST emails are exempt

- Input: an email whose subject contains TRIAGE-TEST (in any capitals), even when its sender or subject would otherwise match.
- Expected: the rule is not applied to it, exactly as in the triage. It is handled like any other lead.

## Case 52: skipped emails do not use up the maximum, and --only-subject still applies first

- Input: more skipped emails than the maximum per run, plus a few ordinary leads.
- Expected: all the ordinary leads are handled. Skipped emails never count as handled or as still waiting.
- Input: --only-subject "invoice", with a skipped email whose subject contains "invoice" and another skipped email whose subject does not.
- Expected: only the first is reported as skipped. The other is ignored, like any email that does not match the text.

## Case 53: emails handled earlier are not announced again

- Input: an email already done in the state file, or marked "needs a person", whose sender would now count as a system sender.
- Expected: it is reported as already handled (or still needing a person), not as a system or security email.

## Case 54: --only-from narrows the run to one sender, and both filters must match

- Input: --only-subject "Demo request" together with --only-from jo@nautspace.example, with three emails in Leads: "Demo request" from jo@nautspace.example, "Demo request for our team" from another sender, and "Something else" from jo@nautspace.example.
- Expected: only the first email is handled. An email must match both filters.
- Expected: the other two are ignored: not sent to Gemini, not looked up in Attio, not written in the state file and not moved.
- Input: --only-from on its own (without --only-subject).
- Expected: every email from that address is handled, and no others.
- Expected: the address is compared in full and without regard to capital letters or spaces around it. "JO@Nautspace.example" matches jo@nautspace.example.

## Case 55: --only-from needs the whole address

- Input: --only-from "nautspace.example" or --only-from "jo" (only part of an address).
- Expected: nothing matches. A plain message says no lead from that address was found, and nothing is changed.
- Input: --only-from with no address, or only spaces.
- Expected: the script stops with a plain message that the address must not be empty, before reading or changing anything.

## Case 56: --only-from works in a dry run and never widens the search

- Input: a dry run with --only-subject "Demo request" --only-from jo@nautspace.example.
- Expected: it shows exactly one email, the one that matches both filters, and reports "1 lead".
- Input: --only-from with an email that is already done, or that is skipped as a system email.
- Expected: it is reported as already handled, or as a system or security email, exactly as without the option. The option only narrows the run, never widens it.

## Case 57: an email the triage already confirmed as a lead is trusted

- Input: an email in Leads whose own message ID (its Message-ID header) matches a triage record marked "Lead - Demo", "Lead - Pricing" or "Lead - Other". Gemini's reply says is_lead false, but gives a name, email address and the other details.
- Expected: Gemini's is_lead answer does not stop the email. Gemini is used only to read the name, email, company, role, request type, urgency and summary.
- Expected: the message sent to Gemini says the email was already confirmed as a lead and to fill in the details, and the output says "confirmed as a lead by the triage".
- Expected: the email is handled like any other lead: person, company, deal, note, and moved to Leads/Processed. It is not marked "needs a person".

## Case 58: emails the triage did not confirm keep the normal check

- Input: an email in Leads with no triage record, or whose triage record is "Not a lead" or "Needs review", or whose record has no message ID (an older record).
- Expected: Gemini's is_lead answer decides, exactly as before.
- Expected: the message sent to Gemini does not carry the "already confirmed" note.

## Case 59: matching uses the email's own message ID, not the Outlook folder ID

- Input: the triage recorded the email under the Outlook ID it had in the Inbox. After the triage moved it, the email in Leads has a different Outlook ID but the same Message-ID header.
- Expected: it is still matched, because the Message-ID header does not change when an email is moved.
- Expected: an email whose Outlook ID equals an old triage record but whose Message-ID header is different is not matched.

## Case 60: the triage record is only read, and trouble with it is harmless

- Input: the triage state file is missing, empty or damaged.
- Expected: no email is trusted, nothing crashes, and every email gets the normal is_lead check.
- Expected: the triage state file is never changed or created by this script, in a live run or a dry run.

## Case 61: a trusted email whose details cannot be read

- Input: a trusted email where Gemini's reply is broken, or has no usable email address (for example only {"is_lead": false}).
- Expected: nothing is created. The email stays in Leads, marked "needs a person", with the reason ("Gemini's reply could not be used" or "no usable email address").
- Input: a trusted email where Gemini gives no valid request type.
- Expected: the request type comes from the triage record (demo, pricing or other). If Gemini gives a valid request type, Gemini's wins.

## Case 62: --retry-attention asks about "needs a person" emails again

- Input: an email marked "needs a person" in the state file. A normal run.
- Expected: it is reported as "Still needs a person" and Gemini is not asked again.
- Input: the same email with --retry-attention, and Gemini now gives a usable lead.
- Expected: Gemini is asked again, the email is handled like any other lead, and its "needs a person" mark is replaced by the normal progress record.
- Input: the same, but Gemini again cannot be used.
- Expected: the email stays in Leads and stays marked "needs a person", with the new reason.
- Expected: --retry-attention never touches emails that are already done, and it works together with --only-subject and --only-from, and in a dry run (which changes no state).

## Default behaviour

- The script runs as a dry run unless it is started with --live.
- System and security emails are never sent to Gemini and are left completely untouched in Leads.
- A dry run asks Gemini and looks things up in Attio with read-only requests, prints exactly what a live run would create or update, and writes nothing to Attio, Outlook or the state file.
- It reads only the Leads folder. It uses the existing Gemini extraction (prompt v2 and the model in config.json).
- It never prints, logs or opens a key, a token or the deal owner. The state file never holds email text.
- Attio and Outlook are only changed in the ways listed in Case 39.
- The state file is git-ignored.

## Assumptions for Ilmah

I made these to be able to write the cases. Please approve or change each one.

1. **Folders.** Leads is found the same way as in the other scripts (top level first, then inside Inbox). Processed must already exist as a folder inside Leads. The script never creates folders, and checks both at the start of a live run.
2. **Nothing is deleted, nothing is rolled back.** "Nothing half-created" means every step is safe to repeat (look up first, then create), not that records are deleted. A person or company created before a failure stays, and the retry reuses it. Deleting records in Attio would be a risky action, so the script never does it.
3. **State file.** leads-to-attio-state.json, next to the script and git-ignored. Per email it keeps the progress, the lead's email address and the Attio record IDs, never the email text. That lets a retry carry on without asking Gemini again. If the file is lost, lookups in Attio plus a short reference to the email inside the note (not the email itself) prevent duplicates.
4. **Extraction.** Gemini is asked again for each email in Leads (the triage keeps no names or companies). The lead's email address is the one Gemini returns, checked for a valid format. There is no fallback to the sender's address.
5. **Extraction problems.** Not a lead, a broken or incomplete reply, or an unusable address: the email stays in Leads, nothing is created, it is reported, and it is marked "needs a person" in the state file so Gemini is not asked every 5 minutes. To retry one, you remove its entry.
6. **Matching.** Email addresses are compared in small letters. The company domain drops subdomains ("mail.company.com" becomes "company.com"). For endings with two parts such as co.uk or com.au, a small built-in list is used. Free-mail means gmail.com, googlemail.com, outlook.com, hotmail.com, live.com, yahoo.com, icloud.com, proton.me and protonmail.com, plus common country versions such as hotmail.co.uk and yahoo.co.uk.
7. **Names.** The company's name is the one Gemini gives, or the domain if Gemini gives none. The deal is named "<company record name, else the person's name, else the email address>: <request type>", with the request type as Gemini returns it (demo, pricing, partnership or other). A person without a name is created with no name.
8. **Existing person.** Only empty fields (name, role) are filled in. Nothing already there is changed or removed, and the email address and company link stay as they are.
9. **Existing deals.** If a person has more than one deal at New, the newest one gets the note. Deals at other stages are never touched.
10. **Stage and owner.** The stage New must already exist in Attio (matched by name), or the script stops at the start. The owner is a setting in .env (for example ATTIO_DEAL_OWNER, holding the workspace member's email address, which the script looks up in Attio). It is not stored in config.json or in any committed file.
11. **Notes and lengths.** The note has a title with the email subject and a short reference, then the summary, request type, urgency, the company named in the email (for free-mail senders), and the date the email was received. The summary is cut at 2,000 characters and the deal name at 150, with a mark when shortened.
12. **Maximum per run.** 10 emails, oldest first. Each email costs one Gemini call and several Attio calls, so this keeps a 5-minute schedule inside the free limits.
13. **Dry run (changed by Ilmah).** A dry run asks Gemini to read each email and looks up the person, company and deals in Attio (read only), then prints exactly what a live run would create or update. It writes nothing to Attio, moves no email and does not touch the state file. Because it needs Gemini and Attio, it needs the same keys and the owner setting as a live run, and it uses some of the free Gemini limit. Attio's own "look up" requests are technically sent as POST requests, so the tests count a request as read-only by what it asks for, not by its method.
14. **Too many requests.** Wait and retry once, then stop, as with Gemini.
15. **Attio objects.** Only the standard People, Companies and Deals objects and notes are used, with no custom fields (for example no field for the Outlook email ID). That is why the state file and the note reference are the guard against duplicates.
16. **Categories.** Moving an email to Processed does not change its Outlook categories (Lead - Demo and so on).
17. **--only-subject.** I added it (Case 40) so the first live test can be limited to one test email, the same way the triage was tested. Delete it if you don't want it.

## Case 63: a company lookup prefers the exact root domain (added after the Day 8 run)

- Why: in the Day 8 run, adding a person whose address was at freemail.example.com made Attio create a second company on its own, "Example Domain" at that subdomain. The lookup matches by root domain, so it found both companies, took the first, and split the leads across the two.
- Input: Attio holds a company at a subdomain (for example freemail.brightwell.example) and one at the exact root domain (brightwell.example), in either order, and a lead arrives from the root domain or from the subdomain.
- Expected: the company whose domain is exactly the root domain is used, whichever comes first. The person is linked to it, the deal is named after it and linked to it, and no company is created. The same holds for every lead in a run and in a dry run, and for two-part endings (mail.acme.co.uk uses acme.co.uk).
- Input: only a company at a subdomain exists.
- Expected: it is used as the fallback, and no company is created.
- Input: no company exists for the root domain at all.
- Expected: one is still created for the root domain, as before.
- Not covered by this fix: Attio may still create its own empty company at the subdomain (see "Known Attio side effect" below).

## Known Attio side effect (accepted, not fixed)

- What: when a person is created with an email address at a domain that has no company of its own (for example a lead who writes from freemail.brightwell.example when only brightwell.example has a company), Attio itself creates a company for that exact domain. It is shown as created by "system", it is empty (no people and no deals), and it is not made by this script, so the report still says "Companies created: 0".
- Seen: twice with the 30 test emails, both times with the two senders at freemail.example.com: in the full run, and again in the re-test after the Case 63 fix. Each time one extra empty company called "Example Domain" appeared.
- Effect: none on the records this script makes. After the Case 63 fix every person and every deal is linked to the company with the exact root domain, and the extra company stays empty. It is only a stray record.
- Decision (Day 8): accepted as a known Attio side effect for senders on a subdomain. The empty company is deleted by hand.
- A possible later fix, not tried: create the person already linked to the company in one request, to see whether Attio then skips its own company. That is only a guess about how Attio behaves, and it would need a live re-test.
- What to do after real subdomain senders: look for an empty company with the same name as an existing one, and delete it by hand.

## Day 8 live result: the 30 test emails through the whole pipeline (22 Oct)

How it was run: a one-off loader (day8_loader.py, cases in 14-day8-loader.md) created the 30 test emails in the Inbox with the subject tag "[DAY8]". Every step used --only-subject "[DAY8]", Slack posted to a test channel only, and both scheduled tasks stayed disabled. Order: a pilot with 2 emails, then the other 28, each step as a dry run first and then live.

| Part | Result |
|---|---|
| Triage | 29 of the 30 were handled. The 30th (a no-reply sender) was skipped and left untouched by the system-email rule, by design. |
| Categories | 23 leads (9 Demo, 8 Pricing, 6 Other) and 6 Not a lead. 28 of the 29 match the answer key. The one difference is the email "Still waiting for the quote": the triage called it Not a lead and the key says Lead - Pricing. Gemini missed it in the accuracy runs too. |
| Slack (test channel) | 23 posts, one per lead and none for non-leads. A second triage run posted nothing. Each lead email carries exactly one "posted" marker. |
| Attio people | 22 different senders: 20 new people, and 2 already existed. No duplicate person. |
| Attio companies | None created by this script. The leads were first split between two companies (found by this run, fixed by Case 63); the re-test below shows they now link to one. |
| Attio deals | 22 deals, exactly one New deal per person, all with the owner set. A sender's second email added a note to their existing deal instead of making a second one. |
| Attio notes | 23, one per lead email, each with its own reference code. |
| Outlook | All 23 handled lead emails are in Leads/Processed. The other 7 are in the Inbox (6 categorised, 1 untouched). |
| Re-test after the fix | Four emails (including both senders at freemail.example.com) were reloaded after a cleanup. Result: 4 leads, 4 Slack posts, 4 new people, 4 New deals and 4 notes, all linked to the original company at example.com, with no duplicate person, deal or note. Attio again created an empty extra company (see the known side effect above). |

What was and was not checked by hand: the Slack channel itself was checked by hand for the pilot (exactly one message). For the full run and the re-test, the posts were counted by the triage's own report and by the markers on the emails.

