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
analysis, a telemetry-derived activity panel (what the agent actually did), and
the full searchable event log.

The `LOG.md` is the agent's own *narration*; `telemetry.jsonl` is the *ground
truth* of every tool call, subagent spawn, and operator message. Use telemetry
to check the log's story and to quantify activity the log only summarises. Every
number this skill puts on the page is counted directly from telemetry — never
estimated, never inferred from the prose.

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
   the box's `.env` files hold live keys. Telemetry `tool.start` records embed
   full command/patch `params`, so their bodies can contain secrets — treat the
   raw file as sensitive. The generator only emits *counts, tool names, subagent
   task names, and message timing/lengths* from telemetry (never raw params or
   message bodies) and scrubs every rendered string against the key regex,
   hard-failing if one survives. You must also avoid echoing secrets in the chat.
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

`LOG.md` is required. Telemetry is strongly recommended — it is the ground truth
for the activity panel and for checking the log's claims; pull it whenever it
exists (it is typically tens of MB). The full `workspace/` mirror is optional —
pull it only if you need to read code/reviews for the analysis; it can be
multiple GB.

```
# required: the log (small)
scp -i /path/key.pem ubuntu@HOST:.openclaw/workspace/LOG.md "$DIR/workspace/LOG.md"   # mkdir -p "$DIR/workspace" first
# recommended: telemetry (tens of MB) — ground-truth activity + message timing
scp -i /path/key.pem ubuntu@HOST:.openclaw/logs/telemetry.jsonl "$DIR/.telemetry.jsonl"
# optional: full workspace mirror (large) — only if you must inspect code/reviews
rsync -az -e "ssh -i /path/key.pem" --exclude '.git' --exclude '.venv' \
  ubuntu@HOST:.openclaw/workspace/ "$DIR/workspace/"
```

The generator expects `LOG.md` at `$DIR/workspace/LOG.md` and (optionally)
telemetry at `$DIR/.telemetry.jsonl`. If telemetry is present the build adds a
"What the agent actually did" panel; if absent, the build still works from the
log alone.

### 4. Read and understand the run

Do the actual analysis. Read `$DIR/workspace/LOG.md` end to end (skim headers
with `rg '^### '`, then read the memos — lines starting `MEMO M-`). Identify:
- the task and budgets (see `BRIEF.md` / early log entries),
- the major turning points (pivots, kills, credential events, milestone gates),
- what actually failed, and the *why-behind-the-why* for each failure.

**Ground-truth against telemetry.** The log is the agent's narration; telemetry
is what actually happened. `.telemetry.jsonl` is one JSON object per line. The
record types observed on real boxes are exactly these (do **not** invent fields):

| type | fields | use it for |
|---|---|---|
| `tool.start` | `sessionKey, agentId, toolName, params` | which tools ran and how often; `params.task_name` on `collaborationspawn_agent` names each delegated subagent |
| `tool.end` | `sessionKey, agentId, toolName, success, durationMs?, error?` | proven tool failures (`success:false` / `error`); durations exist on only *some* tools |
| `agent.start` | `agentId, prompt, promptLength` | turn/session starts |
| `message.in` | `channel, from, content, contentLength, timestamp, metadata` | operator→agent messages **with a wall-clock timestamp** (epoch ms) |
| `message.out` / `message.sending` | `channel, to, content, success?, error?` | agent→operator replies (no timestamp) |

Hard constraints when reasoning from telemetry:
- **Only `message.in` carries a timestamp.** Tool events are *ordered* but not
  individually time-stamped, so you cannot assign a clock time to a tool call
  from telemetry alone — cross-reference `LOG.md` for timing.
- **`durationMs` is present on only a subset of tools** (never `exec` /
  `apply_patch`). Do not report a per-tool runtime you cannot see.
- Quote a telemetry number only if you (or the generator) counted it from the
  file. Quick checks you can run yourself, e.g. tool histogram:
  ```
  python3 -c "import json,collections as c;t=c.Counter(json.loads(l).get('toolName') for l in open('$DIR/.telemetry.jsonl') if l.strip() and json.loads(l).get('type')=='tool.start');print(t.most_common(15))"
  ```

If the log claims something the telemetry contradicts (e.g. "ran the reviewer
20 times" but the tool count is 3), trust the telemetry and say so in the
narrative. Spot-check surprising claims against artifacts before you rely on
them. If you downloaded the full workspace, read the relevant `reviews/*.md` and
`code/`.

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
verbatim (scrubbed) body behind a "details" toggle. If `.telemetry.jsonl` is
present it also inserts a "What the agent actually did" panel — tool-call
histogram, subagent-spawn list, proven tool failures, and operator-message
timing — with every figure counted straight from the telemetry file. The build
prints those counts so you can sanity-check them against your narrative.

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
  except for credential scrubbing. The telemetry panel is likewise **derived
  source data** — pure counts/names/timing computed from `.telemetry.jsonl`, not
  prose. Only the summary/beats/whys are your prose, and they live in
  `narrative.json` — so re-running the build never touches your analysis and the
  same script serves any run.
- The generator never renders raw telemetry `params` or message bodies (which can
  hold secrets); it emits only tool names, counts, subagent `task_name`s, message
  timing, and lengths, each scrubbed against the key regex.
- If a run uses credential shapes not covered by the scrubber, extend the
  `SECRET` regex at the top of `build_timeline.py` before building.
- Re-run step 6 freely as you refine `narrative.json`. To refresh a live run,
  re-download `LOG.md` (step 3) and rebuild.
