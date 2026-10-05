"""Scripted test doubles for the real agent runtime: models that replay outputs, and a coding agent that
writes declared files."""

import asyncio
import json
import os
import signal

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.tracing import response_span
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from crux_scaffold.coding_agents import CODING_AGENTS, CodingAgent, CodingAgentOptions, CodingResult
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext


def call(name: str, arguments: dict, call_id: str) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(arguments=json.dumps(arguments), call_id=call_id, name=name,
                                    type="function_call", id=f"fc_{call_id}", status="completed")


def say(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(id="msg_1", role="assistant", status="completed", type="message",
                                 content=[ResponseOutputText(annotations=[], text=text, type="output_text")])


class ScriptedModel(Model):
    """Replays scripted model outputs and records what the model was sent. Like the real Responses model, it traces
    each call as a response span."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.instructions: list = []
        self.inputs: list = []

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.instructions.append(system_instructions)
        self.inputs.append(input)
        if not self.turns:
            raise AssertionError("ScriptedModel ran out of scripted turns")
        output = [self.turns.pop(0)]
        with response_span() as span:
            span.span_data.input = input
            span.span_data.response = Response(
                id="resp_scripted", created_at=0, model="scripted", object="response", output=output,
                parallel_tool_calls=True, tool_choice="auto", tools=[],
                usage=ResponseUsage(input_tokens=10, output_tokens=5, total_tokens=15,
                                    input_tokens_details=InputTokensDetails(cached_tokens=0, cache_write_tokens=0),
                                    output_tokens_details=OutputTokensDetails(reasoning_tokens=0)))
        return ModelResponse(output=output, response_id=None,
                             usage=Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15))

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


class StoppedModel(ScriptedModel):
    """The scaffold is stopped, as by `systemctl stop`, while the model is answering."""

    async def get_response(self, *args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(30)


class ScriptedCodingOptions(CodingAgentOptions):
    writes: dict[str, str] = {}
    response: str = "done"


@CODING_AGENTS.register
class ScriptedCodingAgent(CodingAgent):
    """Writes its declared files and returns its declared response; records every brief."""

    type_name = "scripted"
    Options = ScriptedCodingOptions

    def __init__(self, name: str, options: ScriptedCodingOptions, *, developer_instructions: str = "") -> None:
        super().__init__(name, options, developer_instructions=developer_instructions)
        self.briefs: list[str] = []

    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        self.briefs.append(brief)
        for rel, content in self.options.writes.items():
            ctx.workspace.write_file(rel, content)
        return CodingResult(completed=True, final_response=self.options.response,
                            usage=TokenUsage(requests=1, input_tokens=100, output_tokens=50))


def tool_outputs(model_input) -> list[str]:
    """The tool results in what a scripted model was sent."""
    return [item["output"] for item in model_input
            if isinstance(item, dict) and item.get("type") == "function_call_output"]
