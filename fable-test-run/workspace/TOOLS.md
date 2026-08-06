# TOOLS.md — Environment Truth

Single source of environment facts. `MEMORY.md` must NOT duplicate anything here — it links here. Subagents see this file: keep it accurate so every subagent doesn't rediscover the environment.

**Hour-0 duty (main agent):** verify every fact below against the live environment and correct this file where reality differs. Briefs and templates routinely carry stale environment facts; a wrong account ID left uncorrected costs days.

## Workspace & runtime

- Workspace path: `{{WORKSPACE_PATH}}`
- Host: `{{HOST_DESCRIPTION|macOS VM, arm64}}`
- Python: `{{PYTHON_SETUP|use uv + a pinned 3.11+ venv under code/; system python is old}}`
- Deliverable toolchain: `{{DELIVERABLE_TOOLCHAIN|LaTeX via tectonic + the NeurIPS 2026 template at templates/paper_template.zip (neurips_2026.tex/.sty + checklist.tex) — unzip into paper/ and build the Milestone-1 skeleton from it on day 1 (see PLAN.md milestone 1); complete checklist.tex before camera-ready}}`

## Accounts (verify, then keep current)

- **Telegram:** operator channel via OpenClaw; main session is the only session bound to it (see Footguns).
- **Cloud (GPU compute):** RunPod — cheap on-demand GPUs via the RunPod REST + GraphQL API. The API key is provided for this run in env `RUNPOD_API_KEY` (verify it works hour-0). You create and tear down pods yourself; the agent host is separate (see § Workspace & runtime). Your RunPod spend limit is {{CLOUD_SPEND_LIMIT}}. **Pod access is non-obvious — read § RunPod (GPU pods) before launching one.**
- **Git:** local only. `git` is installed with a commit identity configured; there is **no remote and no `gh` CLI**. Commit and tag exactly as `AGENTS.md` § Git discipline says — just never `push`. The operator pulls the repo off the box themselves.

## Deliberately absent in this run (do not try to route around them)

This run is a **harness smoke test** with three integrations removed at the provisioning layer. None of them is a broken resource, so **none of them is a Tier-3 blocker** — do not debug them, do not message the operator about them, do not spend turns looking for substitutes.

| Absent | What that means for you |
| --- | --- |
| **External reviewer platforms** (CMU, Stanford, refine.ink) | No `reviews/external/` milestone. Peer review is the internal blind rounds (`playbooks/review.md` §2a), the accompanying final review (§2c), the power critic (§2d), and the brief-fidelity check (§3) — all isolated subagents you cannot author. The Weak Accept bar stands; it is judged on those. |
| **GitHub** (`gh`, remotes, PATs) | Git is local. Commit often, tag milestone gates, never push. "Push at least hourly" reads as "commit at least hourly." |
| **Google / `gog` / Gmail** | No email retrieval of any kind. Nothing in the run depends on an inbox. |

Web search, web fetch, the literature APIs below, and Playwright all still work — lit work is unaffected.

## Literature search (use these, not manual web search)

Keyword sweeps and citation walks are single API calls — the `### COVERAGE` walks required by `playbooks/exploration.md` §2 are cheap here and lossy via general web search. Both APIs are free/keyless; on HTTP 429 back off a few seconds and retry.

```bash
# Semantic Scholar Graph API — keyword search
curl -s "https://api.semanticscholar.org/graph/v1/paper/search?query=QUERY+TERMS&fields=title,year,abstract,citationCount,externalIds&limit=20"
# Forward citation walk (who cites this paper) — accepts arXiv:<id>, DOI:<doi>, or S2 paper id
curl -s "https://api.semanticscholar.org/graph/v1/paper/arXiv:<ID>/citations?fields=title,year,citationCount,externalIds&limit=100"
# Backward walk (its references)
curl -s "https://api.semanticscholar.org/graph/v1/paper/arXiv:<ID>/references?fields=title,year,citationCount,externalIds&limit=100"
# arXiv API — metadata/abstract search
curl -s "http://export.arxiv.org/api/query?search_query=all:%22EXACT+PHRASE%22&max_results=20"
```

Full text: PDF at `https://arxiv.org/pdf/<id>`, LaTeX source at `https://arxiv.org/e-print/<id>` (the deep-read and exemplar steps in `playbooks/exploration.md` §2). OpenReview (`https://api2.openreview.net/notes/search?term=...`) has published reviews for venue papers — useful for seeing what reviewers pressed on in the closest prior work. Subagents doing lit work get this section via this file; point their briefs at the specific walk to run.

## Spend measurement

- **API:** `python3 scripts/telemetry_costs.py` — queries the cost-tracking service (Anthropic Admin API via Lambda). Canonical source of API spend. Never hand-estimate.
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
2. **There is no logs API.** `/v1/pods/<id>/logs` 404s and GraphQL `pod { logs }` is invalid — you cannot pull pod stdout through the API. Get results out by (a) SSHing in and reading them, or (b) having the job push its own output off the pod — `scp`/`rsync` back to the agent host is the default (AWS/S3 is not available), or a webhook / object store you've set up. **Design every job to ship its own results; never plan to read pod logs later.**

Launch discipline: one `create` call carrying `PUBLIC_KEY` + the `dockerStartCmd` above → SSH in to drive the job (or let it self-exfiltrate) → **terminate the pod the instant its results are off it.** Idle GPU pods bill continuously against the {{CLOUD_SPEND_LIMIT}} cap.

**Re-routing heuristic (don't let a stalled path cap ambition).** If the blessed path for a resource stalls — a GPU quota sitting unapproved, a pod type unavailable, a region out of capacity — **find another route**: a different RunPod GPU type or region, a smaller-but-real GPU now while the big one is pending, an alternate provider. A stalled quota is **not** a reason to run everything on CPU; the GPU budget is a target to deploy (`BRIEF.md` § Budgets), and a blocked compute path is a routing problem to solve, not a license to under-spend on depth.

## When commands hang (macOS)

Stall >30s → **screenshot first, debug second**: `/usr/sbin/screencapture -x <workspace>/screen.png`, inspect the image. Most "hangs" are permission/Gatekeeper/keychain dialogs waiting for a click.

- Click/type: `cliclick c:x,y` · `cliclick -w 0 t:"text"` (native auth dialogs need cliclick; osascript is blocked on them).
- App control / dialog buttons: `osascript -e 'tell application "System Events" to click button "X" of window 1 of process "Y"'`.
- Quarantined binaries: `xattr -d com.apple.quarantine <path>`.
- Keychain prompts: prefer file-based keyring backends so CLIs never touch the keychain.
- Analyze screenshots only from inside the workspace dir (not `/tmp`).

## Browser

Chrome via Playwright for web UIs and signups (no reviewer-platform submissions in this run — see § Deliberately absent). Screen Recording + Accessibility permissions are enabled.
