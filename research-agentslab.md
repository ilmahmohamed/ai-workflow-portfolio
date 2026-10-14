# Research task: AgentsLab

I ran the same research prompt in ChatGPT, Claude, Gemini and Grok, each in a new chat. Then I checked their claims against the company website and two news articles. AgentsLab is a real, public company, so all facts here are public.

## Prompt used

Act as a business development assistant. Brief me on AgentsLab (agentslab.eu), a Dutch startup, before a call tomorrow.

Include: what the company does, who its customers are, its size and funding if known, its main competitors, and three questions I could ask on the call.

Keep it under 250 words and use clear headings.

## What I checked against

- The company website: https://www.agentslab.eu/en
- Dutch IT Channel article about the investment: https://www.dutchitchannel.nl/news/726773/oud-consultants-capgemini-halen-kapitaal-op-met-agentslab
- Vectrix news item: https://www.vectrix.nl/en/news/1433/agentslab-raises-750-000/

## What the sources say

- Builds AI "coworkers" for back-office processes, running inside the customer's ERP. Based in Amsterdam.
- Website: 20+ customers in production, 1M+ cases per year, about 8 weeks to go live.
- Founded in 2024 by Daan Kepel, Guido Rademakers and Ruben Teekens, all ex-Capgemini Invent (Dutch IT Channel).
- €750,000 investment from Impacto Group, the vehicle of Eelco Vissinga, reported in March 2026.
- The press says about 300,000 cases last year, while the website says 1M+.
- Team size and competitors are not stated on the website.

## Results

| Tool | What it did well | Problems | Verdict |
|---|---|---|---|
| Claude | Accurate, cited sources, flagged uncertainty, spotted the 300k vs 1M+ mismatch | Dealroom "Groningen" claim unchecked; competitors came from general knowledge, which it said | Best |
| ChatGPT | Careful and clear, said funding was unconfirmed | Missed the confirmed €750k funding; team of 2-10 from Dealroom not verified | Second |
| Grok | Correct on funding, founders and 1M+ cases | "~75% expand" and "team of ~7" not found in the sources; no sources given | Third |
| Gemini | Nothing invented | Generic, no customers, ERPs or funding | Fourth |

## What I learned

- A tool can state a number confidently without a source. Grok's "~75%" and "team of ~7" are examples.
- "Not found" does not mean the fact does not exist. The funding was public.
- The website alone is not enough. News articles added facts the site does not have.
- The best answers marked what was unconfirmed.

## The four answers

### ChatGPT

AgentsLab — Call Brief
Prepared for 15 October 2026 · Business development

1. What the company does
AgentsLab is a Dutch startup founded in 2024 that builds AI "coworkers" to automate repetitive administrative processes from start to finish. Its software handles tasks such as order processing, invoice checks, document classification and service tickets, integrating directly with existing ERP systems rather than requiring customers to adopt a new application. It keeps humans involved in decisions and exceptions.

2. Customers and traction
Its target market is mid-market and enterprise organisations, particularly in wholesale, construction, retail and financial services.
Named customers include Technotrading, De Mandemakers Groep, Dammers Bouw and BPM Company. The company reports 20+ customers in production and over one million cases processed annually. It says implementations take around eight weeks on average.

3. Size and funding
- Founded: 2024; based in Amsterdam, Netherlands.
- Scale: More than 20 production customers; employee headcount not verified.
- Funding: No reliable public funding total or disclosed investment round identified in the sources reviewed.

4. Main competitors
Likely competitors and alternatives include:
- UiPath: enterprise robotic process automation and AI agents.
- Microsoft Power Automate: workflow automation within Microsoft's business ecosystem.
- Rossum: AI-powered document and invoice processing.
These are adjacent competitors rather than confirmed direct rivals; AgentsLab differentiates itself through end-to-end process execution and ERP integration.

5. Questions to ask on the call
1. Growth: Which industries and customer segments are driving the strongest demand, and where do you plan to expand next?
2. Differentiation: How do you compete against established automation platforms and demonstrate measurable ROI to customers?
3. Partnerships: Are you looking for technology, implementation or channel partners to accelerate customer acquisition?

Funding note: Dealroom lists a €750k seed round in March 2026, but I couldn't independently verify the transaction. Treat this as unconfirmed. It estimates a team of 2-10 employees.

### Claude

AgentsLab Briefing

What they do
Amsterdam-based AI startup building autonomous "digital colleagues" for business processes, from invoice processing to order management. They post directly into ERPs such as SAP, AFAS, Exact, Dynamics 365 and Odoo, and claim an average of about 8 weeks to go live. Humans keep the irreversible final step.

Customers
20+ organisations in construction, wholesale, retail and finance. Named clients include ALTEC, De Mandemakers Groep, Technotrading, Dammers Bouw and BPM Company. The focus is Dutch mid-market.

Size and funding
- Raised €750,000 from Impacto Group, the vehicle of Eelco Vissinga. Founded in 2024 by three ex-Capgemini Invent consultants.
- Headcount: not found.
- Dealroom shows no funding and a Groningen HQ, which looks outdated.

Competitors
- The company's own framing: extra hires, scan-and-capture tools, and custom builds by IT partners.
- From my general knowledge (not researched): RPA vendors like UiPath, AP-automation/IDP specialists, and ERP-native AI.

Questions for the call
1. Press materials cite roughly 300,000 cases last year, while the website says 1M+ per year. What is the current run rate?
2. They are building a national partner network for IT and consultancy firms. How are margins, implementation and support split?
3. Bouw and Finance are marked "coming soon". What is the roadmap, and how does pricing scale beyond one process?

Sources: agentslab.eu, Emerce, Dutch IT Channel, Vectrix, Dealroom

### Gemini

What the Company Does
AgentsLab (agentslab.eu) is an Amsterdam-based startup that provides a platform for digital colleagues and custom AI agents. It helps organizations automate internal workflows and operational processes by deploying task-specific AI agents.

Customers
Their target audience primarily consists of mid-market and enterprise businesses looking to integrate AI-driven automation into their daily operations to handle repetitive tasks and streamline processes.

Size and Funding
Specific details regarding headcount size and public venture funding rounds are limited, as it operates as an early-stage private startup registered in Amsterdam.

Main Competitors
Competitors in the broader enterprise AI agent and workflow automation space include specialized AI integration agencies, custom automation software shops, and platforms providing customizable enterprise agent deployment tools (such as Salesforce Agentforce ecosystem tools and various European enterprise AI solution providers).

Call Questions
1. "How do you typically scope and integrate your agents with a client's legacy tech stack during the initial deployment phase?"
2. "What does your security and data privacy framework look like when handling sensitive enterprise or customer data?"
3. "What are the most common operational bottlenecks you see companies facing before they bring in your digital agents?"

### Grok

What they do
AgentsLab builds "AI colleagues" that run end-to-end back-office processes (order entry, invoice processing/matching, service tickets, candidate screening) directly inside the customer's existing ERP. Deep native integrations with Exact (Online/Globe), AFAS, SAP, Dynamics 365, Odoo, Bouwworks, 4PS and similar. Average go-live ~8 weeks; humans keep decision rights and handle exceptions. Positions as anti-hype, results-focused.

Customers
Dutch mid-market and larger organisations (roughly 50-2,000 employees) in construction, wholesale/distribution, retail and finance. 20+ live customers processing 1M+ cases per year; ~75% expand to additional processes after the first.

Size & funding
Amsterdam-based, founded 2024 by three ex-Capgemini Invent consultants (Daan Kepel, Guido Rademakers, Ruben Teekens). Team of ~7 (2-10 range). Raised €750k seed from Impacto Group (Eelco Vissinga) in early 2026.

Main competitors
Generic chatbots/copilots, pure OCR/scan-and-recognise tools, custom RPA or IT-partner builds, and other AI automation players (e.g. Hypatos for invoices; broader European agent platforms). Differentiates on deep Dutch mid-market ERP fit and measurable process ownership rather than assistants.

Three questions for the call
1. Which processes drive the highest volume or friction for your ideal customers right now, and how do you prioritise the first AI colleague?
2. How is the partner network (ERP consultants/implementers) evolving, and what does a successful joint engagement look like?
3. With the recent funding and 1M+ cases run-rate, what are the top product or go-to-market priorities for the next 12 months?
