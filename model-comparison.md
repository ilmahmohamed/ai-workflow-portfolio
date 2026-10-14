# Model comparison

I ran six tasks across four AI tools (ChatGPT, Claude, Gemini, Grok): five executive assistant tasks for the fictional company Nova Wellness, and one research task on a real Dutch startup, AgentsLab. Each tool got the same wording in a new chat. The Nova Wellness data is fake. The AgentsLab facts are public.

| Task | ChatGPT | Claude | Gemini | Grok | Verdict |
|---|---|---|---|---|---|
| Inbox triage | Added an unrequested "Recommended approach" paragraph | Added an Order column and a budget note from outside the prompt | Used exactly the five columns asked for | Used exactly the five columns, with shorter reasons | Gemini (Grok ties) |
| Meeting prep brief | No major issues | Split "Confirmed" from "Proposed", agenda adds up to 30 minutes, right section order | Put "Information still to confirm" before "Decisions needed" | Right order, but did not separate confirmed facts from proposals | Claude |
| Email thread summary | Kept the three parts and wrote "Not specified" for missing owners and deadlines | Added an extra section I did not ask for | Named Ilmah as owner and said Bright Events was "selected", neither of which is in the thread | Vague owner ("Ilmah / Finance") and left out the confirm-booking action | ChatGPT |
| Slack update | Added a promise to share another update that was not in my notes | Stuck to my notes and kept the four sections | No major issues, close | Stuck to my notes but had no greeting | Claude |
| Follow-up email | Left out the subject line I asked for | Subject line, said the booking is not confirmed and why, asked for the hold and two venue options, under 120 words | No major issues | Subject line and every point, close | Claude |
| Research brief on a Dutch startup (AgentsLab) | Careful and clear, but missed the confirmed €750k funding and relied on an unverified team size | Accurate, cited sources, flagged uncertainty, spotted a 300k vs 1M+ cases mismatch | Generic, no customers or funding, nothing invented | Detailed, but included numbers I could not verify (~75% expand, team of ~7) | Claude |

## What I learned about catching made-up facts

- Grok stated a few numbers as fact that I could not find on the AgentsLab website or in the news articles I checked.
- Gemini made up details in the thread summary, and was vague and generic in the research task.
- Some tools said "not found" even though the information was public, so a missing fact is not proof it does not exist.
- Claude and ChatGPT were more careful about marking what was unconfirmed.

## When I would use each tool

These are based on my small set of tests, so they may change as I test more.

- **ChatGPT:** Strict, structured summaries where missing information must be marked "Not specified". I check for extra sections and promises it adds.
- **Claude:** Clear writing for emails, briefs and Slack updates, and research where I want sources and uncertainty flagged. I tell it to use only the information given, because it once added outside information.
- **Gemini:** Quick tables that need exact columns. I check for invented details, and it is thin on research.
- **Grok:** Quick, concise answers that follow the format. I verify any numbers it gives, and I check usage limits on the free plan.
