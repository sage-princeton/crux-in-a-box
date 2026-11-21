"""Run the historical PDF-only TabPFN panel through OpenRouter, independently of research inference."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import math
import os
import time
from pathlib import Path

import httpx
from agents import OpenAIProvider, set_tracing_disabled
from budget import Budget, publish
from openai import AsyncOpenAI
from prepare import HERE
from render import render
from tabpfn_extensions import paper_problem
from telemetry import Observation

from crux_scaffold.agent_runtimes.base import Assembly, Verdict
from crux_scaffold.agent_runtimes.openai_agents_sdk import OpenAIAgentsOptions, OpenAIAgentsRuntime
from crux_scaffold.config import load_config
from crux_scaffold.context_strategies import CONTEXT_STRATEGIES
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import ToolError
from crux_scaffold.gates import judge_panel
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.tools import TOOLS
from crux_scaffold.usage import UsageLedger
from crux_scaffold.workspace import ReviewWorkspace, RunContext, Workspace

HISTORICAL = HERE.parents[1] / "scaffolds/sdk/run-harness/tabpfn-full"
LOG = logging.getLogger(__name__)


class Meter:
    """Record each paid Responses API call, including intermediate PDF-reading turns.

    :ivar budget: Study budget charged for completed reviewer calls.
    """

    def __init__(self, budget: Budget, run: str, key: str):
        """Bind provider usage to a particular blinded submission and budget.

        :param budget: Combined research and review budget.
        :param run: Stable study identifier.
        :param key: Blinded submission cache fingerprint.
        """
        self.budget, self.run, self.key = budget, run, key

    async def before(self, request: httpx.Request) -> None:
        """Enforce the shared study limits before sending an inference request.

        :param request: Outgoing reviewer HTTP request.
        """
        if self.budget.status()["phase"] in {"stopped", "accounting-hold"}:
            raise RuntimeError("Review budget exhausted or metering unavailable")

    async def after(self, response: httpx.Response) -> None:
        """Persist only model identity, timestamps, usage and OpenRouter's billed cost.

        :param response: Provider response including usage.
        """
        if response.status_code != 200 or response.request.url.path.rstrip("/").split("/")[-1] != "responses":
            return
        await response.aread()
        data = response.json()
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        known = isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0
        counts = {k: usage[src] for k, src in (("input", "input_tokens"), ("output", "output_tokens"))
                  if type(usage.get(src)) is int}
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        if type(cached) is int and 0 < cached <= counts.get("input", 0):
            counts["input"] -= cached
            counts["input_cached_tokens"] = cached
        request = json.loads(response.request.content)
        self.budget.ledger.put(Observation.record(
            self.run, "review-" + self.key, data["id"], "Blind paper review", time.time(), request["model"],
            counts, {"provider": cost} if known else None, True))


class CachedJudges:
    """Retain completed independent reviews if a sibling fails or the service restarts.

    :ivar cache: Per-submission directory for completed votes.
    """

    def __init__(self, runtime: OpenAIAgentsRuntime, models: list[str], cache: Path):
        """Bind the immutable submission cache to the ordered historical model list.

        :param runtime: Historical SDK reviewer runtime.
        :param models: Ordered external panel model IDs.
        :param cache: Private per-submission cache directory.
        """
        self.runtime, self.models, self.cache = runtime, models, cache

    async def judge(self, *args, model: str, **kwargs) -> Verdict:
        """Invoke one independent reviewer once per complete result for this PDF and rubric.

        :param model: Provider model identifier.
        :param args: Positional arguments for the historical judge.
        :param kwargs: Remaining keyword arguments for the historical judge.
        :returns: The completed independent review.
        """
        path = self.cache / f"judge-{self.models.index(model)}.json"
        if path.is_file():
            return Verdict.model_validate_json(path.read_bytes())
        verdict = await self.runtime.judge(*args, model=model, **kwargs)
        publish(path, verdict.model_dump(), 0o600)
        return verdict


class BlindPanel:
    """Prepare only the anonymous PDF, then run and publish the historical majority vote.

    :ivar gate: Historical blind-review gate configuration.
    """

    def __init__(self, workspace: Path, state: Path, run: str):
        """Resolve the checked-in historical gate, rubric and model provider configuration.

        :param workspace: Research worktree shared by the CLI and its workers.
        :param state: Trusted study directory containing durable controller records.
        :param run: Stable study identifier.
        """
        self.workspace, self.state, self.run = Workspace(workspace), state, run
        self.drop = DropInDirectory(HISTORICAL, load_config(HISTORICAL))
        self.gate = self.drop.config.gates["research_review"]
        self.models = self.gate["models"]
        self.rubric = self.drop.read(self.gate["rubric"])
        self.public = self.state / "public"

    def key(self, digest: str) -> str:
        """Use the historical gate's content, rubric and ordered-panel fingerprint.

        :param digest: SHA-256 of the anonymous PDF bytes.
        :returns: Submission cache fingerprint.
        """
        return hashlib.sha256((digest + self.rubric + "".join("\n" + m for m in self.models)).encode()).hexdigest()

    async def evaluate(self, *, retry: bool = False) -> None:
        """Validate the submitted draft, meter the isolated panel, and publish a digest-bound verdict.

        :param retry: Allow an operator-inspected interrupted review to resume.
        """
        budget = Budget(self.state)
        limits = budget.status()
        if limits["phase"] in {"stopped", "accounting-hold"}:
            return
        final = limits["phase"] == "finalize" or (self.state / "finalizing").exists()
        if not final:
            request = ReviewWorkspace.snapshot(self.workspace, {"request": "REVIEW_REQUEST.json"})
            requested = json.loads(request.read_file("request"))["paper_sha256"]
        paper = ReviewWorkspace.snapshot(self.workspace, {"submission.pdf": "paper/paper.pdf"})
        digest = hashlib.sha256(paper.read_bytes("submission.pdf")).hexdigest()
        if not final and digest != requested:
            return
        key = self.key(digest)
        directory = self.state / "reviews" / key
        directory.mkdir(parents=True, exist_ok=True)
        report = directory / "panel.json"
        archive = f"reviews/{key}.json"
        if not report.exists():
            started = directory / "in-flight"
            if started.exists() and not retry:
                return
            meta = ReviewWorkspace.snapshot(self.workspace, {p: p for p in ["paper/abstract.txt", "paper/paper.aux"]})
            if problem := paper_problem(meta, paper):
                publish(self.public / "REVIEW_STATUS.json", {"complete": False, "paper_sha256": digest, "feedback": problem})
                return
            publish(self.public / "REVIEW_STATUS.json", {"complete": False, "paper_sha256": digest, "passed": False,
                    "feedback": "Blind review pending. An interrupted panel can be resumed by the operator."})
            started.touch()
            verdict, members = await self.vote(paper, directory, key, budget)
            record = {"paper_sha256": digest, "complete": all(member["completed"] for member in members),
                      "passed": verdict["passed"], "feedback": verdict["feedback"], "review_path": archive}
            publish(directory / "members.json", {"members": members}, 0o600)
            if record["complete"]:
                publish(report, record, 0o600)
        else:
            record = json.loads(report.read_text())
        (self.public / "reviews").mkdir(exist_ok=True, mode=0o755)
        publish(self.public / archive, record)
        publish(self.public / "REVIEW_STATUS.json", record)

    async def vote(self, paper: ReviewWorkspace, cache: Path, key: str, budget: Budget) -> tuple[dict, list]:
        """Use the SDK's blind judge implementation, PDF tools, rubric and majority aggregation.

        :param paper: Read-only PDF snapshot exposed to the panel.
        :param cache: Private per-submission cache directory.
        :param key: Blinded submission cache fingerprint.
        :param budget: Combined research and review budget.
        :returns: Aggregate verdict and ordered individual votes.
        """
        provider = self.drop.config.runtime["model_provider"]
        if provider["base_url"] != "https://openrouter.ai/api/v1":
            raise ValueError("The historical reviewer provider has changed")
        env = {"CRUX_REASONING_EFFORT": "high", "CRUX_MODEL": self.models[0]}
        ctx = RunContext(paper, self.state, env, UsageLedger(), self.drop.config.budget, NullTelemetry())
        tools = {name: TOOLS.create(name, {}) for name in self.gate["tools"]}
        assembly = Assembly(self.drop, ctx, tools, {}, CONTEXT_STRATEGIES.create("context", {"type": "persistent"}),
                            lambda _: self.rubric)
        meter = Meter(budget, self.run, key)
        set_tracing_disabled(True)
        async with (
            httpx.AsyncClient(timeout=180, event_hooks={"request": [meter.before], "response": [meter.after]}) as http,
            AsyncOpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=provider["base_url"],
                        http_client=http, max_retries=0) as client,
        ):
            upstream = OpenAIProvider(openai_client=client, use_responses=True)
            runtime = OpenAIAgentsRuntime("blind-panel", OpenAIAgentsOptions(model_provider=provider),
                      assembly=assembly, models={name: upstream.get_model(name) for name in self.models})
            judges = CachedJudges(runtime, self.models, cache)
            return await judge_panel(judges, "paper", self.rubric, render("submission.md"),
                                     self.models, tools=self.gate["tools"], max_turns=None, review_workspace=paper)

    async def serve(self, once: bool, retry: bool) -> None:
        """Watch for review requests under a single trusted worker lock.

        :param once: Exit after processing the current request.
        :param retry: Allow an operator-inspected interrupted review to resume.
        """
        with (self.state / "review.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                try:
                    await self.evaluate(retry=retry)
                except (OSError, ToolError, ValueError, TypeError, KeyError) as exc:
                    if once:
                        raise
                    LOG.debug("Submission unavailable (%s)", type(exc).__name__)
                except Exception as exc:
                    if once:
                        raise
                    LOG.error("Panel interrupted (%s). Inspect and retry the outstanding review.", type(exc).__name__)
                if once:
                    return
                retry = False
                await asyncio.sleep(10)


def main() -> None:
    """Start the external reviewer service or retry an operator-inspected incomplete panel.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    asyncio.run(BlindPanel(args.workspace, args.state, args.run).serve(args.once, args.retry))


if __name__ == "__main__":
    main()
