---
name: crux-run-timeline
description: >-
  Analyze a CRUX agent run from its live/finished box and produce a single
  self-contained HTML page: a plain-language timeline, a 15-step story, and a
  5–7 Whys root-cause analysis. Use when someone gives you an SSH connection
  string to a CRUX box and asks what happened, why a run failed/stalled, or for
  a shareable write-up. Input: an SSH connection string. Output: timeline.html.
---

# CRUX run timeline + root-cause analysis

Turn a running or finished CRUX box into one shareable `timeline.html` that a
non-expert can read: a short summary, a stepwise story, a 5-Whys root-cause
analysis, and the full searchable event log.

- **Input:** an SSH connection string, e.g.
  `ssh -i /path/key.pem ubuntu@HOST`
- **Output:** `analysis/clean-room/<name>/timeline.html` (self-contained, no assets, credentials scrubbed)

Bundled files (this skill's directory):
- `build_timeline.py` — the generator. Do not rewrite it; drive it.
- `narrative.template.json` — the analysis you fill in (beats + whys + summary).
- `narrative.example.json` — a real completed example from a prior run.

## Ground rules

1. **Everything lands under `analysis/clean-room/<name>/`.** Never write run
   data anywhere else in the repo. Pick a short `<name>` for the run (e.g.
   a short label for the run, or the box hostname).
2. **`analysis/clean-room/` is (and must stay) gitignored** — raw telemetry and
   downloaded workspaces contain **live credentials**. Confirm it is ignored
   (`git check-ignore analysis/clean-room/x`) before downloading anything; if
   not, add `analysis/clean-room` to `.gitignore` first. Never commit anything
   from this tree.
3. **Never print, log, or paste credentials.** The raw `.telemetry.jsonl` and
   the box's `.env` files hold live keys. The generator scrubs known key shapes
   from the HTML and hard-fails if one survives, but you must also avoid echoing
   secrets in the chat.
4. **Treat a live box as read-only.** Only `ls`, `stat`, `du`, and file
   downloads. Do not restart services or edit box state — a running CRUX box is
   an active experiment.

## Procedure

### 1. Set up the clean room

```
NAME=<short-run-name>
DIR=analysis/clean-room/$NAME
mkdir -p "$DIR"
git check-ignore "$DIR" || echo "WARN: add analysis/clean-room to .gitignore first"
```

### 2. Locate the artifacts on the box (read-only)

The two things the generator needs are the research log and (optionally) the
telemetry. On a standard CRUX box:

```
SSH="ssh -i /path/key.pem ubuntu@HOST"      # the provided connection string
$SSH 'ls -la ~/.openclaw/workspace/LOG.md ~/.openclaw/logs/telemetry.jsonl; du -sh ~/.openclaw/workspace'
```

If paths differ, find them:
```
$SSH 'sudo find /home -maxdepth 4 -name LOG.md 2>/dev/null; sudo find /home -maxdepth 4 -name "telemetry.jsonl" 2>/dev/null'
```

### 3. Download into the clean room

`LOG.md` is required; telemetry is optional (used only for operator-message
timing). The full `workspace/` mirror is optional — pull it only if you need to
read code/reviews for the analysis; it can be multiple GB.

```
# required: the log (small)
scp -i /path/key.pem ubuntu@HOST:.openclaw/workspace/LOG.md "$DIR/workspace/LOG.md"   # mkdir -p "$DIR/workspace" first
# optional: telemetry (tens of MB) — enables operator-message timestamps
scp -i /path/key.pem ubuntu@HOST:.openclaw/logs/telemetry.jsonl "$DIR/.telemetry.jsonl"
# optional: full workspace mirror (large) — only if you must inspect code/reviews
rsync -az -e "ssh -i /path/key.pem" --exclude '.git' --exclude '.venv' \
  ubuntu@HOST:.openclaw/workspace/ "$DIR/workspace/"
```

The generator expects `LOG.md` at `$DIR/workspace/LOG.md`.

### 4. Read and understand the run

Do the actual analysis. Read `$DIR/workspace/LOG.md` end to end (skim headers
with `rg '^### '`, then read the memos — lines starting `MEMO M-`). Identify:
- the task and budgets (see `BRIEF.md` / early log entries),
- the major turning points (pivots, kills, credential events, milestone gates),
- what actually failed, and the *why-behind-the-why* for each failure.

Spot-check surprising claims against artifacts before you rely on them. If you
downloaded the full workspace, read the relevant `reviews/*.md` and `code/`.

### 5. Write the narrative

Copy the template and fill it in with **your** analysis:

```
cp analysis/skills/crux-run-timeline/narrative.template.json "$DIR/narrative.json"
```

- `lead`: 1–2 sentences stating the outcome up front (or "" to omit).
- `beats`: the story in ~10–15 steps. Each is `{time,title,desc,cls}`. Use plain
  language — no jargon, no arrows, no memo codes *in the prose*. `cls` is one of:
  `start, credential, kill, block, memo, progress, launch, heartbeat, other`.
- `whys`: **one chain per distinct root failure** (usually 1–3). Each chain is
  5–7 `["Why…?","Because…"]` pairs that end at a *fixable cause, not another
  symptom*, plus a `root` sentence and concrete `fixes`.

Style throughout = ISO house style: short sentences, plain words, active voice,
neutral tone, no insider jargon. See `narrative.example.json` for a generic,
fictional worked example showing the expected shape and level of detail.

### 6. Build the HTML

```
python3 analysis/skills/crux-run-timeline/build_timeline.py "$DIR"
```

This writes `$DIR/timeline.html`. It parses every `### date time — title` entry
in `LOG.md`, auto-classifies and colour-codes them, renders your beats + whys on
top, and appends the full searchable/filterable event stream with each entry's
verbatim (scrubbed) body behind a "details" toggle.

### 7. Verify before sharing

```
grep -Eic 'sk-or-v1-|sk-ant-|ghp_|rpa_|AKIA[0-9A-Z]{16}' "$DIR/timeline.html"   # must be 0
python3 -c "import html.parser;html.parser.HTMLParser().feed(open('$DIR/timeline.html').read());print('html ok')"
open "$DIR/timeline.html"    # (macOS) or just report the path
```

The generator already asserts no known key shape reaches the output; the grep is
a second independent check. Report the path to the user; do **not** attach or
commit the file if the tree isn't a place they intend to publish.

## Notes

- The event stream is **source data** (the run's own log) and is shown unedited
  except for credential scrubbing. Only the summary/beats/whys are your prose,
  and they live in `narrative.json` — so re-running the build never touches your
  analysis and the same script serves any run.
- If a run uses credential shapes not covered by the scrubber, extend the
  `SECRET` regex at the top of `build_timeline.py` before building.
- Re-run step 6 freely as you refine `narrative.json`. To refresh a live run,
  re-download `LOG.md` (step 3) and rebuild.
