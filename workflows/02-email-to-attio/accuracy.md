# Accuracy by prompt version

One row per live run. Accuracy = correct fields divided by all scored fields.

Key correction, 20 Oct: after the first live run (prompt v1, 80.6% against the old key), emails 22 and 24 were changed from lead to not a lead, because they broke the rules in expected-rules.md. The v1 score below was measured against the old key.

6 fresh emails the prompt had never seen. One borderline urgency call on email 205 (key 2, Gemini 1). Key left unchanged.

| Date | Prompt | Model | Accuracy | Broken replies | Per field |
|---|---|---|---|---|---|
| 2026-10-20 | v1 | gemini-3.5-flash-lite | 80.6% (150/186) | 0 | is_lead 26/30; name 19/26; email 22/26; company 22/26; role 21/26; request_type 22/26; urgency 18/26 |
| 2026-10-20 | v2 | gemini-3.5-flash-lite | 96.0% (167/174) | 0 | is_lead 29/30; name 23/24; email 23/24; company 23/24; role 23/24; request_type 23/24; urgency 23/24 |
| 2026-10-20 | v2 [own-emails] | gemini-3.5-flash-lite | 96.7% (29/30) | 0 | is_lead 6/6; name 4/4; email 4/4; company 4/4; role 4/4; request_type 4/4; urgency 3/4 |
| 2026-10-22 | v3 first prompt | gemini-3.5-flash-lite | 92.0% (160/174) | 0 | is_lead 28/30; name 22/24; email 22/24; company 22/24; role 22/24; request_type 22/24; urgency 22/24; category 28/30; draft violations 0 |
| 2026-10-22 | v3 first prompt [own-emails] | gemini-3.5-flash-lite | 96.7% (29/30) | 0 | is_lead 6/6; name 4/4; email 4/4; company 4/4; role 4/4; request_type 4/4; urgency 3/4; category 6/6; draft violations 0 |
| 2026-10-22 | v3 + tweak, REVERTED [own-emails] | gemini-3.5-flash-lite | 93.3% (28/30) | 0 | is_lead 6/6; name 4/4; email 4/4; company 4/4; role 4/4; request_type 3/4; urgency 3/4; category 5/6; draft violations 0 |
| 2026-10-22 | v3a [setA-emails] | gemini-3.5-flash-lite | 98.3% (57/58) | 0 | is_lead 10/10; name 8/8; email 8/8; company 8/8; role 8/8; request_type 8/8; urgency 7/8; category 10/10; draft violations 0 |
| 2026-10-22 | v3b [setB-emails] | gemini-3.5-flash-lite | 87.7% (57/65) | 0 | is_lead 10/11; name 8/9; email 8/9; company 8/9; role 8/9; request_type 8/9; urgency 7/9; category 10/11; draft violations 2 |
| 2026-10-22 | v3c [setB-emails] | gemini-3.5-flash-lite | 100.0% (65/65) | 0 | is_lead 11/11; name 9/9; email 9/9; company 9/9; role 9/9; request_type 9/9; urgency 9/9; category 11/11; draft violations 4 |
| 2026-10-22 | v3 | gemini-3.5-flash-lite | 95.4% (166/174) | 0 | is_lead 29/30; name 23/24; email 23/24; company 23/24; role 22/24; request_type 23/24; urgency 23/24; category 29/30; draft violations 1 (0 when the saved replies were re-checked after allowing "we have received your request"; no new Gemini calls) |
| 2026-10-22 | v3 [own-emails] | gemini-3.5-flash-lite | 100.0% (30/30) | 0 | is_lead 6/6; name 4/4; email 4/4; company 4/4; role 4/4; request_type 4/4; urgency 4/4; category 6/6; draft violations 0 |
