import asyncio
import csv
import io
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.steering import Escalation, Steering, SteeringOptions
from crux_scaffold.telemetry import NullTelemetry


def response(*messages, cursor=""):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["MsgID", "UserID", "Text", "ThreadTs", "Cursor"])
    for index, (ts, user, text) in enumerate(messages):
        writer.writerow([ts, user, text, "", cursor if index == len(messages) - 1 else ""])
    return SimpleNamespace(is_error=False, content=[SimpleNamespace(type="text", text=output.getvalue())])


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Recorder(NullTelemetry):
    def __init__(self):
        self.events = []

    def record(self, name, kind, input, output):
        self.events.append((name, input, output))


def make(tmp_path, transport, telemetry=None, **options):
    policy = SteeringOptions(server="slack", channel_id="C123", operator_ids=["U_OP"], after_ts="1.000000",
                             **options)
    return Steering(policy, tmp_path, transport, telemetry or NullTelemetry())


def test_paginated_history_is_ordered_filtered_and_durable(tmp_path):
    transport = Transport(response(("4.000000", "U_OP", "new\nline, comma"), cursor="page2"),
                          response(("3.000000", "U_BOT", "ignore"), ("2.000000", "U_OP", "first")),
                          response(("4.000000", "U_OP", "new\nline, comma")))
    steering = make(tmp_path, transport)
    asyncio.run(steering.poll())
    assert [m["text"] for m in steering.pending()] == ["first", "new\nline, comma"]
    assert transport.calls[1][1]["cursor"] == "page2"
    assert transport.calls[1][1]["limit"] == ""
    steering.delivered("2.000000")
    restarted = make(tmp_path, transport)
    asyncio.run(restarted.poll())
    assert [m["ts"] for m in restarted.pending()] == ["4.000000"]
    assert restarted.state["cursor"] == "4.000000"
    assert len(restarted.state["inbox"]) == 2


def test_thread_scan_traverses_all_pages_even_when_first_page_is_old(tmp_path):
    transport = Transport(response(("1.000000", "U_OP", "parent"), cursor="next"),
                          response(("2.000000", "U_OP", "reply")))
    steering = make(tmp_path, transport, thread_ts="1.000000")
    asyncio.run(steering.poll())
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == "conversations_replies"
    assert transport.calls[1][1]["thread_ts"] == "1.000000"
    assert steering.pending()[0]["text"] == "reply"


def test_failure_mid_scan_keeps_cursor_and_recovers_without_skipping(tmp_path):
    transport = Transport(response(("4.000000", "U_OP", "four"), cursor="next"),
                          RuntimeError("transport credential must never be copied"),
                          response(("4.000000", "U_OP", "four"), ("2.000000", "U_OP", "two")))
    telemetry = Recorder()
    steering = make(tmp_path, transport, telemetry)
    asyncio.run(steering.poll())
    assert steering.state["cursor"] == "1.000000"
    assert steering.pending() == []
    assert "check" in steering.state["status"]
    asyncio.run(steering.poll())
    assert [m["ts"] for m in steering.pending()] == ["2.000000", "4.000000"]
    assert "credential must" not in json.dumps(telemetry.events) + steering.path.read_text()


@pytest.mark.parametrize("result", [
    response(("invalid", "U_OP", "bad")),
    SimpleNamespace(is_error=True, content=[]),
    SimpleNamespace(is_error=False, content=[SimpleNamespace(type="text", text="unknown response")]),
])
def test_unrecognized_or_failed_responses_cannot_advance_cursor(tmp_path, result):
    steering = make(tmp_path, Transport(result))
    asyncio.run(steering.poll())
    assert steering.state["cursor"] == "1.000000"
    assert steering.state["status"] != "ok"


def test_scan_limit_is_visible_and_does_not_commit_partial_history(tmp_path):
    steering = make(tmp_path, Transport(response(("4.000000", "U_OP", "four"), cursor="next")), max_pages=1)
    asyncio.run(steering.poll())
    assert steering.state["cursor"] == "1.000000"
    assert steering.pending() == []
    assert "max_pages" in steering.state["status"]


def test_state_cannot_silently_change_source_or_operator_authority(tmp_path):
    steering = make(tmp_path, Transport())
    steering.save()
    with pytest.raises(InvalidDropInError, match="steering state"):
        Steering(steering.options.model_copy(update={"operator_ids": ["U_OTHER"]}), tmp_path,
                 Transport(), NullTelemetry())


@pytest.mark.parametrize("update", [
    {"operator_ids": []}, {"operator_ids": [""]}, {"poll_interval_seconds": float("inf")}, {"after_ts": "NaN"},
    {"poll_interval_seconds": 0}, {"channel_id": ""}, {"unknown": True},
])
def test_policy_is_strict(update):
    with pytest.raises(ValidationError):
        SteeringOptions.model_validate({"server": "slack", "channel_id": "C123", "operator_ids": ["U_OP"],
                                        "after_ts": "1.000000", **update})


def escalation():
    return Escalation(request_id="database-down", failure="Database unavailable", diagnosis="Retried; timed out",
                      requested_action="Restore database access", meanwhile="Write offline tests")


def test_escalation_is_persisted_posted_once_and_does_not_wait_for_response(tmp_path):
    telemetry = Recorder()
    transport = Transport(SimpleNamespace(is_error=False, content=[]))
    steering = make(tmp_path, transport, telemetry, thread_ts="1.000000")
    result = asyncio.run(steering.escalate(escalation()))
    assert "posted" in result
    restarted = make(tmp_path, transport, telemetry, thread_ts="1.000000")
    assert asyncio.run(restarted.escalate(escalation())) == result
    assert len(transport.calls) == 1
    assert transport.calls[0][1]["thread_ts"] == "1.000000"
    for field in ("Database unavailable", "Retried; timed out", "Restore database access", "Write offline tests"):
        assert field in transport.calls[0][1]["payload"]
    assert "Database unavailable" not in json.dumps(telemetry.events)
    with pytest.raises(ToolError, match="request_id"):
        asyncio.run(restarted.escalate(escalation().model_copy(update={"failure": "different"})))


def test_uncertain_post_is_not_automatically_repeated_after_restart(tmp_path):
    transport = Transport(TimeoutError("secret transport detail"))
    steering = make(tmp_path, transport)
    assert "uncertain" in asyncio.run(steering.escalate(escalation()))
    restarted = make(tmp_path, transport)
    assert "verify" in asyncio.run(restarted.escalate(escalation()))
    assert len(transport.calls) == 1
    assert "secret transport" not in steering.path.read_text()


def test_restart_during_post_marks_it_uncertain(tmp_path):
    steering = make(tmp_path, Transport())
    steering.state["outbox"]["database-down"] = {"request": escalation().model_dump(), "status": "sending"}
    steering.save()
    restarted = make(tmp_path, Transport())
    assert "uncertain" in asyncio.run(restarted.escalate(escalation()))


def test_recipient_is_bound_to_durable_state(tmp_path):
    steering = make(tmp_path, Transport())
    steering.save()
    with pytest.raises(InvalidDropInError, match="steering state"):
        Steering(steering.options, tmp_path, Transport(), NullTelemetry(), recipient="other_agent")
