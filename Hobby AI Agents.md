# Hobby AI agents

## Summary

A **hobby AI agent** is an AI helper that an ordinary person builds in their spare time, for their own use or for fun. A big company did not make it, and it is not a finished product for sale. It is usually put together from ready-made tools, and it often lives inside a chat app the person already uses, such as Telegram.

These small agents can help in practical ways. They take over the first pass of everyday jobs, such as sorting email, writing a draft reply, setting a reminder or planning the day. The person stays in charge and decides what goes out. They are also a good way to see what is possible without a big team or budget, and they show what works and what goes wrong before you build your own.

## Examples of hobby AI agents

| Agent | Built by | What it does | How hard it was to build |
| --- | --- | --- | --- |
| **Email sorter and drafter** | A retired writer, on Medium | Reads email, sorts it, writes draft replies and flags anything it is unsure about. It cannot send anything without the person's approval. | Easy. About three hours to set up with ready-made tools. |
| **Telegram assistant for admin tasks** | Daria Cupareanu, on Substack | The person sends one text or voice message. It sets calendar events, reads and drafts emails, searches the web and saves ideas to a notes app. | Easy to medium. Built with ready-made automation steps and no heavy coding. |
| **Telegram personal secretary** | A tutorial from Bult.ai, on DEV Community | Answers questions like "what is on my calendar today?" by choosing whether to check email, tasks, contacts or the calendar. | Easy, though connecting to Google takes some patience. |
| **"Tiny Jarvis" for project updates** | Pavlo Chorniy, on DEV Community | A chat bot that answers questions about his projects using his notes, task boards and code history. | Early and rough. He says the first version will be basic. |
| **Daily planner** | Ivo Bernardo, on Substack | Pulls his calendar, to-do list and yearly goals into one plan for the day, working around fixed meetings and breaks. | Medium. Custom-built, and it runs only on his own computer. |

## How people use them in their daily work

- **They take over the first pass.** The agent sorts, drafts or plans. The person makes the final decision.
- **They sit inside apps people already use,** like Telegram, email and a calendar, so there is nothing new to learn.
- **The people with good results kept checking.** One writer's email helper sorted about 94% of 847 emails correctly in a week, but the author still reviewed every draft and kept emails from people they cared about out of the automation.
- **Honest write-ups talk about mistakes as well as savings.** For example, one draft promised a delivery time the author had never agreed to. It was caught only because the author reviewed it before sending.

## The one I found most interesting: the Telegram assistant for admin tasks

I chose this one because it is the closest to my own project.

1. **It works the way my assistant for Andrew would.** You send one text or voice message, and the agent turns it into the right action: an email, a calendar event or a saved note.
2. **It started from a problem I know well.** Small admin jobs were scattered across several apps, so she wanted one chat to handle them. That is what an executive assistant deals with every day.
3. **It is simple to build.** It uses ready-made automation steps, an AI model and apps she already used. That shows my idea is realistic with the free trial tools in my plan.

## What this means for my project

My project is a WhatsApp assistant for Andrew that turns a text or voice message into an email or Slack message.

- **The shape is realistic.** The Telegram assistants show that a chat message in, an AI deciding, and tools doing the work is something one person can build.
- **Start small.** The "tiny Jarvis" builder says plainly that his first version will be rough. A basic version that works is better than a big plan.
- **Expect quick, casual messages.** Andrew will send a text or voice note without thinking too much about the wording, so the assistant has to cope with loose, half-finished requests. My plan sends straight away when there is one clear match, asks when a name is unclear, and saves a draft when the message starts with "Draft". The email helper's near miss shows why checking matters when a message is rushed. I will test this on the free trial and measure how often the assistant gets it wrong.
- **Keep the limits.** Approved contacts only, only Andrew's number accepted, a one-word off switch and no customer health data.
