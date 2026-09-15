# AgentRQ integration

Local agent setup and tracing configuration. For EC2 deployments, see
[workspace provisioning](../src/ec2-workspaces/README.md).

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

Replace `WORKSPACE_ID` with the configured AgentRQ channel ID.

```
cd claude
claude --dangerously-load-development-channels server:agentrq-WORKSPACE_ID --name test
```

See: https://langfuse.com/integrations/developer-tools/claude-code

#### Langfuse hook

The project hook requires `uv`; its PEP 723 header declares the SDK dependency.

- `hooks/langfuse_hook.py`: standalone hook from the
  [Langfuse documentation](https://langfuse.com/integrations/developer-tools/claude-code).
  Supports `CC_LANGFUSE_STATE_DIR` and `CC_LANGFUSE_METADATA` for state location
  and workspace metadata.
- `settings.json`: registers the Stop hook through `${CLAUDE_PROJECT_DIR}`
  and stores state in the gitignored `.claude/state/` directory.
- `settings.local.json`: gitignored credentials; copy from its example file.
  Set `TRACE_TO_LANGFUSE` to the string `"true"` to enable tracing.

Enable only the standalone hook. Enabling the plugin as well registers both
hooks and can duplicate traces. The plugin also filters AgentRQ channel
prompts marked `isMeta: true`.

Set `CC_LANGFUSE_DEBUG=true` for diagnostics in
`claude/.claude/state/langfuse_hook.log`. An empty log can indicate that the
hook did not run or exited early.

When updating the vendored script, preserve the PEP 723 dependency header,
state-directory override and metadata support marked `LOCAL`.

### Codex

```
cd codex
OPENAI_API_KEY=API_KEY_XXXXX npx @agentrq/acp-gateway@latest --login --agent codex-acp
npx @agentrq/acp-gateway@latest --agent codex-acp
```

See: https://langfuse.com/integrations/developer-tools/codex

## Tasks

### Do now

- [ ] transfer langfuse access to org account (blocked by PK)

### Do next

- [ ] copy the run-harness directory to new boxes / configure similar setup
- [ ] Use Google Cloud for spend for agents, optional

### Backlog

- [ ] set up some basic monitoring / incident reporting to escalate suspicious activity to the team (with a high bar for suspicious)
- [ ] archive older openclaw architecture (`/linux` directory, parts of `/utils`, etc.)
- [ ] whitelist other collaborators' IPs
- [ ] tag langfuse traces somehow with an id / slug / etc.
- [ ] Add workspace deletion. Instance teardown retains the AgentRQ workspace
      and its valid MCP token. Decide whether deletion belongs in teardown
      or a separate controller command.
- [ ] spike on slack setup: https://agentrq.com/docs/integrations/slack-self-hosted
- [ ] update langfuse to v4 [?]
