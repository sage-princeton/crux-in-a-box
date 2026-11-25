# PLAN.md — Plan & Budget Ledger

_Rewriteable: this is the live plan. The permanent record of decisions is `LOG.md`. Keep every section current, because this file is what you re-orient from after any context loss._

## Goal

_The task restated in your own words, and what "done" will look like on the deployed site._

## Budget ledger

_Write at hour 0; keep current. Update spent/remaining whenever you spend meaningfully or an estimate proves wrong. A revision is a logged decision._

<!-- prettier-ignore -->
| Phase / item | Time | LLM $ | API $ | AWS $ | What it buys |
|---|---|---|---|---|---|
| Environment + inventory + URL map | | | | | |
| Content model + infrastructure + domain | | | | | |
| Importers + templates | | | | | |
| Verification iterations + fixes | | | | | |
| Reserve | | | | | |
| **Allocated / cap** | / 6 weeks from launch | / $100 | / $100 | / $100 | |

**Current position:** spent / remaining for each budget, as of <timestamp>. Also list the running AWS resources and their hourly cost.

## URL scheme

_How main-site and blog paths map into the combined site, and why. The full map lives in `inventory/url_map.csv`._

## Content model

_Payload collections and globals, their key fields and relationships, and which source page types map to each. Give the reasoning: how each kind of content is used across the sites, and how staff will need to change it._

## Editorial scenarios

_The routine maintenance tasks you will script for criterion 9, and what each one must change on the public site._

## Infrastructure

_What runs where (domain, TLS, compute, database, media storage), how it is provisioned (the committed scripts/IaC), and its hourly cost._

## Milestones

| # | Milestone | Target date | Status |
|---|---|---|---|
| 1 | Environment verified (AWS role, sources incl. blogs-qa lock, API keys, Docker, Playwright) | | |
| 2 | Inventory + pilot URLs confirmed + URL map written | | |
| 3 | Content model | | |
| 4 | Infrastructure up: domain, TLS, admin restricted | | |
| 5 | Importers + templates; first pages live | | |
| 6 | All pilot pages migrated | | |
| 7 | Editorial scenarios + CMS design review pass | | |
| 8 | Verification iteration verdict DONE | | |
| 9 | `COMPLETION_REPORT.md` committed | | |

## Work in flight

| Unit | Kind (process / subagent / scan) | PID or ID | Expected artifact | ETA |
|---|---|---|---|---|
