"""TabPFN submission records and resource-driven research/finalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress

import pypdfium2 as pdfium
from pydantic import Field, model_validator

from crux_scaffold.components import count_limit
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.gates import GATES, GateResult, JudgeOptions, LlmJudgeGate
from crux_scaffold.loop import LOOPS, LoopOutcome, PhasedLoop, PhasedOptions
from crux_scaffold.workspace import ReviewWorkspace


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def cleanup_pods(env, *, request=None):
    outcome = {"terminated": [], "failed": [], "discovery_complete": False}
    deadline = time.monotonic() + 30
    if not env.get("RUNPOD_API_KEY") or not env.get("RUN_SLUG"):
        return outcome
    if request is None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None

        client = urllib.request.build_opener(NoRedirect)

        def request(method, path):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Cleanup deadline")
            req = urllib.request.Request("https://api.runpod.io/v2/" + path, method=method,
                                         headers={"Authorization": f"Bearer {env['RUNPOD_API_KEY']}"})
            with client.open(req, timeout=min(10, remaining)) as response:
                body = response.read(1024 * 1024)
                return json.loads(body) if body else {}

    owned, cursor, seen = set(), None, set()
    try:
        for _ in range(100):
            query = {"includeClusterPods": "true", "limit": 100}
            if cursor:
                query["cursor"] = cursor
            body = request("GET", "pods?" + urllib.parse.urlencode(query))
            for pod in body["pods"]:
                if pod.get("env", {}).get("CRUX_RUN_SLUG") == env["RUN_SLUG"]:
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pod["id"]):
                        raise ValueError("Invalid pod ID")
                    owned.add(pod["id"])
            pagination = body["pagination"]
            if pagination["hasNextPage"] is False and pagination["nextCursor"] is None:
                outcome["discovery_complete"] = True
                break
            cursor = pagination["nextCursor"]
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                break
            seen.add(cursor)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for identifier in sorted(owned):
        try:
            request("DELETE", f"pods/{identifier}")
            outcome["terminated"].append(identifier)
        except (OSError, ValueError):
            outcome["failed"].append(identifier)
    return outcome


class ResearchOptions(PhasedOptions):
    reserve_wall_seconds: float = Field(3600, ge=0, allow_inf_nan=False)
    reserve_fraction: float = Field(0.1, ge=0, lt=1)
    poll_seconds: float = Field(15, gt=0, le=60)


@LOOPS.register
class ResearchLoop(PhasedLoop):
    type_name = "tabpfn_research"
    Options = ResearchOptions

    def __init__(self, name, options, *, gates):
        super().__init__(name, options, gates=gates)
        if [p.name for p in options.phases] != ["research", "finalize"]:
            raise InvalidDropInError("tabpfn_research requires research then finalize phases")

    def reserve_reason(self, ctx):
        budget = ctx.budget
        elapsed = budget.elapsed(ctx.usage, ctx.now())
        if (elapsed is not None and budget.max_wall_seconds is not None
                and elapsed >= budget.max_wall_seconds - self.options.reserve_wall_seconds):
            return "time reserve"
        fraction = 1 - self.options.reserve_fraction
        cost = ctx.model_usage()["model_cost_usd"]
        if budget.max_cost_usd is not None and cost is not None and cost >= budget.max_cost_usd * fraction:
            return "model spend reserve"
        if ctx.resources:
            for name, provider in ctx.resources.providers.items():
                snapshot = ctx.resources.snapshot(name)
                if snapshot and any(snapshot.metrics[key].value >= limit * fraction
                                    for key, limit in provider.options.limits.items() if key in snapshot.metrics):
                    return f"{name} reserve"
        return None

    async def run(self, runtime, drop_in, ctx, state, store):
        remaining = None
        if ctx.budget.max_wall_seconds is not None:
            remaining = max(0, ctx.budget.max_wall_seconds - ctx.budget.elapsed(ctx.usage, ctx.now()))
        try:
            if state.phase_index >= len(self.options.phases):
                return await super().run(runtime, drop_in, ctx, state, store)
            async with asyncio.timeout(remaining):
                return await self._run(runtime, drop_in, ctx, state, store)
        except TimeoutError:
            return LoopOutcome(status="budget_exhausted", phase=self.options.phases[
                min(state.phase_index, 1)].name, final_output="Wall-clock deadline reached; unfinished work stopped.")
        finally:
            cleanup = await asyncio.to_thread(cleanup_pods, ctx.env)
            save_json(ctx.state_dir / "tabpfn/cleanup.json", cleanup)
            ctx.workspace.write_file("CLEANUP_STATUS.json", json.dumps(cleanup, indent=2))
            ctx.telemetry.record("tabpfn:cleanup", "span", None, cleanup)

    async def _run(self, runtime, drop_in, ctx, state, store):
        task = None
        try:
            while True:
                await ctx.refresh_resources()
                if ctx.budget_exhausted():
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                    store.save(state)
                    return LoopOutcome(status="budget_exhausted", phase=self.options.phases[
                        min(state.phase_index, 1)].name, final_output="Resource cap reached; unfinished work stopped.")
                reason = self.reserve_reason(ctx)
                if reason and not (ctx.state_dir / "tabpfn/reserve.json").exists():
                    save_json(ctx.state_dir / "tabpfn/reserve.json", {"reason": reason})
                    ctx.workspace.write_file("FINALIZATION_REASON.json", json.dumps({"reason": reason}))
                if state.phase_index == 0 and reason:
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                        task = None
                    state.phase_index, state.iteration, state.next_prompt = 1, 0, None
                    store.save(state)
                if task is None:
                    task = asyncio.create_task(super().run(runtime, drop_in, ctx, state, store))
                delay = self.options.poll_seconds
                if ctx.budget.max_wall_seconds is not None:
                    delay = min(delay, max(0, ctx.budget.max_wall_seconds - ctx.budget.elapsed(ctx.usage, ctx.now())))
                done, _ = await asyncio.wait([task], timeout=delay)
                if done:
                    return await task
        finally:
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            store.save(state)


class PaperOptions(JudgeOptions):
    final: bool = False

    @model_validator(mode="after")
    def paper_only(self):
        if not self.blind or self.artifacts != {"submission.pdf": "paper/paper.pdf"}:
            raise ValueError("TabPFN reviews require a blind submission.pdf mapped to paper/paper.pdf")
        return self


def paper_problem(workspace, snapshot):
    abstract = workspace.read_file("paper/abstract.txt")
    if not abstract.strip() or len(abstract.split()) > 200:
        return "The plain-text paper/abstract.txt must contain 1–200 words."
    aux = workspace.read_file("paper/paper.aux")
    end = re.search(r"\\newlabel\{crux:main-end\}\{\{[^}]*\}\{(\d+)\}", aux)
    if not end or not 1 <= int(end[1]) <= 9:
        return "Rebuild paper/paper.tex; the crux:main-end label must be on main-body page 1–9."
    with pdfium.PdfDocument(snapshot.read_bytes("submission.pdf")) as pdf:
        if not 1 <= len(pdf) <= 32:
            return "The submission must contain 1–32 total pages, including references and appendices."
    return None


def package_problem(workspace, digest, review_path):
    for path in ("README.md", "reproduce.sh", "paper/paper.tex", "results/index.html", "COMPLETION_REPORT.md"):
        if not workspace.read_file(path).strip():
            return f"Missing final deliverable: {path}"
    report = workspace.read_file("COMPLETION_REPORT.md")
    if digest not in report or review_path not in report:
        return f"Update COMPLETION_REPORT.md with final PDF SHA-256 {digest} and unedited review {review_path}."
    claims = json.loads(workspace.read_file("evidence/claims.json"))
    if not isinstance(claims, list) or not claims:
        return "evidence/claims.json must list the paper's claims and their evidence."
    for claim in claims:
        if (not isinstance(claim, dict) or not claim.get("claim") or not claim.get("command")
                or hashlib.sha256(workspace.read_bytes(claim["artifact"])).hexdigest() != claim["sha256"]):
            return "Every evidence claim needs a claim, re-verification command, artifact path and matching sha256."
    return None


@GATES.register
class PaperReview(LlmJudgeGate):
    type_name = "tabpfn_review"
    Options = PaperOptions

    async def evaluate(self, ctx):
        workspace = ctx.run.workspace

        def result(passed, feedback):
            return GateResult(gate=self.name, passed=passed, feedback=feedback)

        try:
            snapshot = ReviewWorkspace.snapshot(workspace, self.options.artifacts)
            digest = hashlib.sha256(snapshot.read_bytes("submission.pdf")).hexdigest()
            if not self.options.final:
                request = json.loads(workspace.read_file("REVIEW_REQUEST.json"))
                if not isinstance(request, dict) or request.get("paper_sha256") != digest:
                    return result(False, "Continue research; request review with the current PDF SHA-256.")
            if problem := paper_problem(workspace, snapshot):
                return result(False, problem)
            rubric = ctx.drop_in.read(self.options.rubric)
            key = hashlib.sha256((digest + rubric).encode()).hexdigest()
            canonical = ctx.run.state_dir / "tabpfn/reviews" / f"{key}.json"
            if canonical.exists():
                record = json.loads(canonical.read_text())
            else:
                verdict = await ctx.runtime.judge(self.name, rubric, self.evidence(ctx), tools=self.tools,
                                                  max_turns=count_limit(self.options.max_turns),
                                                  review_workspace=snapshot)
                record = {"paper_sha256": digest, "verdict": verdict.model_dump()}
                save_json(canonical, record)
            review_path = f"reviews/{key}.json"
            workspace.write_file(review_path, canonical.read_text())
            workspace.write_file("REVIEW_STATUS.json", json.dumps({**record, "review_path": review_path}, indent=2))
            verdict = record["verdict"]
            if hashlib.sha256(workspace.read_bytes("paper/paper.pdf")).hexdigest() != digest:
                return result(False, "The PDF changed during review; collect writers and submit the current PDF.")
            if not self.options.final:
                return result(verdict["passed"], verdict["feedback"])
            if problem := package_problem(workspace, digest, review_path):
                return result(False, problem)
            reserve = ctx.run.state_dir / "tabpfn/reserve.json"
            if not verdict["passed"] and not reserve.exists():
                return result(False, verdict["feedback"])
            remaining = ctx.run.budget.max_wall_seconds - ctx.run.budget.elapsed(ctx.run.usage, ctx.run.now()) \
                if ctx.run.budget.max_wall_seconds is not None and ctx.run.usage.started_at else 21600
            reproduction = await workspace.run_shell_async("bash reproduce.sh", max(0, remaining))
            save_json(ctx.run.state_dir / "tabpfn/reproduction.json", {"output": reproduction})
            workspace.write_file("REPRODUCTION_STATUS.json", json.dumps({"output": reproduction}, indent=2))
            if not reproduction.startswith("exit_code=0\n"):
                return result(False, "Headline reproduction failed; inspect REPRODUCTION_STATUS.json.")
            if problem := package_problem(workspace, digest, review_path):
                return result(False, problem)
            if hashlib.sha256(workspace.read_bytes("paper/paper.pdf")).hexdigest() != digest:
                return result(False, "Reproduction changed the paper; submit the rebuilt PDF for review.")
            outcome = {"paper_sha256": digest, "review_path": review_path, "accepted": verdict["passed"],
                       "completion_reason": json.loads(reserve.read_text())["reason"] if reserve.exists() else "accepted"}
            save_json(ctx.run.state_dir / "tabpfn/outcome.json", outcome)
            workspace.write_file("RUN_OUTCOME.json", json.dumps(outcome, indent=2))
            return result(True, f"Final package recorded; accepted={verdict['passed']}. Review: {review_path}")
        except (ToolError, OSError, ValueError, KeyError, TypeError, pdfium.PdfiumError):
            return result(False, "Submission incomplete or invalid. Build the paper and submit REVIEW_REQUEST.json; "
                          "finalization also needs the README, reproduction command, HTML, evidence manifest and report.")
