# Operator Guide — product-change demo (AE-230)

This demo exercises the scaffold end to end, in two phases:

- **clarify:** a product manager agent takes an underspecified change request from Slack, clarifies it in the request's thread, and writes `REQUEST.md`. The `spec_written` gate checks the file.
- **implement:** it delegates the change to a Codex coding agent, verifies it and reports in the thread. The `site_tests_pass` and `verification_logged` gates decide when the phase is done.

## 1. Slack

- Create the app from [`slack-app-manifest.yaml`](slack-app-manifest.yaml): at <https://api.slack.com/apps>, choose **Create New App → From a manifest**, pick the workspace and paste the file. It requests only the bot scopes the Slack MCP server needs: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `chat:write`, `users:read`.
- Install it to the workspace and copy the **Bot User OAuth Token** (`xoxb-…`) from **OAuth & Permissions**. Give it to the scaffold as `SLACK_BOT_TOKEN`.
- Invite the bot to the request channel (`/invite @crux-pm`) and copy the channel's ID (`C…`) from its details.

## 2. Resolve placeholders

`PROMPT.md` is the `clarify` phase's first prompt and is sent to the agent verbatim, so operator notes stay in this guide.

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
- Langfuse environment `<slug>` has one trace per iteration (`clarify #1`, `implement #1`, …) in session `<slug>`, tagged `run:<slug>` and `platform:openai-agents`. Each trace contains:
  - SDK spans for the product manager
  - `gate:*` evaluations
  - in `implement`, the Codex `engineer` generation with its token usage, and a `codex:*` child for each command, file change and message
