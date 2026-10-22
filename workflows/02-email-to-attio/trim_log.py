"""Keep a log file short: only its latest lines are kept.

Usage:  python trim_log.py LOGFILE [KEEP]

It keeps the last KEEP lines (500 if KEEP is left out). To avoid rewriting the file on every
run, it only cuts once the file has grown SLACK lines past KEEP. It works on raw bytes, so the
kept lines are exactly as they were, and it never stops the run: a missing file, a locked file
or a bad number are all quietly ignored, and it always ends successfully and prints nothing.

The scheduler batch files call this before they start writing to their log.
"""

import os
import sys
from pathlib import Path

DEFAULT_KEEP = 500
SLACK = 100


def trim_log(path, keep=DEFAULT_KEEP, slack=SLACK):
    """Cut the file to its last `keep` lines if it is over `keep + slack` lines. Never raises.

    Returns True if the file was cut, False if it was left alone.
    """
    temp = None
    try:
        path = Path(path)
        lines = path.read_bytes().splitlines(keepends=True)
        if len(lines) <= keep + slack:
            return False
        temp = path.with_name(path.name + ".tmp")
        temp.write_bytes(b"".join(lines[-keep:]))
        os.replace(temp, path)
        temp = None
        return True
    except OSError:
        return False
    finally:
        if temp is not None:
            try:
                temp.unlink()
            except OSError:
                pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return 0
    keep = DEFAULT_KEEP
    if len(argv) > 1:
        try:
            keep = max(1, int(argv[1]))
        except ValueError:
            pass
    trim_log(argv[0], keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
