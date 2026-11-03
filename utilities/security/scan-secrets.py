#!/usr/bin/env python3
"""scan-secrets.py — independent credential-shape scan of run exports (counts only).

The second half of the hygiene rule in CLAUDE.md § 6: the scrubbers
(utils/clean-telemetry.sh, the extractor's --scrub) REMOVE what they know
about; this script checks the result with a separately written set of
patterns and prints only COUNTS. It never echoes a match, a line, or a
blacklist entry, so its output is safe to paste into a report or a terminal
log — a hit tells you which class leaked and in which file, then you go look
on the box.

Why counts-only and class-shaped: in the fable run, vendor prefixes
(sk-ant-, ghp_, rpa_) found nothing while 187 real token occurrences sat in
the store as labelled `token=…` values, OAuth URL params and a relay secret
the agent had `cat`ed. Fix the class: every known vendor shape PLUS the
label/bearer/JWT/PEM shapes that catch a secret we have never seen before.

Usage:
    scan-secrets.py [--blacklist FILE] [--quiet] PATH...

    PATH        a file, a *.gz file, or a directory (scanned recursively)
    --blacklist literal strings, one per line (make-blacklist.sh output);
                counted as a separate class, reported by entry index only
    --quiet     print only the summary line and per-file hit lines

Exit status: 0 clean, 1 at least one hit, 2 usage / unreadable input.

Runs on the box (python3 >= 3.10) and locally (3.9); stdlib only.
"""

import argparse
import gzip
import io
import os
import re
import sys

# One entry per credential class. Order is the report order. Lookbehinds keep
# the vendor prefixes from matching inside longer identifiers; the labelled
# class deliberately excludes values that start with a scrub marker ('[', '*',
# '<', '{') so "[REDACTED]", "***", "<token>" and "{{PLACEHOLDER}}" stay quiet.
PATTERNS = [
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("openai_project_key", r"sk-proj-[A-Za-z0-9_\-]{20,}"),
    ("openrouter_key", r"sk-or-v1-[A-Za-z0-9]{20,}"),
    ("generic_sk_key", r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{32,}"),
    ("github_token", r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36}"),
    ("github_pat", r"github_pat_[A-Za-z0-9_]{60,}"),
    ("runpod_key", r"(?<![A-Za-z0-9])rpa?_[A-Za-z0-9]{20,}"),
    ("aws_access_key", r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    ("telegram_bot_token", r"(?<![0-9])[0-9]{8,10}:AA[A-Za-z0-9_\-]{30,}"),
    ("huggingface_token", r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}"),
    ("gitlab_token", r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_\-]{20,}"),
    ("slack_token", r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9\-]{10,}"),
    # gog (Google Workspace CLI) is provisioned on every box: API key, OAuth
    # client secret, access token shapes.
    ("google_credential", r"(?<![A-Za-z0-9])(?:AIza[0-9A-Za-z_\-]{35}|GOCSPX-[A-Za-z0-9_\-]{20,}|ya29\.[A-Za-z0-9_\-]{30,})"),
    # header.payload.signature, both JSON segments start with base64("{\"") == eyJ
    ("jwt", r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("bearer_token", r"(?i)\bbearer\s+(?![\[*<{])[A-Za-z0-9_\-\.~+/=]{20,}"),
    ("private_key_block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # label [:=] opaque value — catches secrets with no vendor prefix at all.
    # Quotes may be JSON-escaped (\") because the inputs are JSONL. The value
    # must mix letters and digits: that is what every API key / token / hash
    # looks like, and it keeps prose such as "auth: authentication_error" quiet.
    ("labelled_secret",
     r"(?i)\b(?:api[_\-]?key|apikey|secret|token|password|passwd|pwd|auth"
     r"|access[_\-]?token|refresh[_\-]?token|client[_\-]?secret|private[_\-]?key)"
     r"(?:\\?[\"'])?\s*(?:[:=]|=>)\s*(?:\\?[\"'])?(?![\[*<{])"
     r"(?=[A-Za-z_\-]*[0-9])(?=[0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{16,}"),
]

COMPILED = [(name, re.compile(rx)) for name, rx in PATTERNS]


def load_blacklist(path):
    """Literal strings to count. Entries shorter than 8 chars are ignored —
    they would match everywhere and mean nothing."""
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n").rstrip("\r")
            if len(line) >= 8:
                entries.append(line)
    return entries


def open_text(path):
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def expand_paths(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    out.append(os.path.join(root, f))
        else:
            out.append(p)
    return out


def scan_file(path, blacklist):
    """Return (per_pattern_counts, per_blacklist_counts, line_count)."""
    counts = {name: 0 for name, _ in COMPILED}
    bl_counts = [0] * len(blacklist)
    lines = 0
    with open_text(path) as fh:
        for line in fh:
            lines += 1
            for name, rx in COMPILED:
                n = sum(1 for _ in rx.finditer(line))
                if n:
                    counts[name] += n
            for i, lit in enumerate(blacklist):
                n = line.count(lit)
                if n:
                    bl_counts[i] += n
    return counts, bl_counts, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="credential-shape scan, counts only")
    ap.add_argument("--blacklist", help="file of literal strings to count (never printed)")
    ap.add_argument("--quiet", action="store_true", help="summary and per-file hit lines only")
    ap.add_argument("paths", nargs="+", metavar="PATH")
    args = ap.parse_args(argv)

    blacklist = []
    if args.blacklist:
        try:
            blacklist = load_blacklist(args.blacklist)
        except OSError as e:
            print("scan-secrets: cannot read blacklist: %s" % e.strerror, file=sys.stderr)
            return 2

    files = expand_paths(args.paths)
    if not files:
        print("scan-secrets: no files to scan", file=sys.stderr)
        return 2

    totals = {name: 0 for name, _ in COMPILED}
    bl_totals = [0] * len(blacklist)
    per_file_hits = []
    total_lines = 0
    for path in files:
        try:
            counts, bl_counts, lines = scan_file(path, blacklist)
        except OSError as e:
            print("scan-secrets: cannot read %s: %s" % (path, e.strerror), file=sys.stderr)
            return 2
        total_lines += lines
        file_hits = sum(counts.values()) + sum(bl_counts)
        if file_hits:
            per_file_hits.append((path, file_hits))
        for name in totals:
            totals[name] += counts[name]
        for i, n in enumerate(bl_counts):
            bl_totals[i] += n

    grand = sum(totals.values()) + sum(bl_totals)
    width = max(len(name) for name, _ in COMPILED) + 2
    print("scan-secrets: %d file(s), %d line(s), %d blacklist entr%s"
          % (len(files), total_lines, len(blacklist), "y" if len(blacklist) == 1 else "ies"))
    if not args.quiet:
        for name, _ in COMPILED:
            print("  %-*s %d" % (width, name, totals[name]))
        print("  %-*s %d" % (width, "blacklist_literal", sum(bl_totals)))
        for i, n in enumerate(bl_totals):
            if n:
                print("    blacklist entry #%d (%d chars): %d" % (i + 1, len(blacklist[i]), n))
    for path, n in per_file_hits:
        print("  HIT %s: %d" % (path, n))
    if grand:
        print("total: %d hit(s) — NOT CLEAN" % grand)
        return 1
    print("total: 0 hit(s) — clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
