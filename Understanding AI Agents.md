# Understanding AI Agents

This note explains what AI agents are, how they differ from the AI tools I have used so far, and how they work and can be built. I have written it in plain language, using examples from my own work as an executive assistant and from the email pipeline I built in Week 2.

## What is an AI agent?

An AI agent is an AI system that is given a goal and works out the steps to reach it by itself. To do this, it can use tools, such as email, Slack, a calendar or a database.

Think of how you would brief a good assistant. You do not say "open this email, copy this name, paste it here." You say "make sure the marketing team knows the launch has moved to March." The assistant works out who the marketing team is, what to write and how to send it. An AI agent is built to work in a similar way.

OpenAI describes agents as "systems that independently accomplish tasks on your behalf." The important word is *independently*: the AI chooses what to do next, rather than following a script someone wrote in advance.

## How agents differ from the AI tools I have used so far

So far I have used AI in two ways. The third way is an agent.

| | Who decides the steps? | Example from my work |
| --- | --- | --- |
| **AI tool** (ChatGPT, Claude, Gemini) | I do. I ask one question, it answers, and I decide what happens next. | Asking Claude to draft a reply to an email |
| **Workflow** (automation) | The person who built it. The steps are fixed and always run in the same order. | My email-to-Attio pipeline: read the email, add it to Attio, post to Slack, save a draft |
| **Agent** | The AI, within the limits it has been given. | An assistant that reads a loose message and decides who needs it and what to write |

The biggest difference is **who is in charge of the next step**. With a tool, it is me. With a workflow, it is the builder. With an agent, it is the AI.

A workflow is not worse than an agent. Anthropic, the company that makes Claude, advises starting with the simplest solution and only using an agent when the job needs that flexibility.

## How an agent works

An agent works in a loop. It repeats the same cycle until the job is done, or until it needs help from a person:

1. **Look at the goal.** What am I trying to achieve?
2. **Choose a tool.** For example, look up a contact or draft an email.
3. **Use it and read the result.** What did the tool actually return?
4. **Decide what to do next.** Carry on, ask a person, or stop because the job is done.

**A simple example.** A manager sends this message: "Tell the marketing team the launch is moving to March, and copy Ilmah."

- The agent works out this is a marketing message and that Ilmah must be copied.
- It looks up the marketing team and Ilmah's address in a contact list.
- If one name is not on the list, it asks the manager instead of guessing.
- It writes a draft email and a Slack message, and shows them to the manager.
- Only when the manager approves does anything get sent.

Checking the real result at each step is important. It stops the agent from simply guessing.

## What an agent is made of

OpenAI says an agent needs three core parts: a model, tools and instructions. I would add two more that matter in practice.

| Part | What it is | Everyday example |
| --- | --- | --- |
| **Model** | The AI "brain" that reads and decides | Claude or Gemini |
| **Tools** | The things the agent is allowed to use | Search email, post to Slack, read a contact list |
| **Instructions** | Its job and its rules, written in plain language | "Draft first. Never send without approval." |
| **Memory** | What it remembers about the task so far | "I already asked about Alejandro's address" |
| **Limits (guardrails)** | Checks that stop it doing the wrong thing | A list of approved contacts, and a person who approves before sending |

A standard called **MCP** (Model Context Protocol) is one way to connect an AI to tools. IBM compares it to a USB-C port: one common plug, instead of a different cable for every tool.

## How an agent can be made

These are the steps I would follow, based on what I read from Anthropic and OpenAI:

1. **Pick one clear goal.** A narrow job is easier to get right than a big one.
2. **Try the simple version first.** A single AI call or a fixed workflow may be enough.
3. **Give it only the tools it needs.** The fewer tools, the fewer ways to go wrong.
4. **Describe each tool clearly,** with examples, as if explaining it to a new colleague.
5. **Write the instructions and the limits.** Say what it should do, and what it must never do.
6. **Add human approval** for anything sensitive or hard to undo, such as sending an email.
7. **Test it on real cases,** including cases where things go wrong.
8. **Keep a log and an off switch,** so you can see what it did and stop it quickly.
9. **Start with a person approving every action.** Give it more freedom only as trust grows.

## When an agent is the right choice, and the risks

**When to use one.** OpenAI suggests agents suit jobs where fixed rules struggle: decisions that need judgement, many exceptions, or lots of unstructured information such as emails and documents. When the steps are known and always the same, a workflow is simpler, cheaper and more predictable.

**The risks.** An agent can take real actions, so mistakes have real effects.

- **Small mistakes can grow.** An early error can carry through many steps.
- **More cost and delay.** More steps mean more AI calls.
- **Hidden instructions.** Text the agent reads, such as an email, can contain instructions meant to trick it. This is called *prompt injection*. Security researchers have described a real case in Microsoft 365 Copilot where a crafted email led the assistant to send out internal files.
- **Too much trust.** People stop checking because it usually works.

**How the risks are managed.** No single protection is enough, so builders use several together: give the agent the minimum access, have a person approve risky actions, limit it to approved contacts, make it stop and ask for help when it keeps failing, keep a log, and have a way to switch it off.

## How this connects to my own work

- **My Week 2 pipeline is a workflow.** The steps never change. Gemini only reads each email and writes a draft. It follows the safety ideas above: drafts are saved and never sent, failures post to a Slack alert channel, and one setting switches it off.
- **As an executive assistant,** I could read an inbox and calendar but not act without authorisation. A well-built agent works the same way: capable, but with clear limits.

## Examples of AI agents, and what I have used

Looking back at my training so far, most of what I built was workflows, but I have used at least one real agent.

| Example | Agent or not? | Why |
| --- | --- | --- |
| **Claude Code**, which helped me build the pipeline | **Agent** | I gave it a goal. It read my project files, wrote code, ran the tests, read the results and decided what to fix next. I kept it in manual mode, so I approved its actions. |
| **Claude in a chat with tools** (searching the web, reading pages, writing a document) | **Agent-like** | It chose which sources to open and what to write next, based on what it found. |
| **My Zapier Zap** (new email to Slack) | Not an agent | It follows one fixed rule every time. |
| **The Make template** (new email, ChatGPT, draft reply) | Not an agent | Three fixed steps, and the AI only writes text. |
| **My email-to-Attio pipeline** | Not an agent | The order of the steps never changes. Gemini does one job inside them. |

Anthropic's article gives other well-known kinds of agent:

- **A coding agent** that works through software tasks on its own.
- **A customer support agent** that looks up a customer's details, answers questions and can take actions such as issuing refunds or updating tickets.
- **A computer-use agent** that operates a computer, looking at the screen and clicking and typing.

What these have in common is that the AI chooses what to do next, and checks the result before carrying on.
