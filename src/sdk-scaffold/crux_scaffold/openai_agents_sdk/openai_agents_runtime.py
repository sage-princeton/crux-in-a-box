"""Run scaffold agents through the OpenAI Agents SDK and compatible model endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import replace
from functools import partial
from typing import Any, Literal, Self

from agents import Agent, RunConfig, Runner, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded
from agents.run_config import CallModelData, ModelInputData
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

from crux_scaffold.components import CountLimit, count_limit
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.agent_builder import AgentBuilder
from crux_scaffold.openai_agents_sdk.coding_tasks import CodingTasks
from crux_scaffold.openai_agents_sdk.conversation_context import ConversationContext, is_standing_context
from crux_scaffold.openai_agents_sdk.model_connection import ModelConnection
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.openai_agents_sdk.turn_outcome import TurnOutcome
from crux_scaffold.openai_agents_sdk.usage_hooks import UsageHooks
from crux_scaffold.openai_agents_sdk.verdict import Verdict
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import validate_review_tools
from crux_scaffold.workspace import ReviewWorkspace

JUDGE = "judge"


class OpenAIAgentsRuntime:
    """Run orchestrator turns and isolated judges using assembled SDK agents and managed resources."""

    def __init__(
        self,
        builder: AgentBuilder,
        conversation_context: ConversationContext,
        coding_tasks: CodingTasks,
        max_turns: CountLimit = 50,
    ) -> None:
        self._builder = builder
        self.conversation_context = conversation_context
        self._coding_tasks = coding_tasks
        self.max_turns = max_turns
        self._initialize()

    def _initialize(self) -> None:
        self.context = self._builder.resources.context
        self.drop_in = self._builder.resources.drop_in
        self.tools = self._builder.tools
        self.run_config = RunConfig(call_model_input_filter=self._select_input)
        self._steering_items: list[dict] = []

    @property
    def model_provider(self) -> ModelProvider:
        return self._builder.resources.model_provider

    @property
    def hooks(self) -> UsageHooks:
        return self._builder.hooks

    @property
    def model_family(self) -> Literal["anthropic", "google", "openai"] | None:
        """Derive the model vendor from its prefix, ignoring a leading OpenRouter alias marker.

        Unprefixed names on the direct OpenAI provider belong to OpenAI. Other unrecognized names stay
        unclassified so family-specific request changes are never applied by guesswork.
        """
        model = self.context.model.removeprefix("~")
        if model.startswith("anthropic/"):
            return "anthropic"
        if model.startswith("google/"):
            return "google"
        if model.startswith("openai/") or ("/" not in model and self.model_provider.type == "openai"):
            return "openai"
        return None

    @property
    def background_coding(self) -> bool:
        """Whether coding delegates return before their work completes."""
        return self._coding_tasks.start_in_background

    def _build(self) -> None:
        self._builder.build(self.run_config, self._turn_input)

    async def __aenter__(self) -> Self:
        try:
            return await self._start()
        except BaseException as exc:
            await self._startup_failed(exc)
            raise

    async def _start(self) -> Self:
        _trace_agents_sdk(self.context.telemetry)
        self._build()
        await self._connect()
        self.session = self.conversation_context.session(self.drop_in.config.orchestrator, self.context)
        return self

    async def _connect(self) -> None:
        check_names = self._builder.check_result_names if self.background_coding else None
        escalate = any(tool.type_name == "escalate" for tool in self.tools.values())
        await self._builder.resources.open(check_names, escalate=escalate)

    async def _startup_failed(self, exc: BaseException) -> None:
        await self._builder.resources.close()
        self._builder.resources.startup_failed(exc)

    async def __aexit__(self, *_exc_info: object) -> None:
        try:
            await self._coding_tasks.cancel()
        finally:
            await self._builder.resources.close()

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        self._steering_items = []
        orchestrator = self._builder.agents[self.drop_in.config.orchestrator]
        history = await self.session.get_items()
        input = self._turn_input(orchestrator.name, prompt, history)
        try:
            try:
                result = await Runner.run(
                    orchestrator,
                    input,
                    context=self.context,
                    max_turns=count_limit(self.max_turns),
                    hooks=self.hooks,
                    session=self.session,
                    run_config=replace(self.run_config, workflow_name=workflow),
                )
            except MaxTurnsExceeded:
                turn_outcome = TurnOutcome(final_output="", completed=False)
            else:
                turn_outcome = TurnOutcome(final_output=str(result.final_output), completed=True)
            remaining = await self._coding_tasks.drain()
            if remaining:
                report = "Scaffold coding completion report (uncollected delegate outputs):\n\n" + "\n\n".join(
                    remaining
                )
                await self.session.add_items([{"role": "user", "content": report}])
            turn_outcome.final_output = "\n\n".join(part for part in [turn_outcome.final_output, *remaining] if part)
            return turn_outcome
        except BaseException:
            await self._coding_tasks.cancel()
            raise

    async def judge(
        self,
        name: str,
        rubric: str,
        evidence: str,
        *,
        tools: list[str],
        max_turns: int | None,
        review_workspace: ReviewWorkspace | None = None,
    ) -> Verdict:
        ctx = self.context
        if review_workspace is not None:
            validate_review_tools([self.tools[tool] for tool in tools])
            ctx = replace(ctx, workspace=review_workspace)
        agent = Agent(
            name=JUDGE if review_workspace is not None else f"{JUDGE}:{name}",
            instructions=rubric,
            output_type=Verdict,
            model=self._builder.resources.connection.model(JUDGE),
            model_settings=self._builder.resources.connection.settings(),
            tools=[self._builder.function_tool(tool, ctx) for tool in tools],
        )
        try:
            result = await Runner.run(
                agent,
                evidence,
                context=ctx,
                max_turns=max_turns,
                hooks=UsageHooks(ctx.usage, f"{JUDGE}:{name}"),
                run_config=RunConfig(workflow_name=f"{JUDGE}:{name}", call_model_input_filter=self._select_cache_input),
            )
        except MaxTurnsExceeded:
            return Verdict(
                passed=False,
                next_prompt=None,
                feedback=f"The judge ran out of turns ({max_turns}) before reaching a verdict.",
            )
        return result.final_output

    def _turn_input(self, name: str, prompt: str, history: list[Any]) -> list[Any]:
        drop_in, ctx = self.drop_in, self.context
        load = partial(drop_in.standing_context, drop_in.config.agents[name], agent=name, telemetry=ctx.telemetry)
        prefix = self.conversation_context.inject_standing_context(history, load)
        return [*prefix, {"role": "user", "content": prompt}]

    def describe(self) -> list[str]:
        self._build()
        return self._builder.describe()

    async def _select_input(self, data: CallModelData[Any]) -> ModelInputData:
        steering = self.context.steering
        annotations = []
        if steering and data.agent.name == self.drop_in.config.orchestrator:
            await steering.poll()
            pending = steering.pending()
            if pending:
                for message in pending:
                    envelope = steering.envelope(message)
                    try:
                        history = await self.session.get_items()
                        if envelope not in history:
                            await self.session.add_items([envelope])
                        if envelope not in data.model_data.input:
                            self._steering_items.append(envelope)
                        steering.delivered(message["ts"])
                    except Exception:  # noqa: BLE001 - Session backends must fail closed without exposing raw errors.
                        status = (
                            "delivery_failed: check conversation/state storage; restart with the same state directory"
                        )
                        steering.state["status"] = status
                        steering.save()
                        steering.event("delivery_failed", message_id=message["ts"], status=status)
                        raise InvalidDropInError(status) from None
            if steering.state["status"] != "ok":
                annotations.append(f"Steering transport status: {steering.state['status']}")
        items = list(self.conversation_context.select(list(data.model_data.input)))
        if steering and data.agent.name == self.drop_in.config.orchestrator:
            # Filter additions are not persisted by Runner. Keep newly stored messages in this run's input
            # until its next run reloads the durable session, even when older conversation is trimmed.
            items.extend(item for item in self._steering_items if item not in items)
        status = await self.context.budget_status()
        annotations.append(f"<resource_status>\n{status}\n</resource_status>")
        items = self._cache_input(items)
        # Runner does not save filter additions. Keep volatile status after retained history,
        # using a user message so Gemini adapters do not fold it into cached system instructions.
        items.append({"role": "user", "content": "\n\n".join(annotations)})
        return ModelInputData(input=items, instructions=data.model_data.instructions)

    async def _select_cache_input(self, data: CallModelData[Any]) -> ModelInputData:
        # Judges get only cache markers, without context selection, steering or resource metadata.
        return ModelInputData(input=self._cache_input(data.model_data.input), instructions=data.model_data.instructions)

    def _cache_input(self, items: list[Any]) -> list[Any]:
        """OpenRouter translates these text boundaries to Claude's cache_control blocks.

        Mark the stable snapshot and a growing retained prefix, never the replaced status tail.
        Normalize text consistently on every request and copy it so markers cannot enter sessions.
        """
        provider = self.model_provider
        if (
            provider.type != "openai-compatible"
            or provider.base_url.host != "openrouter.ai"
            or self.model_family != "anthropic"
        ):
            return items
        selected = list(items)
        candidates = []
        anchor = None
        explicit = False
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            field = "output" if item.get("type") == "function_call_output" else "content"
            if field == "content" and item.get("role") not in {"user", "developer", "system"}:
                continue
            content = item.get(field)
            if isinstance(content, str):
                content = [{"type": "input_text", "text": content}]
            elif isinstance(content, list):
                content = [dict(part) if isinstance(part, dict) else part for part in content]
            else:
                continue
            explicit |= any(isinstance(part, dict) and "prompt_cache_breakpoint" in part for part in content)
            texts = [
                position
                for position, part in enumerate(content)
                if isinstance(part, dict) and part.get("type") == "input_text" and part.get("text")
            ]
            if not texts:
                continue
            selected[index] = {**item, field: content}
            candidate = (index, field, texts[-1])
            candidates.append(candidate)
            if is_standing_context(item):
                anchor = candidate
        if candidates and not explicit:
            for index, field, position in {anchor or candidates[0], candidates[-1]}:
                selected[index][field][position]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return selected


async def probe_model(
    env: Mapping[str, str], prompt: str, telemetry: Telemetry, *, model_provider: ModelProvider
) -> str:
    """Run one traced provisioning call with the configured model provider."""
    _trace_agents_sdk(telemetry)
    if not env.get("CRUX_MODEL"):
        raise InvalidDropInError("CRUX_MODEL is not set")
    connection = ModelConnection(model_provider, env)
    async with AsyncExitStack() as stack:
        connection.prepare(stack)
        agent = Agent(
            name="probe",
            instructions="Reply with exactly the text you are asked for.",
            model=connection.model("probe"),
            model_settings=connection.settings(),
        )
        return str((await Runner.run(agent, prompt, max_turns=1)).final_output)


def _trace_agents_sdk(telemetry: Telemetry) -> None:
    """Export Agents SDK spans through OpenTelemetry (and so into Langfuse) instead of to the OpenAI platform.

    Langfuse reads a generation's input and output from `input.value` and `output.value`. The per-message copies
    (`llm.input_messages.*`) grow with the conversation, and past OpenTelemetry's 128-attribute limit they evict a
    span's oldest attributes: Langfuse's session, tags, model and usage."""
    set_tracing_disabled(not telemetry.sdk_tracing)
    if not telemetry.sdk_tracing:
        return
    instrumentor = OpenAIAgentsInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(config=TraceConfig(hide_input_messages=True, hide_output_messages=True))
