#!/usr/bin/env python3
"""render_prompt.py <template> [KEY=VALUE ...] — substitute %%KEY%% tokens.

Plain string replacement (no sed): values may contain any character. Unknown
tokens left in the template are a hard error so a typo can't silently ship an
un-substituted prompt to the agent.
"""
import re
import sys


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as f:
        text = f.read()
    for pair in sys.argv[2:]:
        key, _, value = pair.partition("=")
        text = text.replace(f"%%{key}%%", value)
    leftover = sorted(set(re.findall(r"%%[A-Z_]+%%", text)))
    if leftover:
        sys.exit(f"render_prompt.py: unsubstituted tokens in {sys.argv[1]}: {', '.join(leftover)}")
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
