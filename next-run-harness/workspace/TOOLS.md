# TOOLS.md — Environment Truth

Single source of environment facts. `MEMORY.md` must NOT duplicate anything here — it links here. Subagents see this file: keep it accurate so every subagent doesn't rediscover the environment.

**Hour-0 duty (main agent):** verify every fact below against the live environment and correct this file where reality differs. Briefs and templates routinely carry stale environment facts; a wrong account ID left uncorrected costs days.

## Workspace & runtime

- Workspace path: `{{WORKSPACE_PATH}}`
- Host: `{{HOST_DESCRIPTION|macOS VM, arm64}}`
- Python: `{{PYTHON_SETUP|use uv + a pinned 3.11+ venv under code/; system python is old}}`
- Deliverable toolchain: `{{DELIVERABLE_TOOLCHAIN|LaTeX via tectonic + the NeurIPS 2026 template at templates/paper_template.zip (neurips_2026.tex/.sty + checklist.tex) — unzip into paper/ and build the Milestone-1 skeleton from it on day 1 (see PLAN.md milestone 1); complete checklist.tex before camera-ready}}`

## Accounts (verify, then keep current)

- **GitHub:** `gh` as `{{GITHUB_USER}}`
- **Email (review retrieval):** `gog` CLI (https://gogcli.sh) authenticated to the dedicated review Gmail. Find and read the CMU/Stanford review emails with `gog gmail` (e.g. `gog gmail list "in:inbox"`; see gogcli.sh for read/attachment syntax) and save each into `reviews/external/`. Confirm `gog` is authenticated hour-0 (`OPERATOR_GUIDE.md` Step 3); this inbox exists only to receive portal reviews.
- **Telegram:** operator channel via OpenClaw; main session is the only session bound to it (see Footguns).
- **Cloud (GPU compute):** RunPod — cheap on-demand GPUs via the RunPod REST + GraphQL API. The API key is provided for this run in env `RUNPOD_API_KEY` (verify it works hour-0). You create and tear down pods yourself; the agent host is separate (see § Workspace & runtime). Your RunPod spend limit is {{CLOUD_SPEND_LIMIT}}. **Pod access is non-obvious — read § RunPod (GPU pods) before launching one.**
- **External reviewers (`playbooks/review.md` §2b — submit the camera-ready PDF, save each returned review into `reviews/external/`):**
  1. **CMU Paper Reviewer** — portal `https://prometheus-eval.github.io/cmu-paper-reviewer/`. Submit via the browser (Chrome/Playwright, see § Browser); give the delivery address as the `gog`-authenticated review Gmail. The review comes back **by email** — retrieve it with the `gog` CLI (`gog gmail`; see § Email). Asynchronous: submission and result are decoupled — submit, then poll the inbox on a heartbeat cadence; never block waiting.
  2. **Stanford Agentic Reviewer** — portal `https://paperreview.ai/`. Same pattern: browser-submit with delivery to the review Gmail, then `gog gmail` to pull the emailed review.
  3. **refine.ink** — programmatic REST API; key in env `REFINE_INK_API_KEY`. Submit the PDF and fetch the review via the API — **read refine.ink's API docs hour-0 for the exact endpoints/shape and verify the key works before relying on it.** If it is metered/paid, run it LAST (the §2b ordering: paid single-shot reviews go after internal rounds are clean).
  Hour-0 dry run (do this before launch, per `OPERATOR_GUIDE.md` Step 3): confirm each portal accepts a submission, the email arrives at the review Gmail and is readable via `gog`, and the refine.ink key works. A reviewer that's broken when first needed mid-run is a Tier-3 block (`USER.md`).

## Spend measurement

- **API:** `python3 scripts/telemetry_costs.py` (telemetry at `{{TELEMETRY_PATH}}`). Canonical — it deduplicates by responseId; naive telemetry sums overcount severely. Never hand-estimate.
- **Cloud (RunPod):** RunPod has no Cost Explorer. Track spend as the drop in account credit since launch — record `clientBalance` at run start, then spend ≈ `start_balance − current clientBalance`. Query via the RunPod GraphQL API:

```
query { myself { clientBalance pods { id costPerHr runtime { uptimeInSeconds } } } }
```

For a live burn-rate, sum `costPerHr` over running pods. **Verify the exact field names against the RunPod API hour-0** and reconcile against the RunPod console once early. Idle pods bill while running, not just while in use — terminate them the moment a job's results are off the pod.

## OpenClaw footguns (follow exactly)

1. **Isolated cron sessions cannot deliver Telegram messages.** Any cron that must result in a message to the operator MUST be created with `sessionTarget: main` + payload kind `systemEvent` + `wakeMode: now`. An `agentTurn` payload lands in a cron-event session even when targeting main, and `sessions_send` from there returns `forbidden`.
2. **Self-chain convention.** Every harvest/self-chain one-shot cron carries, in its systemEvent text: the job name, the expected artifact path, and the `LOG.md` entry key (timestamp + title) of its pre-registered branch protocol — so the waking turn has full context even after compaction. Example: `HARVEST exp_07 (runs/exp_07/out.log) per LOG 2026-06-12 14:02 — execute the pre-registered branch.`
3. **Spawning subagents from cron turns** needs an explicit persistent `sessionTarget` (`session:<id>`), not `current` — the cron session isn't persistent. Prefer dispatching subagents from main-session turns; prefer background `nohup` processes for experiments.
4. **Background experiments:** launch with `nohup ... > runs/<exp>/out.log 2>&1 &`, write the PID to `runs/<exp>/pid`, and record both in the state capsule. The harvest reads the `.out` file — never a memory of what it should say.
5. **Subagent context:** subagents receive only `AGENTS.md` + `TOOLS.md` + the spawn prompt. Anything else they need goes in the brief (`playbooks/subagent.md`).

## RunPod (GPU pods) — follow exactly

GPU experiments run on RunPod pods (key in env `RUNPOD_API_KEY`). Two hard-won facts; ignoring either wastes a pod and burns budget:

1. **`runpod/pytorch:*-devel` images do not auto-start sshd.** To get a shell, set `dockerStartCmd` **at pod-create time** to: write `$PUBLIC_KEY` into `/root/.ssh/authorized_keys`, then launch `/usr/sbin/sshd -p 22`. `PUBLIC_KEY` must be in the pod's env **at create time** — PATCHing env on a live pod does **not** restart sshd, so you cannot add SSH access after the fact. If you forgot, recreate the pod; don't try to patch it.
2. **There is no logs API.** `/v1/pods/<id>/logs` 404s and GraphQL `pod { logs }` is invalid — you cannot pull pod stdout through the API. Get results out by (a) SSHing in and reading them, or (b) having the job push its own output off the pod — `scp`/`rsync` back to the agent host is the default (AWS/S3 is no longer available this run), or a webhook / object store you've set up. **Design every job to ship its own results; never plan to read pod logs later.**

Launch discipline: one `create` call carrying `PUBLIC_KEY` + the `dockerStartCmd` above → SSH in to drive the job (or let it self-exfiltrate) → **terminate the pod the instant its results are off it.** Idle GPU pods bill continuously against the {{CLOUD_SPEND_LIMIT}} cap.

## When commands hang (macOS)

Stall >30s → **screenshot first, debug second**: `/usr/sbin/screencapture -x <workspace>/screen.png`, inspect the image. Most "hangs" are permission/Gatekeeper/keychain dialogs waiting for a click.

- Click/type: `cliclick c:x,y` · `cliclick -w 0 t:"text"` (native auth dialogs need cliclick; osascript is blocked on them).
- App control / dialog buttons: `osascript -e 'tell application "System Events" to click button "X" of window 1 of process "Y"'`.
- Quarantined binaries: `xattr -d com.apple.quarantine <path>`.
- Keychain prompts: prefer file-based keyring backends so CLIs never touch the keychain.
- Analyze screenshots only from inside the workspace dir (not `/tmp`).

## Browser

Chrome via Playwright for web UIs, signups, and reviewer-platform submissions. Screen Recording + Accessibility permissions are enabled.
