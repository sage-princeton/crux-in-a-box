# gate-enforcer — OPTIONAL ship-time backstop

**The default posture is cooperative gates honored by the agent, not hard enforcement.** The `scripts/gate_artifact.sh` checks are cooperative: the agent is trusted to run the gate and honor its exit code, and the substance is anchored in artifacts the agent can't author (the isolated power critic's own verdict file, the final review, `locks/budget.json`) — so there is no self-typed token to game. This plugin is **optional**. It exists for operators who want a belt-and-suspenders stop: it turns the three **ship** gates into **runtime vetoes** so a completion / ship cannot slip through even if the agent forgets to run the gate.

It guards **ship only**. Drafting is gated cooperatively by the isolated exploration-sufficiency critic's `reviews/*.md` verdict (playbooks/exploration.md), not by a flag this plugin could enforce. The ship gates it runs:

- `REQUIRE_EXTERNAL_REVIEWS` — an external-review artifact exists in `reviews/external/`.
- `REQUIRE_EVIDENCE_ADEQUATE` — the ship-time power critic's `reviews/power_critic_ship.md` exists and reports an `ADEQUATE` verdict with `seeds≥seed_floor`/`cells≥cell_floor` (floors from `locks/evidence_floors.json`); the headline must be powered, not a single cell.
- `REQUIRE_SHIP_AUTHORIZATION` — a light under-spend backstop: ships if `reviews/final_review.md` records Weak Accept+, **or** `locks/budget.json` shows a cap genuinely (near-)exhausted, **or** `LOG.md` carries an honest ship/under-spend justification memo. No below-bar ship while budget/time visibly remain.

> **This is a reference implementation, not a drop-in.** The OpenClaw plugin-hook API (event names, handler signature, return shapes) is taken from `docs.openclaw.ai/plugins/hooks` as of this writing. **Verify every name against your pinned OpenClaw version before relying on it**, and adapt the trigger detection (below) to your workspace. The *enforcement strategy* is the deliverable; the exact API calls may need a one-line fix.

## What it does (two layers, both ship-time)

1. `before_tool_call` — vetoes the **ship wrapper command** (`SHIP_COMMAND`, default `ship.sh`) unless the ship gates pass. Make `ship.sh` the only allowlisted ship path (exec-approvals below) and deny raw submit; then this is a true, phase-independent hard stop that does **not** depend on agent-authored PLAN.md text. The routine latex build is intentionally *not* gated (you build to review many times).
2. `message_sending` — on an outbound **completion report** (the ship signal), runs the ship gate and returns `cancel: true` if it fails. Body is read from `content` first (then `text`); an **empty/unknown body fails closed** (the gate runs anyway) so a misnamed field can't slip the report through. Routine snapshots/Tier-3 pings (non-matching body) are allowed.

`SHIP_FLAGS` is kept in lockstep with the `PLAN.md` milestone-table ship-gate commands and the three ship gates in `gate_artifact.sh` — update them together.

## Trigger detection (tune this)

- `SHIP_COMMAND` (regex) — the wrapper that performs the real ship. The strongest setup pairs this with exec-approvals so the wrapper is the *only* way to ship; then layer 1 is phase-independent and does the real work.
- `COMPLETION_MARKER` (regex) — matches the completion-report body so snapshots/Tier-3 pings are not blocked. Align with `USER.md` § Status updates.
- `OPENCLAW_WORKSPACE` — set this env var to your provisioning workspace path; the default `$HOME/.openclaw/workspace` assumes the standard layout.

## Wiring into `openclaw.json` (provisioning step — `create-new-crux-box.sh`)

```jsonc
{
  "plugins": {
    "entries": {
      "gate-enforcer": {
        "path": "<workspace>/hooks/gate-enforcer/index.ts",
        "allowConversationAccess": true,   // message body access for COMPLETION_MARKER
        "hooks": { "timeoutMs": 120000 }    // gate_artifact.sh runs python + telemetry
      }
    }
  }
}
```

Set the workspace path your provisioning uses (`~/.openclaw/workspace`). Because `openclaw.json` and `create-new-crux-box.sh` live in the provisioning repo (not this one), this is an **operator** step, done once before launch — and an optional one: skip it entirely if you want the pure cooperative posture.

## Exec-approval backstop (optional, strongest)

In `~/.openclaw/exec-approvals.json`, force the ship path through a wrapper and deny bypasses:

```jsonc
{
  "agents": {
    "<agent-name>": {
      "allowlist": ["bash scripts/ship.sh*"],   // ship.sh runs the gate, then builds/submits
      "denylist": ["latexmk*", "pdflatex*", "tectonic*"]  // raw build cannot bypass the wrapper
    }
  }
}
```

(denylist beats allowlist). Provide `scripts/ship.sh` that runs `REQUIRE_EVIDENCE_ADEQUATE=1 REQUIRE_SHIP_AUTHORIZATION=1 REQUIRE_EXTERNAL_REVIEWS=1 scripts/gate_artifact.sh <pdf>` and only proceeds on exit 0.

## Hard caps OpenClaw does NOT give you

OpenClaw has **no native API-spend ceiling**. Set a hard spend limit in your provider console (Anthropic: Settings → Limits; OpenAI: platform.openai.com/settings/organization/limits) as an independent backstop — it is the only real hard `$` cap. Cron can wake the agent to re-check a gate but cannot force compliance — always pair a "must happen" cron with this plugin.
