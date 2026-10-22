"""Blank anything shaped like an email address in a log, line by line.

Usage (the GitHub Actions workflow puts it in front of the log):  python run_something.py 2>&1 | python log_filter.py

The scripts already print only short IDs, categories and counts, so this is a safety net: if an address ever reached
the output by mistake (for example inside an error message from another service), it is replaced by [removed] before
it can be written to a log that others may read. Any piece of text without spaces that has an "@" with something in
front of it is treated as an address and replaced whole, with its brackets or punctuation. Everything else is passed
through unchanged, byte for byte: IDs, counts, plain words, a lone "@", an @handle.

It reads and writes no file, writes each line as soon as it arrives, and never stops or fails whatever it is given
(empty input, damaged text, huge lines): it always ends successfully, so it can never hide the real result of a run
when the shell is told to keep the exit code of the script in front of it.
"""

import re
import sys

MARK = "[removed]"
TOKEN = re.compile(r"\S+")


def _is_address(token):
    """True if the token has an @ with a character other than @ before it (a long token is checked in one pass)."""
    return any(token[i] == "@" and token[i - 1] != "@" for i in range(1, len(token)))


def _replace(match):
    token = match.group(0)
    return MARK if _is_address(token) else token


def clean(text):
    """The text with every address-shaped piece replaced by [removed]."""
    return TOKEN.sub(_replace, text)


def main(stdin=None, stdout=None):
    source = stdin if stdin is not None else sys.stdin.buffer
    target = stdout if stdout is not None else sys.stdout.buffer
    try:
        for raw in source:
            text = raw.decode("utf-8", errors="replace")
            target.write(clean(text).encode("utf-8", errors="replace"))
            target.flush()
    except Exception:
        pass            # the filter must never stop or fail
    return 0


if __name__ == "__main__":
    sys.exit(main())
