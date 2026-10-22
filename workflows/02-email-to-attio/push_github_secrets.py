"""Copy the pipeline's secrets from .env into GitHub Actions secrets.

Each value goes straight from .env to GitHub through gh's standard input. No value is ever
printed, logged or put on a command line. Only the names are shown.

Run:  python workflows\\02-email-to-attio\\push_github_secrets.py --dry-run   (lists names only, changes nothing)
      python workflows\\02-email-to-attio\\push_github_secrets.py             (copies them for real)
Needs: the GitHub CLI (gh), signed in with permission to write secrets on the repo.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# .env sits in the project root, two folders above this file.
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

REPO = "ilmahmohamed/ai-workflow-portfolio"

# The GitHub secret names are the same as the .env names.
# MS_TOKEN_CACHE is the Outlook sign-in. SLACK_LOG_WEBHOOK_URL is the #automation-log link.
SECRET_NAMES = (
    "GEMINI_API_KEY",
    "MS_CLIENT_ID",
    "MS_TOKEN_CACHE",
    "SLACK_WEBHOOK_URL",
    "SLACK_LOG_WEBHOOK_URL",
    "ATTIO_API_KEY",
    "ATTIO_DEAL_OWNER",
)

GH_FALLBACK = Path("C:/Program Files/GitHub CLI/gh.exe")


class SetupProblem(Exception):
    """A problem we can explain to the user in plain words."""


def read_env_values(path=ENV_PATH):
    """Return {name: value} from the .env file. Returns {} if there is no .env."""
    if not path.exists():
        return {}
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SetupProblem("I couldn't read your .env file (it isn't saved as plain text). Nothing was changed.")
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def find_gh():
    found = shutil.which("gh")
    if found:
        return found
    if GH_FALLBACK.exists():
        return str(GH_FALLBACK)
    raise SetupProblem("I can't find the GitHub CLI (gh). Install it and sign in, then try again.")


def push_secret(gh, repo, name, value):
    """Send one value to GitHub through stdin. Returns True on success. Nothing gh says is shown."""
    result = subprocess.run(
        [gh, "secret", "set", name, "--repo", repo],
        input=value, text=True, capture_output=True,
    )
    return result.returncode == 0


def run(dry_run, repo=REPO, env_path=ENV_PATH, gh=None, out=print):
    """Copy every secret that has a value. Returns (copied, skipped, failed) as lists of names."""
    values = read_env_values(env_path)
    copied, skipped, failed = [], [], []
    if not dry_run and gh is None:
        gh = find_gh()

    out(f"Repo: {repo}")
    out("Dry run: names only, nothing is sent." if dry_run else "Copying from .env to GitHub secrets.")
    for name in SECRET_NAMES:
        if not values.get(name):
            skipped.append(name)
            out(f"  skipped  {name}  (missing or empty in .env)")
        elif dry_run:
            copied.append(name)
            out(f"  would copy  {name}")
        elif push_secret(gh, repo, name, values[name]):
            copied.append(name)
            out(f"  copied   {name}")
        else:
            failed.append(name)
            out(f"  FAILED   {name}  (gh did not accept it)")

    verb = "would copy" if dry_run else "copied"
    out(f"Done: {len(copied)} {verb}, {len(skipped)} skipped, {len(failed)} failed.")
    return copied, skipped, failed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Copy secrets from .env into GitHub Actions secrets.")
    parser.add_argument("--dry-run", action="store_true", help="only list the names, send nothing")
    parser.add_argument("--repo", default=REPO, help=f"owner/name (default {REPO})")
    args = parser.parse_args(argv)
    try:
        _, _, failed = run(args.dry_run, repo=args.repo)
    except SetupProblem as problem:
        print(problem)
        return 1
    except Exception as error:
        # Only the kind of error is shown, in case details contain secrets.
        print(f"Something went wrong ({type(error).__name__}). Nothing else was changed.")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
