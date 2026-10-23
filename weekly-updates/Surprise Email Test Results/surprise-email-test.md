# Surprise email test (Day 10)

Practice company: Nova Wellness (fictional). Test run on Fri 23 Oct 2026. Times are Sri Lanka time (UTC+5:30).

## What happened

A test email I had not prepared arrived in the Nova Wellness mailbox from Jomiene at Naut Space, with the subject **"Interest in newest product"**. It asked for prices for a product she had seen in an advert, and it contained a typo ("neqest") and an email signature with an image and links. I did nothing by hand. The pipeline handled it on its own.

| Step | Result | Time |
|---|---|---|
| Email arrived in Outlook | Received | 4:29 PM |
| Slack #leads alert | Posted: "Lead - Pricing", sender, subject and a preview | 4:29 PM |
| Outlook | Labelled **Lead - Pricing**, moved to Leads/Processed, reply draft saved. The draft says "This message hasn't been sent". | 4:29 PM |
| Attio | Note "Lead email: Interest in newest product" added to the existing deal "Naut Space: demo", with the summary "Inquiry about pricing for the newest product." | 4:29 PM |

From arrival to the Attio note took **under one minute**, with **no hands-on work**.

## What I checked

- **Right category.** Gemini read through the typo and labelled it Lead - Pricing.
- **No duplicate.** The sender already existed in Attio, so the pipeline added a note to her existing person and deal. No second person, company or deal was created.
- **Draft only.** The reply draft was saved in Outlook and nothing was sent.
- **Company.** Naut Space already existed from an earlier test, so the deal stayed linked to it.
- **Where the note appears.** It is on the deal ("Naut Space: demo" then the Notes tab), not on the person's page.

## Notes

- A fair amount of the email was a signature (image, phone number, links). The preview in Slack is cut to 200 characters, but it can still include part of a signature.
- The draft reply is short and generic: "Hello Jomiene. Thank you for getting in touch about your pricing request. A member of our team will be in touch."

## Screenshots

- Slack #leads alert
- Outlook, Processed folder, showing the label and the draft
- Attio, the "Naut Space: demo" deal with the new note
