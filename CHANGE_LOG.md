# Change log — pilot run → second run

Timeline: pilots ran on the June 17–18 harness; the V2 scaffold rewrite landed June 29 – July 1; run 2 (personas + tabpfn) provisioned July 2 and completed July 6–7, 2026.

## Scaffold and engineering changes between runs

| Area | Pilot | Second run |
|---|---|---|
| **Prompt** — launch framing | Linear staircase to a deliverable; generic brief | Research *cycle* with sanctioned back-edges; hour-0 duties: research-plan spine, costed budget-deployment menu, candidate portfolio before any experiment |
| **Prompt** — horizon | ~10 days | 5 days (+24h extension mid-run) |
| **Context** — goal visibility | Research question only in `BRIEF.md`; subagents never saw it | Question + success bar open `AGENTS.md` — every subagent knows the goal it serves |
| **Context** — exploration | One-pass lit skim satisfied the rules (5 refs vs human 52) | Survey → mandatory full deep-reads of anchor papers + LaTeX exemplars; citation-walk APIs in `TOOLS.md`; isolated sufficiency critic (who can search the lit itself) must approve before drafting; ~30–40% of horizon |
| **Context** — commitment & churn | "No idle" read as "always launch something"; 40-round review churn; early headline lock | 5th end-of-turn state (MATURE); yield-based review stopping; power critic + evidence floors at drafting entry *and* ship; underclaim audit + anti-hedge fix rule |
| **Context** — budget | Ceiling to stay under (pilot shipped at 38% API / 2% GPU) | Target to deploy on depth; under-spend = failure; burn-rate heartbeat + light ship backstop |
| **Engineering** — reasoning | Extended thinking **silently off** (root cause of most pilot failures) | `thinkingDefault: xhigh`, 1h prompt-cache retention, 600s provider timeout, verified post-boot |
| **Engineering** — subagents | ≤3 | ≤5, aimed at exploration fan-out |
| **Engineering** — resilience | None — a wedged session stayed dead until noticed | Session watchdog: auto-detects thinking-signature wedges, resets, agent resumes from state capsule |

## Human interventions during run 2

| Intervention | When | What |
|---|---|---|
| Thinking-signature patch | Jul 2 (hours in), broadened Jul 7 | Watchdog deployed after personas wedged; pattern generalized when tabpfn hit a second error variant; ~14 auto/manual recoveries total across both boxes |
| Turn-timeout raise | Jul 2 | Both agents reported turn timeouts; whole-turn ceiling raised 2880s → 3600s |
| Progress report request | mid-run | Status-only structured report (lit, hypotheses, experiments, data, references), committed as markdown — explicitly framed as non-steering |
| Deadline extension | Jul 6 | +24h, routed as a Tier-2 memo through the budget lock, milestone table, and burn-rate math |
| Readability + README closing loop | Jul 7 (post-completion) | Claims-frozen presentation overhaul of both papers + accessible README — since institutionalized as the mandatory Presentation Overhaul milestone and final-README stage with gates, so run 3 does it without intervention |

## Reading run-2 results

The first two interventions are pure infrastructure repair (an upstream runtime bug, not steering), the progress report was read-only, and only the extension and the closing loop touched the research process itself — with the closing loop now automated away for next time.
