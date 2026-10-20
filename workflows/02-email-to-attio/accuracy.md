# Accuracy by prompt version

One row per live run. Accuracy = correct fields divided by all scored fields.

Key correction, 20 Oct: after the first live run (prompt v1, 80.6% against the old key), emails 22 and 24 were changed from lead to not a lead, because they broke the rules in expected-rules.md. The v1 score below was measured against the old key.

6 fresh emails the prompt had never seen. One borderline urgency call on email 205 (key 2, Gemini 1). Key left unchanged.

| Date | Prompt | Model | Accuracy | Broken replies | Per field |
|---|---|---|---|---|---|
| 2026-10-20 | v1 | gemini-3.5-flash-lite | 80.6% (150/186) | 0 | is_lead 26/30; name 19/26; email 22/26; company 22/26; role 21/26; request_type 22/26; urgency 18/26 |
| 2026-10-20 | v2 | gemini-3.5-flash-lite | 96.0% (167/174) | 0 | is_lead 29/30; name 23/24; email 23/24; company 23/24; role 23/24; request_type 23/24; urgency 23/24 |
| 2026-10-20 | v2 [own-emails] | gemini-3.5-flash-lite | 96.7% (29/30) | 0 | is_lead 6/6; name 4/4; email 4/4; company 4/4; role 4/4; request_type 4/4; urgency 3/4 |
