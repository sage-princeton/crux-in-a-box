# Operator Guide — product-change demo (AE-230)

This demo exercises the scaffold end to end, in two phases:

- **clarify:** a product manager agent takes an underspecified change request from Slack, clarifies it in the request's thread, and writes `REQUEST.md`. The `spec_written` gate checks the file.
- **implement:** it delegates the change to a Codex coding agent, verifies it and reports in the thread. The `site_tests_pass` and `verification_logged` gates decide when the phase is done.

## 1. Slack

- Create a Slack app with a bot token (`xoxb-…`) and these scopes: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `chat:write`, `users:read`. Invite the bot to the request channel.
- Give the scaffold the token as `SLACK_BOT_TOKEN` in its environment.

## 2. Resolve placeholders

| Placeholder | Files | Value |
|---|---|---|
| `{{SLACK_CHANNEL_ID}}` | `PROMPT.md`, `scaffold.toml`, `workspace/AGENTS.md` | ID (`C…`) of the request channel. It is also the only channel the bot may post to. **Required.** |

```bash
sed -i 's/{{SLACK_CHANNEL_ID}}/C0123456789/g' PROMPT.md scaffold.toml workspace/AGENTS.md
python -m crux_scaffold check --drop-in .    # lists any placeholder left, with file:line
```

## 3. Post the request, then run

Post this as a top-level message in the channel, as yourself rather than the bot:

> Can events show where they're happening?

It is deliberately underspecified: the demo expects the agent to ask before it builds. Start the run, then answer the agent's questions in the thread. For example: "Optional free-text venue. Show it on the listing and on each event page; leave it out when an event has none." If you do not answer within the `rest` budget (`[tools.rest] total_seconds`, 30 minutes), the agent proceeds on the defaults it offered.

## 4. What success looks like

- A clarifying question appears in the thread before `REQUEST.md` exists, and a summary appears at the end.
- `REQUEST.md` holds `- [ ]` acceptance criteria, and `LOG.md` has entries for the clarification, delegation and verification.
- `site/` gains the field and its tests.
- `.state/state.json` shows both phases with all gates passing.
- Langfuse has one `crux-run` trace in environment `<slug>`, tagged `run:<slug>` and `platform:openai-agents`. It contains:
  - one span per iteration (`clarify #1`, `implement #1`, …)
  - `gate:*` evaluations
  - the Codex `engineer` generation, with token usage
  - SDK spans for the product manager
