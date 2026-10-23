# Reading other automations: one Zap and one Make scenario (Day 10)

Practice company: Nova Wellness (fictional). Reviewed on Fri 23 Oct 2026.

## 1. Zapier: "New Outlook email in Leads → Slack #leads" (my own Day 5 Zap)

| Step | App and action | What it does |
|---|---|---|
| 1. Trigger | Microsoft Outlook, "New Email in Personal Inbox" | Watches the Nova Wellness mailbox, set to the **Leads** folder. It only fires for emails my Outlook rule has already put there. |
| 2. Action | Slack, "Send Channel Message" | Posts to **#leads**. The text is built from the email's fields: Subject, From name, From address and Body Preview. |

Other settings: it posts as a bot called "Zapier" with a link back to the automation. Schedule, File and Thread are empty. "Send Channel Message?" is now set to **No**, so this Zap no longer posts; the Python pipeline sends the alert instead.

**Weaknesses:** there is no AI, so every email in Leads gets a message, including newsletters the Outlook rule filed by mistake. There is no failure alert, and nothing happens in Attio. On the free plan it checks about every 15 minutes.

## 2. Make: "Monitor Microsoft email messages and create draft emails with ChatGPT" (public template)

| Module | App and action | What it does |
|---|---|---|
| 1. Trigger | Microsoft 365 Email (Outlook), watching for new emails | Starts the scenario when a new email arrives. (The exact action name was cut off in the preview.) |
| 2. Middle | OpenAI (ChatGPT), "Generate a completion" | Sends the email text to ChatGPT and gets a written reply back. |
| 3. Action | Microsoft 365 Email (Outlook), "Create a Draft Email" | Saves that reply as a draft in Outlook. |

**Weaknesses:**
- The template's description says it will "send replies", but the third module only creates a draft. The wording and the behaviour don't match, so a reader could assume it sends.
- It replies to **every** new email. There is no step to skip newsletters, auto-replies or security notices, so it would spend AI credits on them and draft replies nobody needs.
- It has no error handling and no alert if ChatGPT or Outlook fails.

## How they compare with my pipeline

| | Zap | Make template | My pipeline |
|---|---|---|---|
| Starts when | A new email lands in Leads | Any new email | Every 5 to 40 minutes (GitHub Actions) |
| Uses AI | No | Yes, one call for the reply | Yes, one Gemini call that classifies, extracts and drafts |
| Skips non-leads | No | No | Yes |
| Saves a draft, never sends | No | Yes (but the description says send) | Yes |
| Creates Attio records | No | No | Yes (person, deal, note) |
| Alerts on failure | No | No | Yes, to #automation-log |

**What I'd take from them:** the Make template shows how simple a reply-draft flow can be (three modules), but my pipeline adds the parts a client would need: filtering, de-duplication, a CRM record, and a failure alert.
