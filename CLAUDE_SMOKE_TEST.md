# Claude Smoke Test: Drop-in Replacement

**Objective:** Run Claude as a drop-in replacement for OpenClaw in the CRUX harness. Minimal changes. Real 24h task.

**Status:** Provisioning script kicks off the run end to end. `setup-device-claude.sh` returns with the agent already working.

---

## What's a "Drop-in Replacement"?

Same harness. Same gates. Same AGENTS.md / PROMPT.md / TOOLS.md / playbooks/.

Only changes:
- **Runtime:** Claude Code CLI instead of the OpenClaw gateway. Same shape — an
  agent loop with bash/file/web tools and subagents — so the harness's tool
  assumptions hold.
- **Agent entry point:** `~/run-claude-agent.sh`, supervised by the `crux-agent`
  systemd service, instead of `openclaw gateway install/restart`.
- **Session management:** Claude Code's own resumable sessions. The run's UUID is
  persisted at `~/.crux/session-id`; the runner resumes it on restart.
- **Run persistence:** `/goal` instead of the 30-minute heartbeat. It installs a
  session-scoped Stop hook that blocks the turn from ending until the condition
  holds, so one invocation carries the whole project.
- **No watchdog:** no thinking-block wedge bug to recover from. `Restart=on-failure`
  plus session resume covers crashes.
- **No Telegram:** operator visibility is `journalctl -u crux-agent` plus the
  stream-json telemetry log.

Everything else stays the same.

---

## Files Provided

### 1. `linux/src/start-claude.sh`
Drop-in replacement for `linux/src/start.sh`.

Installs:
- ✓ XFCE + VNC desktop
- ✓ Python 3 (for the agent's experiment code, not for the runtime)
- ✓ GitHub CLI, authenticated with the classic PAT
- ✓ Claude Code CLI (native installer)
- ✓ Harness workspace at `~/crux-workspace` with placeholders resolved
- ✓ Agent runner (`~/run-claude-agent.sh`) + `crux-agent` systemd service, started

Does NOT install:
- ✗ OpenClaw gateway
- ✗ Watchdog (no wedge bug to recover from)
- ✗ Telegram integration
- ✗ gog / Google Workspace auth

**Usage:** `./setup-device-claude.sh placeholders.txt` — it provisions the box,
runs this bootstrap, and the run is live when it returns.

### 2. `run-claude-agent.sh`
Embedded in start-claude.sh; installed to the user's home and invoked by the
`crux-agent` service. Reads everything it needs from `~/.crux/env`.

Two invocations against one session, in this order:

1. **Opening turn** — `PROMPT.md` on a fresh run, or a re-orientation nudge
   ("read LOG.md and PLAN.md, pick up the current milestone") on a resumed one.
2. **Goal turn** — `/goal <condition>`. Its Stop hook blocks this turn from
   ending until the condition holds, so this call *is* the run. It returns when
   the project is done.

The order matters: `/goal` also instructs the agent to start working toward the
condition immediately, so setting it first would run the whole project without
ever reading `PROMPT.md`.

Run layout on the box:

| Path | What |
|---|---|
| `~/crux-workspace` | agent cwd; harness files |
| `~/.crux/PROMPT.md` | launch message (placeholders resolved) |
| `~/.crux/GOAL.md` | the `/goal` stop condition |
| `~/.crux/env` | secrets + run config; loaded by the service |
| `~/.crux/telemetry.jsonl` | stream-json turn log |
| `~/.crux/session-id` | delete to start the run over |

### 3. Harness Adaptations (minimal)

**AGENTS.md:** No changes (agent-agnostic constitution)

**PROMPT.md:** Update references:
- Line 6: "send scheduled one-way snapshots via Telegram" → "operator messages via file queue" (placeholder)
- Remove OpenClaw-specific session startup instructions

**TOOLS.md:**
- Add § Claude API specifics (session files, cost tracking)
- Update "OpenClaw footguns" → "Claude API footguns"

**gate_artifact.sh:** No changes (pure bash)

**playbooks/:** No changes (agent-agnostic)

---

## Smoke Test Plan

### Phase 1: Provisioning (1h)
1. Prepare EC2 instance with Ubuntu 22.04
2. Run `setup-device.sh` with `start-claude.sh` instead of `start.sh`
3. Verify:
   - Python 3 installed ✓
   - anthropic SDK installed ✓
   - ANTHROPIC_API_KEY accessible ✓
   - `~/run-claude-agent.sh` exists ✓

### Phase 2: Harness Setup (1h)
1. Copy `next-run-harness/` to the box
2. Update PROMPT.md (minimal: remove Telegram refs)
3. Update TOOLS.md (minimal: add Claude API section)
4. Verify:
   - Placeholders resolve ✓
   - AGENTS.md readable ✓
   - gate_artifact.sh executable ✓

### Phase 3: Single-turn Test (30 min)
```bash
cd ~/next-run-harness
~/run-claude-agent.sh . test-1
```

Expected: Claude reads PROMPT.md, responds. Output logged.

Verify:
- ✓ Claude receives full prompt
- ✓ Response is coherent
- ✓ Cost is tracked

### Phase 4: 24h Smoke Test (24h)
Run a **real research task** (not a toy task):

Example: "Literature survey on efficient transformers"
- Claude reads BRIEF.md / PLAN.md
- Claude performs literature search (via TOOLS.md APIs: Semantic Scholar, arXiv)
- Claude writes notes (creates `runs/lit-survey/notes.md`)
- Claude runs gate_artifact.sh checks
- Claude logs decisions to LOG.md

Expected output:
- ✓ Coherent research planning
- ✓ Tool use (curl for API calls)
- ✓ File creation (notes, logs)
- ✓ Multiple turns (if multi-turn is needed)
- ✓ Cost tracking (accurate token counts)

### Phase 5: Compare (2h)
Run same task on OpenClaw baseline.

Compare:
- Quality of output
- Cost
- Time taken
- Friction points

---

## Minimal Harness Changes

### PROMPT.md
**Location:** `next-run-harness/PROMPT.md`, line 9

**OLD:**
```
You send scheduled one-way snapshots (USER.md), and only two messages may ever ask anything of them...
```

**NEW:**
```
You send scheduled snapshots (USER.md), and only two messages may ever ask anything of them...
(Note: Operator messages come via file queue, not Telegram.)
```

### TOOLS.md
**Location:** `next-run-harness/workspace/TOOLS.md`, before "OpenClaw footguns"

**ADD new section:**
```markdown
## Claude API specifics

1. **Sessions are JSON files.** Stored at `~/.claude-sessions/{SESSION_ID}.json`.
   Load on resume; append messages; save after each turn.

2. **Cost tracking.** API response metadata includes token counts.
   Pricing: $3/1M input, $15/1M output tokens.

3. **No native Telegram.** Operator messages come via file queue (polling, not push).

4. **No native heartbeat.** Scheduled turning via cron job (implementation pending).

5. **No wedge detection.** Claude API is stable; no thinking-block corruption bug.
```

**MODIFY "OpenClaw footguns":**
```markdown
## Claude API footguns (follow exactly)

1. **File-based operator queue.** Instead of Telegram:
   - Operator creates `operator-queue.jsonl` with system messages
   - Agent polls queue on each turn; injects messages if found
   - Remove message after processing

2. **Session persistence.** Load JSON on resume; append messages; save.
   Example:
   ```bash
   cat ~/.claude-sessions/main-1.json | jq '.messages | length'  # Check turn count
   ```

3. **API cost in response metadata.** Track tokens from response.usage:
   ```python
   cost = (input_tokens * 3.0/1e6) + (output_tokens * 15.0/1e6)
   ```
```

### gate_artifact.sh
**No changes** (pure bash; works for any agent)

---

## Expected Friction Points

1. **No heartbeat — the run's liveness rides on `/goal`.** The Stop hook keeps one
   turn going until the condition holds. If that hook clears early or the turn
   dies for a non-crash reason, the service exits 0 and the run is over with no
   alarm. This is the main thing to watch in the 24h test: does the goal turn
   actually stay alive for hours? If not, a scheduled resume (cron calling the
   runner) is the fallback.

2. **Goal-condition wording is load-bearing.** It's the only thing standing
   between "works for 24h" and "declares victory at hour 2." It should name
   checkable artifacts and gate invocations, not vibes.

3. **No Telegram notifications:** operator visibility is `journalctl` plus the
   telemetry stream. `USER.md` snapshots have nowhere to go yet.

4. **Crash recovery is coarse:** `Restart=on-failure` resumes the session with a
   re-orientation nudge. There's no wedge *detection* — a hung turn that never
   exits is invisible to systemd.

5. **`AGENTS.md` rides the system prompt only.** It's passed via
   `--append-system-prompt` on every invocation; there is deliberately no
   `CLAUDE.md` symlink, which would put the constitution in context twice. The
   tradeoff: an operator running `claude` by hand in the workspace gets no
   constitution unless they pass the flag themselves.

---

## Provisioning: How to Use

### Option 1: EC2 Instance (Real Test)
```bash
# 1. Fill in linux/placeholders.txt (see placeholders.txt.example).
#    ANTHROPIC_MODEL may keep OpenClaw's provider-qualified form
#    (anthropic/claude-opus-4-8) — start-claude.sh strips the prefix.
#    Optionally set GOAL_CONDITION to scope the run.

# 2. Provision. The run starts before this returns.
cd linux && ./setup-device-claude.sh placeholders.txt

# 3. Watch it
ssh -i ~/.ssh/crux-key ubuntu@<box-ip> 'journalctl -u crux-agent -f'
ssh -i ~/.ssh/crux-key ubuntu@<box-ip> 'tail -f ~/.crux/telemetry.jsonl'
```

To stop, restart, or reset the run on the box:
```bash
sudo systemctl stop crux-agent                    # pause (resumable)
sudo systemctl start crux-agent                   # resume the same session
rm ~/.crux/session-id && sudo systemctl restart crux-agent   # start over
```

### Option 2: Local Docker (Faster Test)
```dockerfile
FROM ubuntu:22.04
COPY linux/src/start-claude.sh /tmp/
RUN bash /tmp/start-claude.sh
COPY next-run-harness /home/ubuntu/
RUN chown -R ubuntu:ubuntu /home/ubuntu
USER ubuntu
WORKDIR /home/ubuntu
```

### Option 3: Existing Box (If Available)
```bash
# Install on running OpenClaw box
apt-get install -y python3 python3-pip
pip install anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
cd ~/next-run-harness
python3 run-claude-agent.sh . main-1
```

---

## Success Criteria

✓ Provisioning completes without errors
✓ Claude receives full PROMPT.md
✓ Claude responds coherently
✓ AGENTS.md / TOOLS.md / gate_artifact.sh all work
✓ Cost is tracked accurately
✓ 24h task produces usable output (notes, logs, artifacts)
✓ Comparison shows Claude is viable (quality, cost) or identifies gaps

---

## Fallback Plan

If Claude struggles:
- ✗ Quality is poor → Use OpenClaw; document lessons learned
- ✗ Cost is high → Use OpenClaw; maybe revisit later (pricing may improve)
- ✗ Friction is too high → Use OpenClaw; Claude is a learning exercise

**No shame in that.** Goal is to **evaluate**, not commit.

---

## Next Steps

1. **Operator decision:** Green-light smoke test? Or gather more info first?

2. **Provision:** Run `setup-device.sh` with start-claude.sh (or test locally).

3. **Single-turn test:** `~/run-claude-agent.sh . test-1` (30 min).

4. **Smoke test:** Run 24h real task (e.g., literature survey).

5. **Compare:** Run same task on OpenClaw; document findings.

6. **Decision:** Is Claude viable? For production? As fallback? Hybrid?

---

**Status:** Provisioning script complete. Ready for smoke test. Awaiting operator approval + ANTHROPIC_API_KEY.
