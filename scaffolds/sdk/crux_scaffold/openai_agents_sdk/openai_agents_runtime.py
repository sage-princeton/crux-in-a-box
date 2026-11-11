"""Run scaffold agents through the OpenAI Agents SDK and compatible model endpoints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, Literal, Self

from agents import Agent, FunctionTool, RunConfig, RunContextWrapper, Runner, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded
from agents.run_config import CallModelData, ModelInputData
from agents.strict_schema import ensure_strict_json_schema
from agents.tool_context import ToolContext
from openai import BadRequestError
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from pydantic import ValidationError

from crux_scaffold.components import CountLimit, count_limit
from crux_scaffold.context_strategies import ContextStrategy, is_standing_context, since_restart
from crux_scaffold.delegate_mailboxes import DelegateMailboxes
from crux_scaffold.delegate_tasks import DelegateTasks
from crux_scaffold.errors import InvalidDropInError, ModelRequestError
from crux_scaffold.openai_agents_sdk.agent_builder import AgentBuilder
from crux_scaffold.openai_agents_sdk.brief_arguments import BriefArguments
from crux_scaffold.openai_agents_sdk.coding_tasks import CodingTasks
from crux_scaffold.openai_agents_sdk.delegation_limiter import DelegationLimiter
from crux_scaffold.openai_agents_sdk.inbox_arguments import InboxArguments
from crux_scaffold.openai_agents_sdk.message_arguments import MessageArguments
from crux_scaffold.openai_agents_sdk.model_connection import ModelConnection
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.openai_agents_sdk.send_arguments import SendArguments
from crux_scaffold.openai_agents_sdk.task_arguments import TaskArguments
from crux_scaffold.openai_agents_sdk.turn_outcome import TurnOutcome
from crux_scaffold.openai_agents_sdk.usage_hooks import UsageHooks
from crux_scaffold.openai_agents_sdk.verdict import Verdict
from crux_scaffold.steering import is_envelope
from crux_scaffold.telemetry import Telemetry
from crux_scaffold.tools import Arguments, validate_review_tools
from crux_scaffold.workspace import ReviewWorkspace

JUDGE = "judge"


class OpenAIAgentsRuntime:
    """Run orchestrator turns and isolated judges using assembled SDK agents and managed resources."""

    def __init__(
        self,
        builder: AgentBuilder,
        context_strategy: ContextStrategy,
        coding_tasks: CodingTasks,
        max_turns: CountLimit = 50,
        *,
        delegation: DelegationLimiter,
        research_context: Callable[[str], str],
        interactive_delegates: bool = False,
        coding_result_wait_seconds: float | Literal["unlimited"] = 1800,
    ) -> None:
        self._builder = builder
        self.context_strategy = context_strategy
        self._coding_tasks = coding_tasks
        self.max_turns = max_turns
        self._delegation = delegation
        self.research_context = research_context
        self.interactive_delegates = interactive_delegates
        self.coding_result_wait_seconds = coding_result_wait_seconds
        self._initialize()
        self.delegates: DelegateTasks | None = None
        self._delegate_context: ContextVar[tuple[str, list[dict]] | None] = ContextVar("delegate_context", default=None)
        if interactive_delegates:
            self.delegates = DelegateTasks(DelegateMailboxes(self.context.state_dir / "delegates.sqlite"),
                                           self._run_delegate, self.context.telemetry)

    def _initialize(self) -> None:
        self.context = self._builder.resources.context
        self.drop_in = self._builder.resources.drop_in
        self.tools = self._builder.tools
        self.run_config = RunConfig(call_model_input_filter=self._select_input)
        self._steering_items: list[dict] = []
        self._restart_tools = {name for name in self.drop_in.config.agents[self.drop_in.config.orchestrator].tools
                               if self.tools[name].type_name == "restart_context"}

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
        if self.delegates and any(spec.delegates for name, spec in self.drop_in.config.agents.items()
                                 if name != self.drop_in.config.orchestrator):
            raise InvalidDropInError("interactive_delegates supports direct PI delegates only")
        self._builder.build(self.run_config,
                            interactive_tool=self._interactive_tool if self.delegates else None,
                            communication_tools=self._communication_tools if self.delegates else None)

    async def __aenter__(self) -> Self:
        try:
            return await self._start()
        except BaseException as exc:
            await self._startup_failed(exc)
            raise

    async def _start(self) -> Self:
        _trace_agents_sdk(self.context.telemetry)
        self._build()
        if self.delegates:
            self.delegates.mailboxes.recover()
        await self._connect()
        self.session = self.context_strategy.session(self.drop_in.config.orchestrator, self.context)
        return self

    async def _connect(self) -> None:
        check_names = self._builder.check_result_names if self.background_coding or self.delegates else None
        escalate = any(tool.type_name == "escalate" for tool in self.tools.values())
        await self._builder.resources.open(check_names, escalate=escalate)

    async def _startup_failed(self, exc: BaseException) -> None:
        await self._builder.resources.close()
        self._builder.resources.startup_failed(exc)

    async def __aexit__(self, *_exc_info: object) -> None:
        try:
            if self.delegates:
                await self.delegates.close()
            await self._coding_tasks.cancel()
        finally:
            await self._builder.resources.close()

    async def run(self, prompt: str, *, workflow: str) -> TurnOutcome:
        self._steering_items = []
        orchestrator = self._builder.agents[self.drop_in.config.orchestrator]
        try:
            try:
                result = await Runner.run(
                    orchestrator,
                    await self._turn_input(prompt, workflow),
                    context=self.context,
                    max_turns=count_limit(self.max_turns),
                    hooks=self.hooks,
                    session=self.session,
                    run_config=replace(self.run_config, workflow_name=workflow),
                )
            except MaxTurnsExceeded:
                outcome = TurnOutcome(final_output="", completed=False)
            else:
                outcome = TurnOutcome(final_output=str(result.final_output), completed=True)
            remaining = await self._coding_tasks.drain()
            if remaining:
                report = "Scaffold coding completion report (uncollected delegate outputs):\n\n" + "\n\n".join(
                    remaining
                )
                await self.session.add_items([{"role": "user", "content": report}])
            outcome.final_output = "\n\n".join(part for part in [outcome.final_output, *remaining] if part)
            if self.delegates:
                outstanding = [task for task in self.delegates.status() if not task['collected']]
                if outstanding:
                    outcome = TurnOutcome(completed=False, final_output=(
                        'Delegate work is outstanding. Read delegate_inbox, answer questions, and collect results '
                        'before finishing. Interrupt work explicitly if it should stop.\n' + json.dumps(outstanding)))
            self._pending_turn_path().unlink(missing_ok=True)
            return outcome
        except BaseException as exc:
            if self.delegates:
                await self.delegates.close()
            await self._coding_tasks.cancel()
            # Compaction has already had its chance to shrink an oversized request.
            if isinstance(exc, BadRequestError):
                raise ModelRequestError from None
            raise

    def _pending_turn_path(self) -> Path:
        """Locate the orchestrator's durable input-admission marker outside its workspace."""
        name = self.drop_in.config.orchestrator
        return self.context.state_dir / f"pending-turn-{hashlib.sha256(name.encode()).hexdigest()}.json"

    async def _turn_input(self, prompt: str, workflow: str) -> str | list:
        """Resume an interrupted turn without saving its task twice.

        :param prompt: Loop-supplied task, retained unchanged in the SDK session.
        :param workflow: Phase identity distinguishing pending tasks with identical text.
        :returns: The new task, or no additional input when that pending task is already durable.
        """
        path = self._pending_turn_path()
        identity = {"workflow": workflow, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        history = await self.session.get_items()
        if path.exists():
            pending = json.loads(path.read_text())
            index = pending["history_items"]
            if all(pending.get(key) == value for key, value in identity.items()) and 0 <= index < len(history):
                item = history[index]
                if item.get("role") == "user" and item.get("content") == prompt:
                    return []
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({**identity, "history_items": len(history)}))
        temporary.replace(path)
        return prompt

    async def judge(
        self,
        name: str,
        rubric: str,
        evidence: str,
        *,
        tools: list[str],
        max_turns: int | None,
        review_workspace: ReviewWorkspace | None = None,
        model: str | None = None,
    ) -> Verdict:
        ctx = self.context
        if review_workspace is not None:
            validate_review_tools([self.tools[tool] for tool in tools])
            ctx = replace(ctx, workspace=review_workspace)
        agent = Agent(
            name=JUDGE if review_workspace is not None else f"{JUDGE}:{name}",
            instructions=rubric,
            output_type=Verdict,
            model=self._builder.resources.connection.model(JUDGE, model),
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
                run_config=RunConfig(workflow_name=f"{JUDGE}:{name}", call_model_input_filter=partial(
                    self._select_cache_input, model=model or ctx.model)),
            )
        except BadRequestError:
            raise ModelRequestError from None
        except MaxTurnsExceeded:
            return Verdict(
                passed=False,
                next_prompt=None,
                feedback=f"The judge ran out of turns ({max_turns}) before reaching a verdict.",
            )
        return result.final_output


    def describe(self) -> list[str]:
        self._build()
        return self._builder.describe()

    async def _select_input(self, data: CallModelData[Any]) -> ModelInputData:
        is_orchestrator = data.agent.name == self.drop_in.config.orchestrator
        steering = self.context.steering
        annotations = []
        delegate_context = self._delegate_context.get()
        session_id = data.agent.name if is_orchestrator else None
        pinned_mail = []
        # TODO(AE-328): Extract mailbox consumption into a separate class method.
        if self.delegates:
            boxes = self.delegates.mailboxes
            if is_orchestrator:
                messages = boxes.pending('pi')
                annotations.append('<delegate_status>\n' + json.dumps(self.delegates.status()) + '\n</delegate_status>')
                if messages:
                    annotations.append('<delegate_messages>\n' + json.dumps(messages) + '\n</delegate_messages>')
                    await self.session.add_items([{'role': 'user', 'content': annotations[-1]}])
                    boxes.delivered([m['id'] for m in messages])
            elif delegate_context and boxes.get(delegate_context[0])['name'] == data.agent.name:
                session_id, pinned_mail = delegate_context
                messages = boxes.pending('delegate', session_id)
                if messages:
                    envelope = {'role': 'user', 'content': '<pi_messages>\n' + json.dumps(messages) + '\n</pi_messages>'}
                    session = self.context_strategy.session(session_id, self.context)
                    await session.add_items([envelope])
                    pinned_mail.append(envelope)
                    boxes.delivered([m['id'] for m in messages])
            self.delegates.flush_events()
        if steering and is_orchestrator:
            await steering.poll()
            pending = steering.pending()
            if pending:
                for message in pending:
                    envelope = steering.envelope(message)
                    try:
                        history = await self.session.get_items()
                        if envelope not in history:
                            await self.session.add_items([envelope])
                            history.append(envelope)
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
        history = [item for item in data.model_data.input if not is_standing_context(item)]
        prompt_item = history[-1] if history and history[-1].get("role") == "user" else None
        if prompt_item is not None and is_envelope(prompt_item):
            prompt_item = None
        if pinned_mail:
            session = self.context_strategy.session(session_id, self.context)
            for index, item in enumerate(await session.get_items()):
                if item in pinned_mail and item not in history:
                    history.insert(index, item)
        if is_orchestrator and self._steering_items:
            stored = [item for item in await self.session.get_items() if not is_standing_context(item)]
            for index, item in enumerate(stored):
                if item in self._steering_items and item not in history:
                    history.insert(index, item)
        if is_orchestrator:
            # After a deliberate restart the orchestrator continues from its handoff; the session keeps everything.
            history = since_restart(history, self._restart_tools)
        strategy = self.context_strategy
        ctx = self.context
        research = self.research_context(data.agent.name)
        status = await ctx.budget_status()
        request_context = {
            "instructions": data.model_data.instructions, "research": research, "resources": status,
            "tools": [{"type": "function", "name": tool.name, "description": tool.description,
                       "parameters": tool.params_json_schema, "strict": tool.strict_json_schema}
                      for tool in await data.agent.get_all_tools(RunContextWrapper(data.context))
                      if isinstance(tool, FunctionTool)],
        }

        prepare_request = partial(
            self._prepare_request, request_context=request_context, prompt_item=prompt_item,
            annotations=annotations, pinned_steering=(self._steering_items if steering and is_orchestrator else []) + pinned_mail,
        )
        items = list(await strategy.select_for_call(
            list(history), ctx=ctx,
            session_id=session_id,
            request_context=request_context, agent=data.agent, model_provider=self.run_config.model_provider,
            prepare_input=self._cache_input, research_context=lambda: self.research_context(data.agent.name),
            prepare_request=prepare_request,
        ))
        positions = {id(item): index for index, item in enumerate(history)}
        retained = [positions.get(id(item)) for item in items]
        self.context.telemetry.record(
            "context:select", "span",
            {"agent": data.agent.name, "strategy": strategy.type_name,
             "max_items": getattr(strategy.options, "max_items", None), "items_before": len(history)},
            {"items_selected": len(items), "items_dropped": len(history) - len(set(retained) - {None}),
             "retained_positions": retained},
        )
        return prepare_request(items)

    async def _select_cache_input(self, data: CallModelData[Any], *, model: str) -> ModelInputData:
        return self._cache_input(data.model_data, model=model)

    def _cache_input(self, data: ModelInputData, *, model: str | None = None) -> ModelInputData:
        """OpenRouter translates these text boundaries to Claude's cache_control blocks.

        Mark stable instructions and retained conversation, never the replaced status tail.
        Normalize text consistently on every request and copy it so markers cannot enter sessions.
        """
        provider = self.model_provider
        if (provider.type != "openai-compatible" or provider.base_url.host != "openrouter.ai"
                or not (model or self.context.model).removeprefix("~").startswith("anthropic/")):
            return data
        selected = list(data.input)
        if data.instructions:
            selected.insert(0, {"role": "developer", "content": data.instructions})
        candidates = []
        explicit = False
        for index, item in enumerate(selected):
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
            texts = [position for position, part in enumerate(content)
                     if isinstance(part, dict) and part.get("type") == "input_text" and part.get("text")]
            if not texts:
                continue
            selected[index] = {**item, field: content}
            candidate = (index, field, texts[-1])
            candidates.append(candidate)
        if candidates and not explicit:
            for index, field, position in {candidates[0], candidates[-1]}:
                selected[index][field][position]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return ModelInputData(input=selected, instructions=None)

    def _prepare_request(
        self, selected: list[Any], *, request_context: dict[str, Any], prompt_item: dict | None,
        annotations: list[str], pinned_steering: list[dict],
    ) -> ModelInputData:
        """Render the same request for token checks and ordinary inference.

        :param selected: History selected by the context strategy.
        :param request_context: Fixed instructions and the latest research and resource context.
        :param prompt_item: Current user task to move to the trailing message.
        :param annotations: Steering transport notices for this call.
        :param pinned_steering: Delivered messages to replay until Runner reloads the session.
        :returns: Cache-formatted history followed by the current context and task.
        """
        items = list(selected)
        if prompt_item is not None:
            for index in range(len(items) - 1, -1, -1):
                if items[index] == prompt_item:
                    items.pop(index)
                    break
        items.extend(item for item in pinned_steering if item not in items)
        tail = [f"<research_context>\n{request_context['research']}\n</research_context>", *annotations,
                f"<resource_status>\n{request_context['resources']}\n</resource_status>"]
        if prompt_item is not None:
            tail.append(f"# Current task\n\n{prompt_item['content']}")
        result = self._cache_input(ModelInputData(input=items, instructions=request_context["instructions"]))
        result.input.append({"role": "user", "content": "\n\n".join(tail)})
        return result

    def _json_tool(self, name: str, description: str, arguments: type[Arguments], callback) -> FunctionTool:
        async def invoke(_: ToolContext[Any], raw: str) -> str:
            try:
                return json.dumps(await callback(arguments.model_validate_json(raw)))
            except (ValueError, ValidationError) as exc:
                return json.dumps({'error': str(exc)})
        return FunctionTool(name=name, description=description, on_invoke_tool=invoke,
                            params_json_schema=ensure_strict_json_schema(arguments.model_json_schema()))

    def _interactive_tool(self, name: str) -> FunctionTool:
        async def start(args):
            return self.delegates.start(name, args.brief)
        config = self.drop_in.config
        description = (config.agents[name].description if name in config.agents else self._builder.coding_agents[name].description)
        return self._json_tool(name, description +
                               ' Starts in the background; collect results with delegate_inbox.', BriefArguments, start)

    def _communication_tools(self, name: str) -> list[FunctionTool]:
        config = self.drop_in.config
        if name != config.orchestrator:
            async def report(args, *, question):
                active = self._delegate_context.get()
                if not active:
                    raise ValueError('No active delegate conversation')
                mailbox = self.delegates.mailboxes.delegate(active[0])
                receipt = mailbox.ask_pi(args.message) if question else mailbox.report_progress(args.message)
                self.delegates.flush_events()
                return receipt
            return [self._json_tool('ask_pi', 'Ask the PI a question without interrupting it. Continue independent work.',
                                    MessageArguments, partial(report, question=True)),
                    self._json_tool('report_progress', 'Report completed work, evidence, blockers and next step to the PI.',
                                    MessageArguments, partial(report, question=False))]

        async def send(args):
            return await self.delegates.send(**args.model_dump())

        async def stop(args):
            return await self.delegates.stop(args.task_id)

        async def progress(args):
            return self.delegates.request_progress(args.task_id)

        async def inbox(args):
            limit = self.coding_result_wait_seconds
            seconds = args.wait_seconds if limit == 'unlimited' else min(args.wait_seconds, limit)
            return await self.delegates.inbox(seconds)

        tools = [self._json_tool('send_message', 'Steer or answer a delegate. Resumes ended conversations; interrupt=true '
                                'stops active work before resuming. Use task IDs from dispatch/inbox.', SendArguments, send),
                 self._json_tool('interrupt_delegate', 'Stop a delegate and await cleanup. Does not resume it.',
                                 TaskArguments, stop),
                 self._json_tool('request_progress', 'Ask an active delegate for a fresh progress report; returns a receipt, '
                                 'not the report. Read delegate_inbox for the response.', TaskArguments, progress),
                 self._json_tool('delegate_inbox', 'Inspect all delegate status and messages and collect every ready result. '
                                 'wait_seconds=0 checks immediately; a positive wait wakes on any new message or task '
                                 'completion, failure or interruption, or returns when idle or timed out. Waiting never '
                                 'stops delegate work.',
                                 InboxArguments, inbox)]
        return tools

    async def _run_delegate(self, task: dict, prompt: str) -> str:
        ctx = self.context
        name = task['name']
        if name in self._builder.coding_agents:
            if prompt != task['brief'] and not self.delegates.mailboxes.get(task['id'])['conversation_id']:
                prompt = task['brief'] + '\n\n' + prompt
            async with self._delegation.slot(background=True):
                result = await self._builder.coding_agents[name].run(
                    prompt, ctx, mailbox=self.delegates.mailboxes.delegate(task['id']))
                if not result.completed:
                    raise RuntimeError(result.as_tool_output())
                return result.as_tool_output()
        session = self.context_strategy.session(task['id'], ctx)
        if prompt != task['brief'] and not await session.get_items():
            prompt = task['brief'] + '\n\n' + prompt
        # Persist the incoming turn before inference so injected mail retains its true chronological position.
        await session.add_items([{'role': 'user', 'content': prompt}])
        token = self._delegate_context.set((task['id'], []))
        try:
            async with self._delegation.slot(background=True):
                result = await Runner.run(self._builder.agents[name], [], context=ctx, session=session, hooks=self.hooks,
                                          max_turns=count_limit(self.drop_in.config.agents[name].max_turns),
                                          run_config=replace(self.run_config, workflow_name=f"delegate:{name}:{task['id']}"))
                return str(result.final_output)
        finally:
            self._delegate_context.reset(token)

    def coding_status(self) -> str:
        return self._coding_tasks.status()


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
