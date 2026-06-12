# TOOLS.md — Environment Truth

Single source of environment facts. `MEMORY.md` must NOT duplicate anything here — it links here. Subagents see this file: keep it accurate so every subagent doesn't rediscover the environment.

**Hour-0 duty (main agent):** verify every fact below against the live environment and correct this file where reality differs. Briefs and templates routinely carry stale environment facts; a wrong account ID left uncorrected costs days.

## Workspace & runtime

- Workspace path: `{{WORKSPACE_PATH}}`
- Host: `{{HOST_DESCRIPTION|macOS VM, arm64}}`
- Python: `{{PYTHON_SETUP|use uv + a pinned 3.11+ venv under code/; system python is old}}`
- Deliverable toolchain: `{{DELIVERABLE_TOOLCHAIN|LaTeX via tectonic + venue template — installed and building from day 1, see PLAN.md milestone 1}}`

## Accounts (verify, then keep current)

- **GitHub:** `gh` as `{{GITHUB_USER}}`; project remote `{{GITHUB_REMOTE}}`.
- **Email:** `gog` CLI (`gog gmail list "in:inbox"`).
- **Telegram:** operator channel via OpenClaw; main session is the only session bound to it (see Footguns).
- **Cloud:** `{{CLOUD_ACCOUNT_DETAILS}}` — credentials type, account id, region, spend cap. (Operator pre-approves quotas before launch; if a quota is 0, that's a Tier-3 setup defect, not yours to wait on silently.)
- **External reviewers:** {{EXTERNAL_REVIEWER_ACCESS|platform → how to submit → quota, one line each}}.

## Spend measurement

- **API:** `python3 scripts/telemetry_costs.py` (telemetry at `{{TELEMETRY_PATH}}`). Canonical — it deduplicates by responseId; naive telemetry sums overcount severely. Never hand-estimate.
- **Cloud:** `{{CLOUD_SPEND_COMMAND}}`.

## OpenClaw footguns (follow exactly)

1. **Isolated cron sessions cannot deliver Telegram messages.** Any cron that must result in a message to the operator MUST be created with `sessionTarget: main` + payload kind `systemEvent` + `wakeMode: now`. An `agentTurn` payload lands in a cron-event session even when targeting main, and `sessions_send` from there returns `forbidden`.
2. **Self-chain convention.** Every harvest/self-chain one-shot cron carries, in its systemEvent text: the job name, the expected artifact path, and the `LOG.md` entry key (timestamp + title) of its pre-registered branch protocol — so the waking turn has full context even after compaction. Example: `HARVEST exp_07 (runs/exp_07/out.log) per LOG 2026-06-12 14:02 — execute the pre-registered branch.`
3. **Spawning subagents from cron turns** needs an explicit persistent `sessionTarget` (`session:<id>`), not `current` — the cron session isn't persistent. Prefer dispatching subagents from main-session turns; prefer background `nohup` processes for experiments.
4. **Background experiments:** launch with `nohup ... > runs/<exp>/out.log 2>&1 &`, write the PID to `runs/<exp>/pid`, and record both in the state capsule. The harvest reads the `.out` file — never a memory of what it should say.
5. **Subagent context:** subagents receive only `AGENTS.md` + `TOOLS.md` + the spawn prompt. Anything else they need goes in the brief (`playbooks/subagent.md`).

## When commands hang (macOS)

Stall >30s → **screenshot first, debug second**: `/usr/sbin/screencapture -x <workspace>/screen.png`, inspect the image. Most "hangs" are permission/Gatekeeper/keychain dialogs waiting for a click.

- Click/type: `cliclick c:x,y` · `cliclick -w 0 t:"text"` (native auth dialogs need cliclick; osascript is blocked on them).
- App control / dialog buttons: `osascript -e 'tell application "System Events" to click button "X" of window 1 of process "Y"'`.
- Quarantined binaries: `xattr -d com.apple.quarantine <path>`.
- Keychain prompts: prefer file-based keyring backends so CLIs never touch the keychain.
- Analyze screenshots only from inside the workspace dir (not `/tmp`).

## Browser

Chrome via Playwright for web UIs, signups, and reviewer-platform submissions. Screen Recording + Accessibility permissions are enabled.
