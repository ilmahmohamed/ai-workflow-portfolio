"""One-time Outlook sign-in for the Nova Wellness practice inbox.

Opens a Microsoft sign-in page in the browser, then saves a "remember me"
token as one line (MS_TOKEN_CACHE) in the .env file. The token is never
printed and never written anywhere else.

Run:  python workflows\\01-outlook-to-slack\\outlook_signin.py
Needs: MS_CLIENT_ID (in .env or set in the terminal) and the msal library.
"""

import base64
import json
import os
import sys
from pathlib import Path

# .env sits in the project root, two folders above this file.
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Personal Microsoft accounts only. msal adds "offline_access" by itself,
# which is what makes the sign-in last.
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Mail.ReadWrite"]
CACHE_NAME = "MS_TOKEN_CACHE"
BROWSER_WAIT_SECONDS = 300


class SetupProblem(Exception):
    """A problem we can explain to the user in plain words."""


def read_env_text():
    """Return (text, has_bom) for .env, or ("", False) if there is no .env."""
    if not ENV_PATH.exists():
        return "", False
    raw = ENV_PATH.read_bytes()
    try:
        return raw.decode("utf-8-sig"), raw.startswith(b"\xef\xbb\xbf")
    except UnicodeDecodeError:
        raise SetupProblem(
            "I couldn't read your .env file (it isn't saved as plain text). "
            "Nothing was changed."
        )


def read_env_value(text, name):
    """Find NAME=value in the .env text. Returns "" if not there."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("'\"")
    return ""


def get_client_id(env_text):
    """The environment wins; otherwise use the .env file."""
    client_id = os.environ.get("MS_CLIENT_ID", "").strip()
    if not client_id:
        client_id = read_env_value(env_text, "MS_CLIENT_ID")
    if not client_id:
        raise SetupProblem(
            "I can't find MS_CLIENT_ID. Add it to your .env file and try again."
        )
    return client_id


def save_cache_line(env_text, has_bom, value):
    """Add or replace only the MS_TOKEN_CACHE line. Every other line is kept."""
    newline = "\r\n" if "\r\n" in env_text else "\n"
    new_line = f"{CACHE_NAME}={value}{newline}"
    lines = env_text.splitlines(keepends=True)

    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{CACHE_NAME}="):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(new_line)

    data = "".join(lines).encode("utf-8")
    if has_bom:
        data = b"\xef\xbb\xbf" + data

    # Write a full copy first, then swap it in, so a crash can't leave
    # .env half-written.
    temp_path = ENV_PATH.with_name(ENV_PATH.name + ".tmp")
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, ENV_PATH)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def sign_in(client_id):
    """Open the browser, let the user log in, return (who, cache_value)."""
    try:
        import msal
    except ImportError:
        raise SetupProblem(
            "The sign-in helper (msal) isn't installed yet. "
            "Install it and try again. Nothing was changed."
        )

    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        client_id, authority=AUTHORITY, token_cache=cache
    )

    print("Opening your browser. Please log in and click Yes to allow access.")
    result = app.acquire_token_interactive(
        SCOPES, prompt="select_account", timeout=BROWSER_WAIT_SECONDS
    )

    if "access_token" not in result:
        # Show only the short error label, never the full response.
        reason = result.get("error", "unknown")
        raise SetupProblem(
            "Sign-in didn't finish, so nothing was saved "
            f"(reason: {reason}). Please try again."
        )

    claims = result.get("id_token_claims") or {}
    who = claims.get("name") or claims.get("preferred_username") or "your account"

    # One line, no special characters, so .env stays tidy.
    compact = json.dumps(json.loads(cache.serialize()), separators=(",", ":"))
    cache_value = base64.urlsafe_b64encode(compact.encode("utf-8")).decode("ascii")
    return who, cache_value


def main():
    # Stop odd characters in a name from crashing the printout.
    sys.stdout.reconfigure(errors="replace")
    try:
        env_text, has_bom = read_env_text()
        client_id = get_client_id(env_text)
        who, cache_value = sign_in(client_id)
        save_cache_line(env_text, has_bom, cache_value)
    except SetupProblem as problem:
        print(problem)
        return 1
    except Exception as unexpected:
        # Only the kind of error is shown, in case details contain secrets.
        print(
            "Something unexpected went wrong "
            f"({type(unexpected).__name__}). Your .env was not changed."
        )
        return 1

    print(f"Signed in as {who}. Your sign-in has been saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
