## TODOs

- [x] set up langfuse on codex
      Working, just delayed ~10 minutes!
- [ ] set up langfuse on claude
- [ ] work on cloud/AWS setup for codex, then for claude

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
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
claude --dangerously-load-development-channels server:agentrq-0iQGyKJhRkP --name test
<!-- FIXME: we want yolo to be enabled here, too -->
```

See: https://langfuse.com/integrations/developer-tools/claude-code

### Codex

```
cd codex
OPENAI_API_KEY=API_KEY_XXXXX npx @agentrq/acp-gateway@latest --login --agent codex-acp
npx @agentrq/acp-gateway@latest --agent codex-acp
```

See: https://langfuse.com/integrations/developer-tools/codex
