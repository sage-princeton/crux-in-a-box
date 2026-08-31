# telemetry-hal — pinned plugin patch + config block

**Why this exists.** The fable run's telemetry audit found that the
[openclaw-telemetry-hal](https://github.com/schwartzadev/openclaw-telemetry-hal)
plugin ran for the whole run with its service never started: `start.sh` linked the
plugin but never wrote `plugins.entries.telemetry-hal.config`, so the gate in
`service.ts` returned silently and only the raw `appendFileSync` fallback in
`index.ts` ran — no timestamps, no `seq`, no redaction, no rotation, no `llm.usage`
(audit §3.1, §3.4, §3.5). `agent_end` was refused by the gateway because
`hooks.allowConversationAccess` must sit at the *entry* level (§3.2; putting it
inside `config` fails schema validation and disables the whole plugin). The
deprecated `before_agent_start` hook fired twice per run and copied the full
context each time — 552 of 556 MB (§3.3). Had the service been enabled, the
fallback would have double-written every event unredacted onto the same path
(§3.6), and `llm.usage` would *still* have been zero: the gateway emits
`model.usage` as a trusted diagnostic event and the public `onDiagnosticEvent`
filters trusted events out (verify_code.md). `telemetry-hal.patch` fixes the
class of each: the fallback becomes a real fallback (only when the service is not
started; stamped, default-redacted, `fallback:true`, loud in the gateway log);
redaction is on whenever the service starts unless `redact.enabled === false`,
and an uncompilable pattern list falls back to the defaults instead of taking the
pipeline down; `agent.start` comes from `before_prompt_build` (fires once) with
`before_agent_start` deduped by `runId`, and both `agent.start`/`agent.end` log a
per-session **delta** (`newMessages`, capped at 50) instead of the history;
`llm.usage` subscribes through `onInternalDiagnosticEvent` from
`openclaw/plugin-sdk/diagnostic-runtime` (a guarded dynamic import, so the plugin
still loads on an SDK without the subpath; if only the public listener can be
installed the `llm_output` hook supplies the same token counts, minus cost);
`tool.*` carry `toolCallId`, `message.*` carry the routing envelope; the default
redaction list learns the harness's key shapes (`sk-ant-`, `sk-or-v1-`, `rpa_`,
Telegram, `github_pat_`, `hf_`, AWS, PEM, labelled 40–64-hex tokens); and the
shipped `service.test.ts` uses the right entry key so `vitest` exercises the real
gate. `before_prompt_build`/`before_agent_start` do **not** expose the assembled
system prompt (their event is `{prompt, messages}`); `llm_input` does
(`systemPrompt?`), so the patch logs `systemPromptSha256`/`systemPromptLength` on
every `llm.input` line and the full text once per session as `system.prompt`
whenever the hash changes. `llm_input`, `llm_output` and `agent_end` are all
"conversation" hooks — they only register when the block's
`hooks.allowConversationAccess: true` is present.

**What `start.sh` does with it.** Instead of cloning upstream `HEAD`, it clones and
checks out the sha in `UPSTREAM_COMMIT`, then runs `git apply --check` /
`git apply telemetry-hal.patch` **before** `pnpm install && pnpm run build`, and
aborts provisioning loudly if the patch does not apply (a moved upstream is a
deliberate re-pin, not something to paper over at boot). After `openclaw plugins
install --link .` it merges `openclaw_plugins_block.json` into
`~/.openclaw/openclaw.json` under `.plugins.entries`, overriding three literal
defaults with placeholders: `.config.filePath` (the box's state dir),
`.config.rotate.maxSizeBytes` from `{{TELEMETRY_ROTATE_MAX_BYTES|104857600}}` and
`.config.rotate.maxFiles` from `{{TELEMETRY_ROTATE_MAX_FILES|50}}` (see
`linux/placeholders.txt.example`). Everything else in the block is literal:
`config.enabled`, the full `redact.patterns` list (it *replaces* the plugin
defaults, hence the whole list), `integrity`/`rateLimit` off, and
`hooks.allowConversationAccess` at the entry level. Once rotation is on, anything
that collects telemetry must take `telemetry.jsonl*`, not just the live file.

**How to re-verify (and how to re-pin).** Everything needed is in this
directory plus a clone of upstream; nothing depends on any machine's scratch
files. On a dev machine (Node 20+, pnpm, git):

1. Clone upstream at the pin and apply the patch — exactly what `start.sh` does:
   ```sh
   HAL=<repo>/linux/src/telemetry-hal
   git clone https://github.com/schwartzadev/openclaw-telemetry-hal hal && cd hal
   git checkout --detach "$(tr -d '[:space:]' < "$HAL/UPSTREAM_COMMIT")"
   git apply --check "$HAL/telemetry-hal.patch" && git apply "$HAL/telemetry-hal.patch"
   ```
   `git apply --check` failing means upstream moved under the patch: re-pin
   (step 4), never build unpatched.
2. Build and test as the box does: `pnpm install && pnpm run build` (the
   upstream `tsconfig` is strict, so a clean build is the type check) and
   `pnpm test` (the patched `service.test.ts` exercises the real config gate).
   `dist/index.js` and `dist/src/service.js` must be newer than their sources;
   `dist/src/service.js` must still contain the dynamic
   `import("openclaw/plugin-sdk/diagnostic-runtime")`.
3. One-heartbeat smoke test on a box (or any machine with the gateway):
   `openclaw plugins install --link .`, merge `openclaw_plugins_block.json`
   into `~/.openclaw/openclaw.json` under `.plugins.entries` (start.sh's
   TELEMETRY CONFIG step; by hand:
   `jq --slurpfile b openclaw_plugins_block.json '.plugins.entries += $b[0]' openclaw.json`),
   restart the gateway, trigger one heartbeat (or send one message), then:
   - `journalctl --user -u openclaw-gateway --no-pager | grep -E 'telemetry:|agent_end blocked'`
     must show `telemetry: <path>`, `telemetry: redaction enabled` and
     `telemetry: llm.usage from the internal diagnostic bus`, and **no**
     `agent_end blocked` line;
   - `telemetry.jsonl` must have gained exactly one `agent.start` carrying
     `newMessageCount`, one `llm.input` (with `systemPromptSha256`), one
     `agent.end`, and one `llm.usage` with `source:"model.usage"` — every line
     with `seq` and `ts`, none with `fallback:true`;
   - `utils/scan-secrets.py ~/.openclaw/logs/telemetry.jsonl` is only a class
     scan; the plugin's redaction is pattern-based, so a box literal that looks
     like nothing still needs `utils/export-run.sh`'s blacklist pass.
4. Re-pinning to a newer upstream: `git checkout --detach <new-sha>`,
   `git apply --3way "$HAL/telemetry-hal.patch"`, resolve, run steps 2–3, then
   `git diff > "$HAL/telemetry-hal.patch"` (a/ b/ prefixes, as `git apply`
   expects) and write the new sha into `UPSTREAM_COMMIT`. The patch must touch
   only tracked files, or `start.sh`'s `git checkout --force` will not undo a
   previous provisioning's apply. If `openclaw.plugin.json`'s `configSchema`
   changed, re-check `openclaw_plugins_block.json` against it (every key in the
   block must be in the schema; `hooks.allowConversationAccess` stays at the
   entry level, outside `config`).
