"""Render PROMPTS.md: the prompt text this drop-in supplies, one block per file, for reading and rewriting.

After changing a prompt, persona, standing-context file, delegate description or gate message, rerun from
src/sdk-scaffold:

    .venv/bin/python examples/tabpfn/render_prompts.py

PROMPTS.md is not committed: generate it when you want to read or edit the prompts. Paragraphs are unwrapped so the
text is easy to edit in a document editor; the words are the files' own. GATE_MESSAGES is checked against tabpfn_extensions.py, so rendering
fails when the gate's messages change: update the table rather than the check.

PROMPTS.md also shows the setup sections the scaffold generates, built offline from the scaffold code here.
"""

import argparse
import ast
import re
import shutil
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCAFFOLD_ROOT = HERE.parents[1]
sys.path.insert(0, str(SCAFFOLD_ROOT))  # run as a script, like the tests' rootdir import

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

TARGET = HERE / "PROMPTS.md"
# Stand-ins for the run's placeholders and credentials: assembling the scaffold calls nothing.
SLACK = {"{{SLACK_CHANNEL_ID}}": "C0PROMPTS", "{{SLACK_OPERATOR_ID}}": "U0PROMPTS", "{{SLACK_AFTER_TS}}": "0"}
ENV = {"CRUX_MODEL": "google/gemini-4-argon", "CRUX_REASONING_EFFORT": "high", "RUN_SLUG": "tabpfn-prompts",
       "OPENROUTER_API_KEY": "unused", "SLACK_BOT_TOKEN": "unused", "COST_TRACKER_URL": "https://cost.invalid/cost",
       "COST_TRACKER_TOKEN": "unused", "RUNPOD_API_KEY": "unused"}
# Lines that stay on their own when paragraphs are unwrapped: headings, list items, table rows, fences and LaTeX.
STRUCTURAL = re.compile(r"^(#|- |\* |\d+\. |\||```|\\)")

# The review gate's messages in tabpfn_extensions.py: (message, when it's sent). None: recorded, never sent.
GATE_MESSAGES = [
    (("Submission incomplete or invalid. Build the paper and submit REVIEW_REQUEST.json; finalization also needs the "
      "README, reproduction command, HTML, evidence manifest and report."),
     ("A file the gate needs is missing or unreadable: no PDF yet, no `REVIEW_REQUEST.json`, no `paper.aux`, a "
      "missing deliverable. In the research phase this follows every turn that ends without a review request.")),
    ("Continue research; request review with the current PDF SHA-256.",
     "`REVIEW_REQUEST.json` exists but names a different PDF."),
    ("The plain-text paper/abstract.txt must contain 1–200 words.", "The abstract is empty or longer than 200 words."),
    ("Rebuild paper/paper.tex; the crux:main-end label must be on main-body page 1–9.",
     "The label is missing from `paper.aux` or lies past page 9."),
    ("The submission must contain 1–32 total pages, including references and appendices.",
     "The PDF is longer than 32 pages."),
    ("The PDF changed during review; collect writers and submit the current PDF.",
     "`paper/paper.pdf` changed while the reviewer was reading it."),
    ("Missing final deliverable: `<path>`",
     "`README.md`, `reproduce.sh`, `paper/paper.tex`, `results/index.html` or `COMPLETION_REPORT.md` is empty."),
    ("Update COMPLETION_REPORT.md with final PDF SHA-256 `<digest>` and unedited review `<review path>`.",
     "The report doesn't name the current PDF hash and review path."),
    ("evidence/claims.json must list the paper's claims and their evidence.", "The manifest isn't a non-empty JSON list."),
    ("Every evidence claim needs a claim, re-verification command, artifact path and matching sha256.",
     "An entry is incomplete or its artifact's hash doesn't match."),
    ("Headline reproduction failed; inspect REPRODUCTION_STATUS.json.", "`bash reproduce.sh` exited non-zero."),
    ("Reproduction changed the paper; submit the rebuilt PDF for review.",
     "Running `reproduce.sh` changed `paper/paper.pdf`."),
    ("Final package recorded; accepted=`<true or false>`. Review: `<path>`", None),
]


def literal_prefix(node):
    """A string literal's text, or an f-string's text before its first field; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.JoinedStr):
        prefix = ""
        for part in node.values:
            if not isinstance(part, ast.Constant):
                return prefix, False
            prefix += part.value
        return prefix, True
    return None


def check_gate_messages():
    """GATE_MESSAGES must list exactly the review gate's literal feedback messages."""
    tree = ast.parse((HERE / "tabpfn_extensions.py").read_text())
    found = []
    for function in ast.walk(tree):
        if (isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
                and function.name in {"paper_problem", "package_problem", "evaluate"}):
            for node in ast.walk(function):
                if isinstance(node, ast.Return):
                    literal = literal_prefix(node.value)
                elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "result":
                    literal = literal_prefix(node.args[1])
                else:
                    continue
                if literal:
                    found.append(literal)

    def matches(row, literal):
        prefix, whole = literal
        return row == prefix if whole else row.startswith(prefix)

    rows = [message for message, _ in GATE_MESSAGES]
    missing = [prefix for prefix, whole in found if not any(matches(row, (prefix, whole)) for row in rows)]
    stale = [row for row in rows if not any(matches(row, literal) for literal in found)]
    if missing or stale:
        raise ValueError(f"tabpfn_extensions.py gate messages changed; update GATE_MESSAGES in render_prompts.py "
                         f"(not listed: {missing}; no longer in the code: {stale})")


def unwrap(text):
    """Join hard-wrapped lines into paragraphs; structural lines keep their own lines."""
    lines = []
    for line in text.rstrip("\n").split("\n"):
        if lines and line.strip() and lines[-1].strip() and not STRUCTURAL.match(line) \
                and not STRUCTURAL.match(lines[-1]):
            lines[-1] += " " + line.strip()
        else:
            lines.append(line)
    return "\n".join(lines)


@contextmanager
def offline_scaffold(env):
    """This drop-in assembled in a temporary copy, with stand-ins for the run's placeholders."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "tabpfn"
        shutil.copytree(HERE, root, ignore=shutil.ignore_patterns(TARGET.name, "__pycache__"))
        toml = root / "scaffold.toml"
        text = toml.read_text()
        for placeholder, value in SLACK.items():
            text = text.replace(placeholder, value)
        toml.write_text(text)
        yield Scaffold(DropInDirectory.load(root), env, state_dir=Path(tmp) / "state", telemetry=NullTelemetry())


def scaffold_setup():
    """The setup sections the scaffold generates for the PI and the research assistant, Slack IDs in angle brackets.

    PROMPTS.md can't show the {{...}} placeholders themselves: the drop-in refuses to load with any left unresolved.
    """
    with offline_scaffold(ENV) as scaffold:
        pi = scaffold.assembly.instructions("pi").split("\n\n---\n\n", 1)[0]
        ra = scaffold.assembly.instructions("research_assistant")
        ra = ra.removeprefix(scaffold.drop_in.instructions("research_assistant")).strip()
    if not (pi.startswith("# Scaffold setup") and ra.startswith("# Scaffold setup")):
        raise ValueError("the scaffold's instruction layout changed; update scaffold_setup in render_prompts.py")
    pi = pi.replace(SLACK["{{SLACK_CHANNEL_ID}}"], "<Slack channel ID>")
    pi = pi.replace(SLACK["{{SLACK_OPERATOR_ID}}"], "<operator's Slack user ID>")
    return pi, ra


class Document:
    def __init__(self):
        self.parts = []

    def add(self, text):
        self.parts.append(text.strip("\n") + "\n")

    def block(self, heading, who, body, keep=None):
        if "```" in body:
            raise ValueError(f"{heading} contains a code fence; render it another way")
        self.add(f"### {heading}\n\n{who}")
        if keep:
            self.add(f"Keep: {keep}")
        self.parts.append(f"```text\n{body}\n```\n")

    def file(self, rel, who, keep=None, *, wrap=True):
        body = (HERE / rel).read_text()
        self.block(f"`{rel}`", who, unwrap(body) if wrap else body.rstrip("\n"), keep)

    def text(self):
        return "\n".join(self.parts)


def render():
    check_gate_messages()
    config = tomllib.loads((HERE / "scaffold.toml").read_text())
    doc = Document()
    doc.add("""
# TabPFN run: prompts

This file collects the prompt text this drop-in supplies to the TabPFN research run's agents, so it can be read, reviewed and rewritten in one place. Each grey block is one file as the model receives it, except that line breaks inside paragraphs have been joined to make editing easier. Markdown inside a block (`#` headings, `**bold**`, `-` lists) is part of the prompt. Where a block has a "Keep" note, the harness's code depends on that text.

This file is generated by `render_prompts.py`, so editing it changes nothing. To rewrite prompts, edit a copy (for example in Google Docs), carry the changes into the files named in the headings, then rerun from `src/sdk-scaffold`:

```bash
.venv/bin/python examples/tabpfn/render_prompts.py
```

Section 6 shows the largest piece of text generated around these prompts: the scaffold's setup section. Still not included: the tool definitions sent with each call, the research context and resource status added to each call, Codex's own additions to each worker request, and tool results. They come from the scaffold code in `src/sdk-scaffold/crux_scaffold/` and from Codex.
""")

    doc.add("## 1. PI (orchestrator)")
    doc.file("personas/pi.md", "The PI's persona: its system instructions on every model call, after the scaffold's "
             "generated setup section (section 6).")
    doc.file("PROMPT.md", "The PI's first message of the run (research phase).")
    doc.file("prompts/research-continue.md", "Every later message in the research phase, after the gate has run.",
             "`$iteration` (the iteration just finished) and `$feedback` (replaced by the gate's feedback: the "
             "reviewer's full review or one of the gate messages below).")
    doc.file("prompts/finalize.md", "The PI's first message of the finalize phase.",
             "the deliverables the final gate checks: `README.md`, `reproduce.sh`, `results/index.html`, "
             "`COMPLETION_REPORT.md` (naming the final PDF's SHA-256 and the review path) and `evidence/claims.json` "
             "as a list of entries with `claim`, `artifact`, `sha256` and `command`.")
    doc.file("prompts/finalize-continue.md", "Every later message in the finalize phase.",
             "`$phase`, `$iteration` and `$feedback`.")

    delegates = [(name, config["agents"][name]["description"]) for name in config["agents"]["pi"]["delegates"]
                 if name in config["agents"]]
    delegates += [(name, config["coding_agents"][name]["description"]) for name in config["agents"]["pi"]["delegates"]
                  if name in config["coding_agents"]]
    rows = "\n".join(f"| `{name}` | {description} |" for name, description in delegates)
    doc.add(f"""
### Delegate descriptions (`scaffold.toml`)

How the PI's helpers are described to it, in its setup section and as the descriptions of the tools that call them. The scaffold adds a sentence to each Codex worker's description saying it starts in the background.

| Delegate | Description |
|---|---|
{rows}
""")
    rows = "\n".join(f"| {when} | {message} |" for message, when in GATE_MESSAGES if when)
    doc.add(f"""
### Gate messages (`tabpfn_extensions.py`)

What the PI is told, through `$feedback`, when a review gate stops before or after the reviewer. Keep: the values in angle brackets, which the code fills in.

| When it's sent | Message |
|---|---|
{rows}
""")

    worker = config["coding_agents"]["worker_a"]
    files = ", ".join(f"`{rel}`" for rel in worker["context"])
    dynamic = ", ".join(f"`{rel}`" for rel in worker.get("workspace_context", []))
    own_copy = ("a user message with Codex's own copy of `workspace/AGENTS.md`, which it loads from the workspace by "
                "itself, then " if worker.get("project_docs", True) else "a user message with ")
    doc.add("## 2. Research assistant and Codex workers")
    doc.file("personas/ra.md", "The research assistant's system instructions on every call. The three Codex workers "
             "get the same text as part of their developer instructions"
             + ("." if worker.get("system_prompt") else "; their system prompt is Codex's own."))
    if worker.get("system_prompt"):
        doc.file(worker["system_prompt"], "The Codex workers' system prompt on every call, in place of Codex's "
                 "built-in one, which is written for an interactive coding session with a person. The rest of each "
                 f"worker request, in order: a developer message with the scaffold's developer instructions ({files}), "
                 f"then Codex's list of its bundled skills and a note on its sandbox permissions; {own_copy}a block "
                 "describing the environment (working directory, shell, date and timezone); and a user message with "
                 f"the research context (workflow state and {dynamic}), then \"# Current task\" and the PI's brief.")

    doc.add("## 3. Reviewer")
    doc.file("prompts/review.md", "The reviewer's system instructions, for both the research and the final review. Its "
             "only other input is the line \"Available submission artifacts: - submission.pdf\" and what it reads "
             "from the PDF.",
             "the last paragraph's rules, which the gate depends on: the whole review goes in `feedback`, `passed` is "
             "true exactly when the paper should be accepted (now Overall 5 or 6), and `next_prompt` is null. Also "
             "keep an \"Overall\" score and a \"Recommendation\" line if you want the calibration scripts in "
             "`verifier-calibration` to keep scoring reviews.")

    doc.add("""
## 4. Standing context (PI, research assistant and Codex workers)

These files are inserted into every agent's context. `PLAN.md` is reread whenever the context is refreshed, because the PI rewrites it during the run.
""")
    doc.file("workspace/AGENTS.md", "The operating rules for every agent.",
             "the build command, `abstract.txt` as plain text, the `\\label{crux:main-end}` label, the limits the gate "
             "checks (main body at most 9 pages, abstract at most 200 words), the `REVIEW_REQUEST.json` format "
             "`{\"paper_sha256\": \"...\"}`, and the `CRUX_RUN_SLUG` marker on RunPod pods.")
    doc.file("workspace/RESEARCH.md", "The research question.")
    doc.file("workspace/PLAN.md", "The starting plan template, which the PI replaces with its own plan.")

    doc.add("""
## 5. Starter files the agents edit

Not inserted into any prompt, but the agents read and rewrite them.
""")
    doc.file("workspace/LOG.md", "The header of the PI's append-only research log.")
    doc.file("workspace/paper/abstract.txt", "The placeholder abstract.")
    doc.file("workspace/paper/paper.tex", "The starter paper. Its line breaks are kept because it's LaTeX.",
             "valid LaTeX, `\\input{abstract.txt}` inside the abstract and `\\label{crux:main-end}` right after the main "
             "body.", wrap=False)

    pi_setup, ra_setup = scaffold_setup()
    doc.add("""
## 6. Generated by the scaffold

These blocks don't come from this drop-in's prompt files, so rewording them takes a scaffold change, as noted under each. They are exact: their line breaks are the model's.
""")
    doc.block("Scaffold setup (PI)", "The top of the PI's system instructions on every call, before `personas/pi.md` "
              "(a `---` line separates them). The scaffold writes it from `scaffold.toml`, so the loop, gates, "
              "delegates, tools, limits and Slack settings follow the configuration; the run fills in the Slack IDs "
              "shown in angle brackets. Its wording is in `crux_scaffold/scaffold.py` and is shared by every "
              "drop-in. From this drop-in, `[preamble] append = \"...\"` in `scaffold.toml` adds text after it, and "
              "`[preamble] enabled = false` removes it, including the operator-steering rules, together with the "
              "research assistant's setup below.", pi_setup)
    doc.block("Scaffold setup (research assistant)", "Appended to the research assistant's system instructions, "
              "after `personas/ra.md`, on every call. Same source and switches as the PI's setup.", ra_setup)
    return doc.text()


def main(argv=None):
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    TARGET.write_text(render())
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
