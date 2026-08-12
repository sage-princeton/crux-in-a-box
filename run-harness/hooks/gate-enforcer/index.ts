/**
 * gate-enforcer — OPTIONAL reference plugin that turns the cooperative
 * gate_artifact.sh SHIP checks into runtime vetoes (see ./README.md).
 *
 * The default is COOPERATIVE gates honored by the agent, not hard enforcement.
 * The isolated critics' own verdict files are the evidence and the agent is
 * trusted to run the gate and honor its exit code. This plugin exists only as an
 * OPTIONAL ship-time backstop: it makes the three SHIP gates non-bypassable at
 * the ship / completion-report boundary for operators who want a belt-and-
 * suspenders stop.
 *
 * ⚠️ REFERENCE, NOT DROP-IN. Event names, the `api.on` signature, the ctx field
 * names (sessionKey/content), and the block/cancel return shapes follow
 * docs.openclaw.ai/plugins/hooks at time of writing. VERIFY against your pinned
 * OpenClaw before relying on it; the enforcement strategy is the point, the exact
 * API may need a one-line fix. Design rule: when ctx shape is unrecognized, FAIL
 * CLOSED (run the gate) rather than silently passing.
 *
 * Layers (ship only):
 *   before_tool_call  -> veto the SHIP wrapper command if the ship gates fail
 *   message_sending   -> cancel the completion-report message if the ship gates fail
 *
 * NOTE: this only RUNS gate_artifact.sh and honors its exit code — it inherits
 * whatever the gate does. The substance checks (read the isolated power critic's
 * own artifact, the final review, and locks/budget.json directly) live in
 * gate_artifact.sh; this plugin makes them non-bypassable at the ship boundary.
 */

import { execFileSync } from "node:child_process";

// --- knobs to tune for your workspace (see README) -------------------------
const WORKSPACE = process.env.OPENCLAW_WORKSPACE || `${process.env.HOME}/.openclaw/workspace`;
const PDF = "paper/main.pdf";
// The single command that performs the actual ship (build+submit). Make this the
// ONLY allowlisted ship path (exec-approvals) and deny raw build/submit. The
// routine latex build is intentionally NOT gated here — only the ship wrapper.
const SHIP_COMMAND = /(^|\/|\s)ship\.sh\b/;
// Matches the completion-report body so daily snapshots / Tier-3 pings are NOT
// blocked. Align with USER.md § Status updates (the report's header line).
const COMPLETION_MARKER = /completion report|COMPLETION REPORT/;

// SHIP gate flags — keep in lockstep with PLAN.md milestone-table gate commands
// and the three ship gates in scripts/gate_artifact.sh. This enforcer guards
// SHIP only.
const SHIP_FLAGS = {
  REQUIRE_EXTERNAL_REVIEWS: "1",
  REQUIRE_EVIDENCE_ADEQUATE: "1",
  REQUIRE_SHIP_AUTHORIZATION: "1",
};

// --- gate runner -----------------------------------------------------------
function runGate(flags: Record<string, string>): { ok: boolean; output: string } {
  try {
    const out = execFileSync("bash", ["scripts/gate_artifact.sh", PDF, "paper"], {
      cwd: WORKSPACE,
      env: { ...process.env, ...flags },
      timeout: 115_000,
      encoding: "utf8",
    });
    return { ok: true, output: String(out) };
  } catch (e: any) {
    // non-zero exit (GATE FAIL) OR an exec error -> fail closed
    return { ok: false, output: String(e?.stdout || "") + String(e?.stderr || e?.message || "") };
  }
}
function failing(output: string): string {
  const lines = output.split("\n").filter((l) => l.startsWith("GATE FAIL"));
  return (lines.length ? lines.join("\n") : output.slice(-800)).trim();
}

// --- plugin entry ----------------------------------------------------------
export default function register(api: any) {
  // 1) Hard veto: the SHIP wrapper command cannot run unless the ship gates pass.
  api.on(
    "before_tool_call",
    async (ctx: any) => {
      const cmd = String(ctx?.params?.command ?? ctx?.command ?? ctx?.params?.cmd ?? "");
      if (!SHIP_COMMAND.test(cmd)) return { block: false };
      const res = runGate(SHIP_FLAGS);
      if (res.ok) return { block: false };
      return { block: true, reason: "gate-enforcer: ship blocked — ship gates failed:\n" + failing(res.output) };
    },
    { priority: 100 },
  );

  // 2) Cancel the completion-report message if the ship gates fail.
  api.on(
    "message_sending",
    async (ctx: any) => {
      // body may live under content or text depending on surface; fail closed on empty.
      const body = String(ctx?.content ?? ctx?.message?.content ?? ctx?.text ?? ctx?.message?.text ?? "");
      const looksLikeReport = COMPLETION_MARKER.test(body);
      if (body && !looksLikeReport) return {}; // a normal snapshot/ping -> allow
      const res = runGate(SHIP_FLAGS); // empty body -> enforce anyway (fail closed)
      if (res.ok) return {};
      return {
        cancel: true,
        reason: "gate-enforcer: completion report withheld — ship gates failed:\n" + failing(res.output),
      };
    },
    { priority: 100 },
  );
}
