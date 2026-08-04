# env.sh — run configuration + secrets for the Codex outer loop.
# Populated from linux/placeholders-codex-<box>.txt by setup-codex.sh.
# SECRETS LIVE HERE after resolution — this file stays on the box, chmod 600,
# and is never committed (the repo copy holds only placeholder tokens).

# ── Paths (resolved by setup-codex.sh) ──────────────────────────────────
export LOOP_DIR="{{LOOP_DIR}}"
export WORKSPACE_DIR="{{WORKSPACE_PATH}}"

# ── Logging / telemetry ─────────────────────────────────────────────────
# Codex writes rich per-thread rollouts to $CODEX_HOME/sessions (transcripts,
# reasoning, tool calls, token usage, ultra subagents) and internal logs to
# $CODEX_HOME/log. RUST_LOG sets the internal-log verbosity (info is useful
# without being noisy; debug for deep triage). The run indexes each iteration's
# rollouts live (loop/bin/index_iteration.py) and bundles everything at run end
# (loop/collect_telemetry.sh).
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export RUST_LOG="${RUST_LOG:-info}"

# ── Secrets ─────────────────────────────────────────────────────────────
export OPENAI_API_KEY="{{OPENAI_API_KEY}}"
# Optional: an OpenAI ADMIN key turns on exact spend measurement via the
# organization costs API (else the loop prices Codex session token counts).
export OPENAI_ADMIN_KEY="{{OPENAI_ADMIN_KEY|}}"
export OPENAI_PROJECT_ID="{{OPENAI_PROJECT_ID|}}"
export RUNPOD_API_KEY="{{RUNPOD_API_KEY|}}"
export REFINE_INK_API_KEY="{{REFINE_INK_API_KEY|}}"

# ── Model ───────────────────────────────────────────────────────────────
# "GPT-5.6 Sol Ultra" = model gpt-5.6-sol + Codex effort "ultra" (there is no
# *-ultra model ID). Codex effort tiers: minimal|low|medium|high|xhigh|max|ultra;
# ultra sits above max and enables proactive multi-agent coordination.
export CODEX_MODEL="{{CODEX_MODEL|gpt-5.6-sol}}"
export CODEX_REASONING_EFFORT="{{CODEX_REASONING_EFFORT|ultra}}"
export VERIFIER_REASONING_EFFORT="{{VERIFIER_REASONING_EFFORT|high}}"

# ── Budgets & cutoffs (the loop's stop conditions) ──────────────────────
export API_BUDGET_USD="{{API_BUDGET_USD|3000}}"
export CLOUD_SPEND_LIMIT_USD="{{CLOUD_SPEND_LIMIT_USD|500}}"
export DEADLINE_HOURS="{{DEADLINE_HOURS|144}}"        # set at launch, from launch time
export API_STOP_FRACTION="{{API_STOP_FRACTION|0.98}}" # stop when spend ≥ this × budget
export POLISH_BUDGET_FRACTION="{{POLISH_BUDGET_FRACTION|0.92}}"
export FINAL_POLISH_HOURS="{{FINAL_POLISH_HOURS|4}}"

# ── Loop tuning ─────────────────────────────────────────────────────────
export ITERATION_TIMEOUT_MIN="{{ITERATION_TIMEOUT_MIN|180}}"  # per-session wall-clock cap (also the wedge backstop); a session may sit and wait on a background job up to this
export VERIFIER_TIMEOUT_MIN="{{VERIFIER_TIMEOUT_MIN|30}}"
export LOOP_PAUSE_SECONDS="{{LOOP_PAUSE_SECONDS|20}}"
export MAX_FAST_FAILURES="{{MAX_FAST_FAILURES|6}}"    # consecutive <60s exits before long backoff

# ── Token pricing (session-scan fallback; USD per 1M tokens) ────────────
# Set these to the actual rates for CODEX_MODEL. Ignored when OPENAI_ADMIN_KEY
# is set (the costs API is authoritative).
export OPENAI_INPUT_USD_PER_MTOK="{{OPENAI_INPUT_USD_PER_MTOK|1.25}}"
export OPENAI_CACHED_INPUT_USD_PER_MTOK="{{OPENAI_CACHED_INPUT_USD_PER_MTOK|0.125}}"
export OPENAI_OUTPUT_USD_PER_MTOK="{{OPENAI_OUTPUT_USD_PER_MTOK|10.00}}"
