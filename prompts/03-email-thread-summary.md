# Email thread summary

## Prompt
```
Act as an executive assistant at Nova Wellness. Summarise this fictional email thread using only the information provided. Answer in this chat without accessing email or other apps.

Monday, Manager: Can we arrange a workshop on 23 October for 20 staff? Ilmah, please check with Bright Events.

Monday, Bright Events: We are available on 23 October. The quotation is LKR 120,000, excluding the venue.

Monday, HR: Several staff can't attend on 23 October. Could we consider 27 October instead? The updated headcount is 22.

Tuesday, Bright Events: We can offer 27 October, 10 am–noon, for 22 people. The revised quotation is LKR 132,000, excluding the venue.

Tuesday, Manager: The new date works for me, but please don't confirm the booking until Finance approves the budget.

Tuesday, Ilmah: I've asked Finance to confirm approval by Wednesday at noon. We still need to decide on a venue.

Give me:

1. A short summary of the latest position.
2. Confirmed decisions.
3. Pending actions in a table showing action, owner and deadline.

Keep it under 200 words. Don't present the booking or budget as approved. Where an owner or deadline isn't stated, write "Not specified."
```

## Results
- **ChatGPT:** Kept to the three requested parts and wrote "Not specified" where an owner or deadline was missing.
- **Claude:** Accurate, but added an extra section ("Not approved or not decided") that I didn't ask for.
- **Gemini:** Named Ilmah as the owner of confirming the booking and said Bright Events was "selected". Neither is in the thread.

## My choice
**Preferred tool:** ChatGPT

**Reason:** Kept to the three parts I asked for and wrote "Not specified" for the missing owners and deadlines. Gemini broke that rule by naming Ilmah as the owner of the booking, and it listed Bright Events as "selected", which the thread never says. Claude added an extra section I didn't ask for.
