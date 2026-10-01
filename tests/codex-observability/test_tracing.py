"""What the pinned plugin exports for each Codex rollout shape crux-web-pilot hit.

Every test reads the behavior it checks from the plugin version's expectations
file and compares the export with the ground truth the fixture recorded.
"""
from __future__ import annotations

import collections
import os
from datetime import datetime

import pytest

from collector import Collector, Span

MS = 1_000_000


def behavior(expect: dict, key: str, *known):
    value = expect[key]
    if value not in known:
        pytest.fail(f"Expectation {key}={value!r} is not a behavior these tests know how to check. "
                    f"Teach test_tracing.py the new behavior before trusting this plugin version.")
    return value


def by_trace(spans: list[Span]) -> dict[str, list[Span]]:
    traces = collections.defaultdict(list)
    for span in spans:
        traces[span.trace_id].append(span)
    return dict(traces)


def root_of(trace: list[Span]) -> Span:
    roots = [s for s in trace if s.parent_id is None]
    assert len(roots) == 1, [s.name for s in roots]
    return roots[0]


def roots_in_order(spans: list[Span]) -> list[Span]:
    return sorted((root_of(t) for t in by_trace(spans).values()), key=lambda s: s.start_ns)


def ancestors(span: Span, spans: list[Span]) -> list[Span]:
    index = {s.span_id: s for s in spans}
    chain = []
    while span.parent_id:
        span = index[span.parent_id]
        chain.append(span)
    return chain


def ns(at: datetime) -> int:
    return int(at.timestamp() * 1000) * MS


def assert_turn_root(root: Span, turn, expect):
    assert root.name == expect["names"]["turn"]
    assert root.type == "agent"
    assert root.input == turn.prompt


def test_long_turn_exports_every_tool_call_and_model_step(traced, expect):
    scenario, spans, _ = traced("long_turn")
    [turn] = scenario.turns
    [trace] = by_trace(spans).values()
    root = root_of(trace)
    assert_turn_root(root, turn, expect)
    assert root.output == turn.final_text
    assert {s.attributes.get("session.id") for s in spans} == {turn.thread_id}

    behavior(expect, "tool_spans", "one_per_tool_call_named_as_called")
    tools = [s for s in trace if s.type == "tool"]
    assert collections.Counter(s.name for s in tools) == collections.Counter(turn.tool_names)

    behavior(expect, "generations", "one_per_model_step")
    generations = [s for s in trace if s.type == "generation"]
    assert len(generations) == len(turn.steps)
    assert {s.name for s in generations} == {expect["names"]["generation"]}


def test_generation_spans_follow_the_versions_timing_rule(traced, expect):
    behavior(expect, "generation_timing", "first_message_or_tool_call_to_first_tool_call")
    scenario, spans, _ = traced("long_turn")
    generations = sorted((s for s in spans if s.type == "generation"), key=lambda s: s.start_ns)
    for span, step in zip(generations, scenario.turns[0].steps, strict=True):
        start = step.message_at or step.first_tool_at
        end = step.first_tool_at or step.closed_at
        assert abs(span.start_ns - ns(start)) <= MS and abs(span.end_ns - ns(end)) <= MS, \
            f"generation {span.start_ns}..{span.end_ns} vs rollout {start}..{end}"


def test_tool_spans_run_from_call_to_output(traced, expect):
    behavior(expect, "tool_timing", "call_to_output")
    scenario, spans, _ = traced("long_turn")
    truth = [t for s in scenario.turns[0].steps for t in s.tools if t.kind == "function"]
    tools = sorted((s for s in spans if s.type == "tool" and s.name != "web_search"), key=lambda s: s.start_ns)
    for span, tool in zip(tools, truth, strict=True):
        assert span.name == tool.name
        assert abs(span.start_ns - ns(tool.call_at)) <= MS and abs(span.end_ns - ns(tool.output_at)) <= MS


def test_subagent_threads_are_nested_in_the_spawning_turn(traced, expect):
    behavior(expect, "subagents", "nested_in_parent_turn")
    scenario, spans, _ = traced("subagents")
    [turn] = scenario.turns
    [trace] = by_trace(spans).values()
    root = root_of(trace)
    assert_turn_root(root, turn, expect)

    child_turns = [t for child in scenario.children for t in child.turns]
    sub_turns = [s for s in trace if s.name == expect["names"]["subagent_turn"]]
    assert len(sub_turns) == len(child_turns)
    assert sorted(s.input for s in sub_turns) == sorted(t.prompt for t in child_turns)
    assert all(root in ancestors(s, trace) for s in sub_turns)

    sub_generations = [s for s in trace if s.name == expect["names"]["subagent_generation"]]
    assert len(sub_generations) == sum(len(t.steps) for t in child_turns)
    tools = collections.Counter(s.name for s in trace if s.type == "tool")
    assert tools == collections.Counter(turn.tool_names + [n for t in child_turns for n in t.tool_names])


def test_thread_events_between_turns_do_not_create_turns(traced, expect):
    behavior(expect, "turns_between_thread_events", "no_phantom_turns")
    scenario, spans, _ = traced("turns_with_thread_events")
    roots = roots_in_order(spans)
    assert [r.input for r in roots] == [t.prompt for t in scenario.turns]
    assert [r.output for r in roots] == [t.final_text for t in scenario.turns]


def test_interrupted_turn_is_uploaded_and_flagged_on_the_next_stop(traced, expect):
    flags = expect["interrupted_turn"]
    assert flags["uploaded_on_next_stop"] is True, "teach this test the new behavior"
    scenario, spans, _ = traced("interrupted")
    aborted, short = scenario.turns
    first, second = roots_in_order(spans)
    assert_turn_root(first, aborted, expect)
    assert (first.level, first.attributes.get("langfuse.observation.status_message")) == \
        (flags["level"], flags["status_message"])
    assert first.meta("codex.aborted") == flags["aborted_metadata"]

    behavior(expect, "subagents", "nested_in_parent_turn")
    sub_turns = [s for s in spans if s.name == expect["names"]["subagent_turn"]]
    assert {s.trace_id for s in sub_turns} == {first.trace_id}
    child_tools = [n for child in aborted.children for t in child.turns for n in t.tool_names]
    assert collections.Counter(s.name for s in spans if s.type == "tool" and s.trace_id == first.trace_id) == \
        collections.Counter(aborted.tool_names + child_tools)

    assert_turn_root(second, short, expect)
    assert second.level == "DEFAULT" and second.output == short.final_text


def test_goal_continuation_turns_are_each_traced_once_as_their_own_turn(rollouts, stop_hook, tmp_path, expect):
    """Codex fires Stop at the end of each turn, before its task_complete lands, then starts the next goal turn."""
    behavior(expect, "goal_continuation", "own_trace_with_internal_prompt_as_input")
    behavior(expect, "stop_before_task_complete", "uploaded_once")
    scenario = rollouts.goal_continuation()
    sessions = tmp_path / "sessions"
    with Collector() as collector:
        for turn, end in zip(scenario.turns, _task_complete_indexes(scenario.main), strict=True):
            stop_hook(scenario.main.write(sessions, upto=end), collector, turn)
    roots = roots_in_order(collector.spans)
    assert [r.input for r in roots] == [t.prompt for t in scenario.turns]
    assert all(r.input == rollouts.GOAL_PROMPT for r in roots[1:])
    assert [r.output for r in roots] == [t.final_text for t in scenario.turns]
    assert {r.name for r in roots} == {expect["names"]["turn"]}
    assert [r.export for r in roots] == list(range(1, len(scenario.turns) + 1)), \
        "each Stop should upload exactly the turn that stopped"
    for root, turn in zip(roots, scenario.turns):
        tools = [s.name for s in collector.spans if s.trace_id == root.trace_id and s.type == "tool"]
        assert collections.Counter(tools) == collections.Counter(turn.tool_names)


def _task_complete_indexes(rollout) -> list[int]:
    return [i for i, line in enumerate(rollout.lines)
            if line["type"] == "event_msg" and line["payload"]["type"] == "task_complete"]


def _task_complete_index(rollout) -> int:
    return _task_complete_indexes(rollout)[0]


def test_hook_run_mid_turn_uploads_nothing(rollouts, stop_hook, tmp_path, expect):
    """A hook fired on a timer mid-turn (no Stop turn_id), the way to watch a long turn live."""
    behavior(expect, "in_progress_turn_without_stop_turn_id", "not_uploaded")
    scenario = rollouts.long_turn()
    transcript = scenario.main.write(tmp_path / "sessions", upto=_task_complete_index(scenario.main) - 40)
    with Collector() as collector:
        stop_hook(transcript, collector)
    assert collector.spans == []


def test_stop_before_task_complete_uploads_the_turn_once(rollouts, stop_hook, tmp_path, expect):
    """Codex's Stop hook can read the rollout before the turn's task_complete line lands."""
    behavior(expect, "stop_before_task_complete", "uploaded_once")
    scenario = rollouts.long_turn()
    [turn] = scenario.turns
    sessions = tmp_path / "sessions"
    with Collector() as collector:
        stop_hook(scenario.main.write(sessions, upto=_task_complete_index(scenario.main)), collector, turn)
        first = {(s.trace_id, s.span_id) for s in collector.spans}
        stop_hook(scenario.main.write(sessions), collector, turn)
    assert first, "the stopped turn was not uploaded"
    assert {s.trace_id for s in collector.spans} == {t for t, _ in first}
    assert {(s.trace_id, s.span_id) for s in collector.spans} == first


def test_reupload_after_lost_sidecar_reuses_trace_and_span_ids(rollouts, stop_hook, tmp_path, expect):
    behavior(expect, "reupload_after_lost_sidecar", "same_trace_and_span_ids")
    scenario = rollouts.subagents()
    transcript = scenario.write(tmp_path / "sessions")
    with Collector() as collector:
        stop_hook(transcript, collector, scenario.turns[-1])
        os.remove(f"{transcript}.langfuse")
        stop_hook(transcript, collector, scenario.turns[-1])
    exports = collections.defaultdict(set)
    for s in collector.spans:
        exports[s.export].add((s.trace_id, s.span_id))
    assert len(exports) == 2 and exports[1] == exports[2]


def test_pilot_sized_rollout_uploads_within_the_hook_timeout(traced, expect):
    scenario, spans, result = traced("pilot_sized")
    assert result.seconds < expect["pilot_sized_hook_seconds_max"], \
        f"upload took {result.seconds:.1f}s; Codex kills the Stop hook at 30s"
    [turn] = scenario.turns
    child_turns = [t for child in scenario.children for t in child.turns]
    assert len([s for s in spans if s.name == expect["names"]["subagent_turn"]]) == len(child_turns)
    assert len([s for s in spans if s.type == "tool"]) == \
        len(turn.tool_names) + sum(len(t.tool_names) for t in child_turns)
