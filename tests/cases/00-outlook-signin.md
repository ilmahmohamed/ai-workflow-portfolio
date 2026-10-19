# Test cases: one-time Outlook sign-in

Written before any code. Each case says what goes in and what should happen.
No real keys or tokens are written in this file.

## Case 1: sign-in works and shows only my name

- Input: MS_CLIENT_ID is set correctly. I have not signed in before. I run the sign-in script.
- Expected: my browser opens on the Microsoft sign-in page. I log in and click "Yes".
- Expected: the terminal shows a short message like "Signed in as <my name or email>. Your sign-in has been saved."
- Expected: no token, key or password is ever shown on screen.
- Expected: .env gets one new line called MS_TOKEN_CACHE. Every older line is unchanged.
- Expected: .env does not show up in git status.

## Case 2: the client ID is missing

- Input: MS_CLIENT_ID is missing or empty. I run the sign-in script.
- Expected: the script stops straight away and no browser opens.
- Expected: a clear message in plain words, like "I can't find MS_CLIENT_ID. Add it to your .env file and try again."
- Expected: no technical error text, and .env is not changed.
