#!/usr/bin/env python3
"""Test verify.py against the published CRUX-2 crosswalk and its sources.

Sources: the agents' final self-reviews in the two run workspaces, and the
expert reviews reproduced in content/crux/crux-2.md.
Run from the repo root: python3 .claude/skills/verify-review-crosswalk/test.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VERIFY = Path(__file__).parent / "verify.py"

# expert reviews live inside the article's appendix; extract to temp files
article = (REPO / "content" / "crux" / "crux-2.md").read_text(encoding="utf-8")
expert_files = {}
for slug, marker in (("personas", "#### Personas review from paper authors"),
                     ("tabpfn", "#### TabPFN review from paper authors")):
    block = article.split(marker, 1)[1]
    for stop in ("\n#### ", "\n### "):
        if stop in block:
            block = block.split(stop, 1)[0]
    path = Path(tempfile.gettempdir()) / f"crux-crosswalk-expert-{slug}.md"
    path.write_text(block, encoding="utf-8")
    expert_files[slug] = path

out = subprocess.run(
    [
        sys.executable, str(VERIFY),
        "--data", str(REPO / "app" / "data" / "reviewCrosswalk.ts"),
        "--source", f"personas:expert={expert_files['personas']}",
        "--source", f"personas:self={REPO / 'crux-trait-space' / 'reviews' / 'final_review.md'}",
        "--source", f"tabpfn:expert={expert_files['tabpfn']}",
        "--source", f"tabpfn:self={REPO / 'pfn-harmful-shift-detector' / 'reviews' / 'final_blind.md'}",
    ],
    capture_output=True, text=True,
)
print(out.stdout.strip())
if out.returncode != 0:
    print("FAIL")
    sys.exit(1)
print("[verify-review-crosswalk] PASS")
