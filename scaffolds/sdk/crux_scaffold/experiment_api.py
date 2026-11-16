"""OpenRouter experiments: explicit credentials and a shared, separate spending pool.

Run from the workspace: python -m crux_scaffold.experiment_api status|call.
Experiment tokens never enter the scaffold inference ledger or priced generations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import (
    RESOURCE_PROVIDERS,
    ResourceMetric,
    ResourceOptions,
    ResourceProvider,
    ResourceReadError,
    ResourceSnapshot,
)

if TYPE_CHECKING:
    from crux_scaffold.workspace import RunContext

KEY = "OPENROUTER_EXPERIMENT_API_KEY"
BUDGET = "OPENROUTER_EXPERIMENT_BUDGET_USD"
STATE = "OPENROUTER_EXPERIMENT_STATE_DIR"
KEY_CAP = "OPENROUTER_EXPERIMENT_KEY_CAP_USD"
BILLING = "OPENROUTER_EXPERIMENT_BILLING_MODE"
BASE = "https://openrouter.ai/api/v1/"


class ExperimentError(ResourceReadError):
    """Fixed safe feedback; never embeds provider bodies, credentials or prompts."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


_HTTP = urllib.request.build_opener(_NoRedirect)


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ExperimentError("experiment usage measurement invalid; new experiments paused")
    return float(value)


def validate(env: Mapping[str, str]) -> float:
    if any(not env.get(k, "").strip() for k in (KEY, BUDGET, "OPENROUTER_API_KEY", "RUN_SLUG")):
        raise InvalidDropInError(f"experiments require {KEY}, {BUDGET}, OPENROUTER_API_KEY and RUN_SLUG")
    if env[KEY] == env["OPENROUTER_API_KEY"]:
        raise InvalidDropInError("agent inference and experiment credentials must be distinct")
    if env.get(BILLING, "credits") not in {"credits", "byok"}:
        raise InvalidDropInError(f"{BILLING} must be credits or byok")
    try:
        allocation = number(float(env[BUDGET]))
        key_cap = number(float(env.get(KEY_CAP, env[BUDGET])))
        if allocation <= 0 or key_cap <= 0:
            raise ValueError
    except (ValueError, ExperimentError):
        raise InvalidDropInError(f"{BUDGET} must be a finite positive USD allocation") from None
    return allocation


@contextmanager
def _database(env: Mapping[str, str], root: Path | None = None) -> Iterator[sqlite3.Connection]:
    directory = Path(env.get(STATE) or (root or Path.cwd()) / ".experiment-api")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "receipts.sqlite3"
    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        path.chmod(0o600)
        connection.execute("CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                           "run TEXT, fingerprint TEXT, allocation REAL)")
        connection.execute("CREATE TABLE IF NOT EXISTS receipts (generation_id TEXT PRIMARY KEY, cost REAL, payload TEXT)")
        binding = env[KEY] + "\0" + env.get(BILLING, "credits")
        identity = (env["RUN_SLUG"], hashlib.sha256(binding.encode()).hexdigest(), float(env[BUDGET]))
        connection.execute("INSERT OR IGNORE INTO identity VALUES (1, ?, ?, ?)", identity)
        if connection.execute("SELECT run, fingerprint, allocation FROM identity").fetchone() != identity:
            raise ExperimentError("experiment identity changed; operator must reconcile the saved run before rebinding")
        yield connection


def _request(env: Mapping[str, str], path: str, payload: dict | None = None, *, timeout: float = 10) -> dict:
    request = urllib.request.Request(BASE + path, data=json.dumps(payload).encode() if payload else None,
                                     headers={"Authorization": f"Bearer {env[KEY]}",
                                              "Content-Type": "application/json"})
    for attempt in range(3 if payload else 1):
        try:
            with _HTTP.open(request, timeout=timeout) as response:
                raw = response.read(8_388_609)
            if len(raw) > 8_388_608:
                raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict) or "error" in result:
                raise ValueError
            return result
        except urllib.error.HTTPError as error:
            with error:
                try:
                    metadata = json.loads(error.read(65536)).get("error", {}).get("metadata", {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                except (ValueError, AttributeError):
                    metadata = {}
            transient = (error.code in {429, 500, 502, 503, 504}
                         or (error.code == 402 and metadata.get("limit_source") == "openrouter_in_flight_budget"))
            try:
                delay = max(2 ** attempt, float(error.headers.get("Retry-After", "0")))
            except (ValueError, TypeError):
                delay = 2 ** attempt
            if transient and payload and attempt < 2 and math.isfinite(delay) and delay <= 30:
                time.sleep(delay)
                continue
            if transient:
                raise ExperimentError("experiment provider temporarily unavailable; retry later on the same key") from None
            raise ExperimentError("experiment request rejected; check experiment key, credits and permissions; "
                                  "agent inference remains separate") from None
        except (OSError, ValueError):
            # A timeout can have been billed. Do not silently resubmit an ambiguous request.
            raise ExperimentError("experiment provider response unavailable; reconcile usage before retrying") from None
    raise AssertionError("unreachable")


def status(env: Mapping[str, str], *, root: Path | None = None) -> dict:
    allocation = validate(env)
    with _database(env, root) as database:
        database_usage = database.execute("SELECT COALESCE(SUM(cost), 0) FROM receipts").fetchone()[0]
    try:
        data = _request(env, "key")["data"]
        spent, cap, remaining = (number(data[k]) for k in ("usage", "limit", "limit_remaining"))
        if data.get("limit_reset") is not None or cap > float(env.get(KEY_CAP, allocation)) or cap <= 0:
            raise ExperimentError("experiment key needs a non-resetting provider cap at or below the declared key cap")
        byok = number(data["byok_usage"])
        mode = env.get(BILLING, "credits")
        if mode == "credits" and byok != 0:
            raise ExperimentError("experiment key has BYOK usage; reconcile billing route before new experiments")
        if mode == "byok" and data.get("include_byok_in_limit") is not True:
            raise ExperimentError("experiment provider key cap must include BYOK before new experiments")
        remaining_limits = [number(item["remaining_usd"]) for item in data.get("budgets", [])]
    except (KeyError, TypeError):
        raise ExperimentError("experiment usage response incomplete; new experiments paused") from None
    paced_spend = max(spent + byok, database_usage)
    return {"purpose": "experiments", "run_slug": env["RUN_SLUG"], "allocation_usd": allocation,
            "spent_usd": paced_spend, "provider_usage_usd": spent, "provider_limit_usd": cap,
            "billing_mode": mode, "provider_byok_usage_usd": byok,
            "remaining_usd": min(max(0, allocation - paced_spend), remaining, *remaining_limits),
            "receipt_cost_usd": database_usage, "unattributed_usd": max(0, spent + byok - database_usage),
            "account_balance_usd": None, "as_of": datetime.now(UTC).isoformat(),
            "note": "One shared experiment pool; lifetime key usage counts toward this run. "
                    "Run allowance is cooperative; the provider cap is the billing backstop. "
                    "Settlement may lag. BYOK meters upstream inference separately from OpenRouter charges; "
                    "these are provider-reported amounts, not invoice reconciliation. "
                    "Account funds and in-flight commitments are unknown."}


def record(env: Mapping[str, str], receipt: dict) -> None:
    with _database(env) as database:
        database.execute("INSERT OR IGNORE INTO receipts VALUES (?, ?, ?)",
                         (receipt["generation_id"], number(receipt["cost_usd"]), json.dumps(receipt)))


def complete(env: Mapping[str, str], model: str, prompt: str, *, provider: str, max_tokens: int,
             agent: str, experiment: str) -> dict:
    current = status(env)
    if current["remaining_usd"] <= 0:
        raise ExperimentError("experiment credit exhausted; continue analysis and writing with agent inference")
    if not 1 <= max_tokens <= 32768 or not prompt.strip():
        raise ExperimentError("experiment needs a nonempty prompt and max_tokens between 1 and 32768")
    if any(not re.fullmatch(r"[A-Za-z0-9_./:@-]{1,160}", v) for v in (model, provider, agent, experiment)):
        raise ExperimentError("experiment model, provider, agent and experiment identifiers are invalid")
    result = _request(env, "chat/completions", {
        "model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
        "provider": {"only": [provider], "allow_fallbacks": False},
    }, timeout=180)
    identifier = result.get("id")
    usage = result.get("usage", {})
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", identifier):
        raise ExperimentError("experiment generation identity missing; reconcile provider usage")
    expected_byok = env.get(BILLING, "credits") == "byok"
    if not isinstance(usage, dict) or usage.get("is_byok") is not expected_byok or usage.get("cost") is None:
        raise ExperimentError("experiment usage receipt missing or unexpected billing mode; reconcile before retrying")
    openrouter_cost = number(usage["cost"])
    upstream_cost = number(usage.get("cost_details", {}).get("upstream_inference_cost")) if expected_byok else 0
    receipt = {"generation_id": identifier, "cost_usd": openrouter_cost + upstream_cost, "agent": agent,
               "openrouter_cost_usd": openrouter_cost, "upstream_cost_usd": upstream_cost,
               "billing_mode": env.get(BILLING, "credits"),
               "experiment": experiment, "run_slug": env["RUN_SLUG"], "model": model, "provider": provider,
               "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
               "purpose": "experiments", "as_of": datetime.now(UTC).isoformat()}
    record(env, receipt)
    try:
        answer = result["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise ExperimentError("experiment returned no usable answer; billed usage retained, do not treat it as success") from None
    return {"answer": answer, "receipt": receipt}


class ExperimentOptions(ResourceOptions):
    refresh_seconds: float = Field(30, ge=0, allow_inf_nan=False)


@RESOURCE_PROVIDERS.register
class OpenRouterExperiments(ResourceProvider):
    type_name = "openrouter-experiments"
    Options = ExperimentOptions

    def validate(self, env: Mapping[str, str]) -> None:
        validate(env)
        if self.options.limits:
            raise InvalidDropInError("experiment limits stop submissions only; do not set whole-run resource limits")

    async def read(self, ctx: RunContext) -> ResourceSnapshot:
        report = await asyncio.to_thread(status, ctx.env, root=ctx.workspace.root)
        return ResourceSnapshot(as_of=datetime.fromisoformat(report["as_of"]),
                                metrics={key: ResourceMetric(value=report[key], unit="USD") for key in (
                                    "allocation_usd", "spent_usd", "remaining_usd", "receipt_cost_usd", "unattributed_usd")},
                                note=f"Billing mode: {report['billing_mode']}. {report['note']}")


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="refresh shared experiment allowance; never prints credentials")
    call = commands.add_parser("call", help="one experimental model call using only the experiment credential")
    for name in ("model", "provider", "agent", "experiment"):
        call.add_argument("--" + name, required=True)
    call.add_argument("--prompt-file", type=Path, required=True)
    call.add_argument("--max-tokens", type=int, required=True)
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    try:
        result = (status(env) if args.command == "status" else complete(
            env, args.model, args.prompt_file.read_text(), provider=args.provider, max_tokens=args.max_tokens,
            agent=args.agent, experiment=args.experiment))
    except (ExperimentError, InvalidDropInError) as error:
        print(str(error))
        return 2
    except OSError:
        print("experiment input or state unavailable; check private workspace paths")
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
