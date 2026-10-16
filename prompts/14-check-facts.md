# Catch the AI making things up

## Prompt
```
Tell me about [company or topic].

After your answer, add a section called "Check this" that:
- Lists each factual claim as a short bullet
- Marks each as Sure, Not sure, or Guessing
- Says where I could verify it (for example, the company website)

If you do not know something, say "I don't know". Do not invent details.
```

## Results
- **ChatGPT:** Used a fictional company and marked everything "Not sure", so the prompt wasn't really tested.
- **Claude:** Used a real company (Zapier), labelled 8 claims (Sure, Not sure, Guessing) and said "I don't know" where needed.
- **Gemini:** Marked nearly everything "Sure" and got a detail wrong (it described AlphaFold as a diagnostic tool). Overconfident.

## My choice
**Preferred tool:** Claude

**Reason:** Its labels were honest and varied, and I could check them against a real website.
