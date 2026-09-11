## TODOs

### Do now

- [ ] stabilize AWS setup - add add'l workspaces and tear them down with no issues (manually - no coding agent use)
- [ ] configure https access for AgentRQ

### Backlog

- [ ] tag langfuse traces somehow with an id / slug / etc.
- [ ] ensure we can set model types and thinking levels when making new boxes!
- [ ] reconsider workspace lifecycle: workspaces outlive their boxes
      `teardown.sh` removes the instance, the Elastic IP and the ssh alias, but
      deliberately never touches the control plane — so a torn-down box leaves
      its AgentRQ workspace behind with `agentConnected: false` and a **live
      365-day MCP token**. After the Sept 11 teardown, `crux-codex-1`
      (`0iTbHcPsc6L`) and `codex-2` (`0iTmD5Wz8sL`) are both still there.
      Correct behavior as written, not a bug: teardown staying out of the
      control plane is what makes it safe to run. But repeated
      provision/teardown cycles accumulate orphaned workspaces and valid
      tokens, and `bootstrap-workspace.sh` has `--list` but no delete.
      Add a `--delete` there (and decide whether teardown should call it, or
      whether unpicking the control plane stays a separate deliberate act)
      once the cycle is routine rather than occasional.
- [ ] spike on slack setup: https://agentrq.com/docs/integrations/slack-self-hosted
- [ ] update langfuse to v4 [?]

### Done

- [x] set up langfuse on codex
      Working. Ingest latency is ~17s; the apparent delay is that the plugin
      only exports at end of turn.
- [x] set up langfuse on claude
      Uses the docs' standalone hook, **not** the plugin. The plugin skips user
      rows with `isMeta: true` and every AgentRQ channel prompt is one, so it
      emits nothing here. The standalone script has no such check.
- [x] work on cloud/AWS setup for codex, then for claude
- [x] ensure secrets/config is working - three levels: (a) system-wide in SSM,
      e.g., langfuse creds (b) per-box config, not secret, e.g., model type
      (c) per-box config, secret, e.g., OpenAI API Key
      All three verified on a real second box (`crux-codex-2`, Sept 11).
      (a) `/crux/system/env`, one SecureString for the whole fleet, read via
      `crux-system-role` whose only privilege is `GetParameter` on that ARN —
      so rotating Langfuse is one upload, not a per-box edit.
      (b) `CODEX_MODEL` / `CODEX_REASONING_EFFORT` in `placeholders-<slug>.txt`;
      proven by running box 2 at `medium` while box 1 was at `high`.
      (c) `run-secrets-<slug>.json` scp'd at launch and **deleted on the box**
      after configure. Per-box secrets never reach AWS: no per-box SSM
      parameter, no per-box IAM role, so teardown leaves nothing to forget.
      The legacy `/crux/run/<slug>/env` params are gone.
      Non-obvious thing this shook out: **codex ignores `OPENAI_API_KEY` in the
      environment.** It reads `~/.codex/auth.json`, and without it sends no
      auth header at all — `401 ... Missing bearer`, which reads like a revoked
      key. Box 1 only worked because someone had run the login by hand, so
      every scripted box would have been dead on arrival. `configure-run.sh`
      now runs `codex login --with-api-key` itself. See `src/README.md`
      ("Secrets and config — three tiers") for the table.

## Setup

### Backend

```
docker run -d \
    --name agentrq \
    --restart unless-stopped \
    -p 2026:2026 \
    --env-file .env \
    -v ./_storage:/_storage \
    agentrq/agentrq:latest
```

### Claude

```
cd claude
claude --dangerously-load-development-channels server:agentrq-0iQGyKJhRkP --name test
<!-- FIXME: we want yolo to be enabled here, too -->
```

See: https://langfuse.com/integrations/developer-tools/claude-code

#### Langfuse (standalone hook — project-local, nothing machine-global)

Everything lives under `claude/.claude/`, so a fresh clone on an AWS box is
already wired. Only requirement on the box is `uv`; the hook's PEP 723 header
resolves the SDK itself, no venv to provision.

- `hooks/langfuse_hook.py` — vendored from the docs. Two changes, both marked
  `LOCAL` in its docstring: the PEP 723 header, and `STATE_DIR` honouring
  `CC_LANGFUSE_STATE_DIR`. Re-syncing upstream means re-applying both.
- `settings.json` — registers the `Stop` hook via `${CLAUDE_PROJECT_DIR}` and
  points state at `.claude/state/` (gitignored). Checked in; no secrets.
- `settings.local.json` — keys, gitignored, see `.example`.
  `TRACE_TO_LANGFUSE` must be the literal string `"true"`; the hook exits
  silently otherwise, which is also how it stays off elsewhere.

Do **not** also enable the Langfuse plugin: hooks merge across settings levels,
so both would fire and double-emit. (The plugin can't trace this setup anyway —
see TODOs.)

Debug: `CC_LANGFUSE_DEBUG=true`, log at `claude/.claude/state/langfuse_hook.log`.
An empty log after a session means the hook never ran or exited early.

Re-vendor the script:

````
curl -sL https://langfuse.com/integrations/developer-tools/claude-code.md \
  | sed -n '/^#!\/usr\/bin\/env python3$/,/^```$/p' | sed '$d' \
  > claude/.claude/hooks/langfuse_hook.py
````

### Codex

```
cd codex
OPENAI_API_KEY=API_KEY_XXXXX npx @agentrq/acp-gateway@latest --login --agent codex-acp
npx @agentrq/acp-gateway@latest --agent codex-acp
```

See: https://langfuse.com/integrations/developer-tools/codex
