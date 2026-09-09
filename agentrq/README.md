## TODOs

### Do now

- [ ] ensure secrets/config is working - three levels: (a) system-wide in SSM, e.g., langfuse creds (b) per-box config, not secret, e.g., model type (c) per-box config, secret, e.g., OpenAI API Key
- [ ] stabilize AWS setup - add add'l workspaces and tear them down with no issues
- [ ] tag langfuse traces somehow with an id / slug / etc.

### Backlog

- [ ] ensure we can set model types and thinking levels when making new boxes!
- [ ] configure https access for AgentRQ
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
