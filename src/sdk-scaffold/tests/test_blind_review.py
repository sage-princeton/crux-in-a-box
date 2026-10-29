import asyncio
import json
import os

import pytest

from crux_scaffold.agent_runtimes.openai_agents_sdk import Verdict
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.gates import GATES, GateContext
from crux_scaffold.telemetry import NullTelemetry

from drop_ins import edit
from scripted import ScriptedModel, call, say


def submission_pdf():
    stream = b"BT /F1 16 Tf 40 180 Td (Result: 42) Tj ET 0 0 1 rg 40 40 80 80 re f"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 240] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


def configure_blind(root, extra=""):
    (root / "rubric.md").write_text("Verify the result using the submission and its figure.")
    source = root / "workspace/author/revision-7"
    source.mkdir(parents=True)
    (source / "draft.pdf").write_bytes(submission_pdf())
    (source / "data.csv").write_text("result\n42\n")
    (root / "workspace/LOG.md").write_text("PRIVATE previous review: expected score 10")
    edit(root, "scaffold.toml", 'continue_prompt = "prompts/continue.md"',
         'continue_prompt = "prompts/continue.md"\ngates = ["revision_7"]\nmax_iterations = 2')
    with (root / "scaffold.toml").open("a") as config:
        config.write('''
[gates.revision_7]
type = "llm_judge"
rubric = "rubric.md"
blind = true
artifacts = { "submission.pdf" = "author/revision-7/draft.pdf", "data.csv" = "author/revision-7/data.csv" }
tools = ["read_file", "list_files", "read_pdf"]
''' + extra)


class Evaluations(NullTelemetry):
    def __init__(self):
        self.records = []

    def record(self, *args):
        self.records.append(args)


def test_blind_review_through_the_runtime_is_restricted_fresh_and_metered(drop_in_dir, assemble):
    configure_blind(drop_in_dir)
    verdicts = [Verdict(passed=False, feedback="FIRST_REVIEW", next_prompt="Revise."),
                Verdict(passed=True, feedback="Figure and data agree.", next_prompt=None)]
    turns = []
    for i, verdict in enumerate(verdicts):
        turns.extend([call("list_files", {"path": "."}, f"list{i}"),
                      call("read_file", {"path": "LOG.md"}, f"denied{i}"),
                      call("read_file", {"path": "data.csv"}, f"data{i}"),
                      call("read_pdf", {"path": "submission.pdf", "page": 1}, f"pdf{i}"),
                      say(verdict.model_dump_json())])
    judge = ScriptedModel(*turns)
    telemetry = Evaluations()
    scaffold = assemble(drop_in_dir, {"pm": ScriptedModel(say("AUTHOR_OUTPUT"), say("SECOND_OUTPUT")),
                                      "judge": judge}, telemetry=telemetry)
    assert asyncio.run(scaffold.run()).status == "completed"
    for first_input in (judge.inputs[0], judge.inputs[5]):
        assert first_input == [{"role": "user", "content":
                                "Available submission artifacts:\n- data.csv\n- submission.pdf"}]
    received = str(judge.inputs) + str(judge.instructions)
    for forbidden in ("AUTHOR_OUTPUT", "SECOND_OUTPUT", "PRIVATE", "revision-7", "revision_7", "Phase",
                      str(drop_in_dir), "expected score", "resource_status"):
        assert forbidden not in received
    assert "FIRST_REVIEW" not in str(judge.inputs[5:])
    assert "error: artifact unavailable" in received
    assert "result\\n42" in received
    assert "Result: 42" in received
    assert "data:image/png;base64," in received
    assert scaffold.context.usage.by_source["judge:revision_7"].requests == 10
    records = [row for row in telemetry.records if row[0] == "gate:revision_7"]
    assert [row[3]["feedback"] for row in records] == [v.feedback for v in verdicts]


@pytest.mark.parametrize("replacement", [
    'tools = ["write_file"]', 'tools = ["budget_status"]',
    'tools = ["custom_check"]',
])
def test_blind_mode_rejects_unrestricted_tools_before_any_model_call(drop_in_dir, assemble, replacement):
    configure_blind(drop_in_dir)
    edit(drop_in_dir, "scaffold.toml", 'tools = ["read_file", "list_files", "read_pdf"]', replacement)
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write('\n[tools.custom_check]\ntype = "command"\ncommand = "pwd"\ndescription = "check"\n')
    with pytest.raises(InvalidDropInError, match="blind.*tools"):
        assemble(drop_in_dir)


@pytest.mark.parametrize("change", [
    {"inspect": ["LOG.md"]}, {"artifacts": {}}, {"artifacts": {"../round-2": "x"}},
    {"artifacts": {"paper": "../LOG.md"}}, {"artifacts": {"paper": "/etc/passwd"}},
    {"blind": False, "artifacts": {"paper": "x"}}, {"blind": "yes"},
])
def test_invalid_evidence_policies_are_configuration_errors(change):
    with pytest.raises(InvalidDropInError):
        GATES.create("review", {"type": "llm_judge", "rubric": "rubric.md", "blind": True,
                                "artifacts": {"paper": "paper.md"}, **change})


def test_a_tool_alias_cannot_replace_a_blind_reader_with_a_shell(drop_in_dir, assemble):
    configure_blind(drop_in_dir)
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write('\n[tools.read_pdf]\ntype = "command"\ncommand = "pwd"\ndescription = "check"\n')
    with pytest.raises(InvalidDropInError, match="blind.*tools"):
        assemble(drop_in_dir)


def test_direct_runtime_calls_also_enforce_blind_tool_access(drop_in_dir, assemble):
    from crux_scaffold.workspace import ReviewWorkspace

    configure_blind(drop_in_dir)
    judge = ScriptedModel()
    scaffold = assemble(drop_in_dir, {"judge": judge})
    with pytest.raises(InvalidDropInError, match="blind.*tools"):
        asyncio.run(scaffold.runtime.judge("review", "rubric", "evidence", tools=["write_file"], max_turns=2,
                                          review_workspace=ReviewWorkspace({"data.csv": b"42"})))
    assert judge.inputs == []


@pytest.mark.parametrize("source", ["missing.pdf", "LOG.md", "author", "directory", "fifo", "root"])
def test_missing_files_and_symlink_escapes_fail_closed(drop_in_dir, assemble, source):
    configure_blind(drop_in_dir)
    if source == "LOG.md":
        (drop_in_dir / "workspace/link").symlink_to(drop_in_dir / "workspace/LOG.md")
        source = "link"
    elif source == "author":
        (drop_in_dir / "workspace/link").symlink_to(drop_in_dir / "workspace/author", target_is_directory=True)
        source = "link/revision-7/draft.pdf"
    elif source == "directory":
        source = "author/revision-7"
    elif source == "fifo":
        os.mkfifo(drop_in_dir / "workspace/pipe")
        source = "pipe"
    elif source == "root":
        (drop_in_dir / "workspace").rename(drop_in_dir / "original-workspace")
        (drop_in_dir / "workspace").symlink_to(drop_in_dir / "original-workspace", target_is_directory=True)
        source = "author/revision-7/draft.pdf"
    edit(drop_in_dir, "scaffold.toml", "author/revision-7/draft.pdf", source)
    judge = ScriptedModel()
    scaffold = assemble(drop_in_dir, {"judge": judge})
    gate = scaffold.loop.gates["revision_7"]
    ctx = GateContext(scaffold.context, scaffold.drop_in, scaffold.runtime, "SECRET_PHASE", 99, "SECRET_OUTPUT")
    result = asyncio.run(gate.check(ctx))
    assert not result.passed
    assert result.feedback == "Submission artifact unavailable: submission.pdf"
    assert judge.inputs == []


def test_oversize_artifacts_fail_without_sending_truncated_evidence(drop_in_dir, assemble, monkeypatch):
    from crux_scaffold.workspace import ReviewWorkspace

    configure_blind(drop_in_dir)
    monkeypatch.setattr(ReviewWorkspace, "MAX_ARTIFACT_BYTES", 2)
    scaffold = assemble(drop_in_dir)
    with pytest.raises(ValueError, match="Submission artifact unavailable: paper"):
        ReviewWorkspace.snapshot(scaffold.context.workspace, {"paper": "author/revision-7/draft.pdf"})


def test_snapshot_survives_source_changes_and_returns_only_generic_errors(drop_in_dir, assemble):
    from crux_scaffold.workspace import ReviewWorkspace

    configure_blind(drop_in_dir)
    scaffold = assemble(drop_in_dir)
    workspace = ReviewWorkspace.snapshot(scaffold.context.workspace, {"data.csv": "author/revision-7/data.csv"})
    (drop_in_dir / "workspace/author/revision-7/data.csv").write_text("CHANGED")
    assert workspace.read_file("data.csv") == "result\n42\n"
    assert workspace.list_files(".") == "data.csv"
    for path in ("../LOG.md", "/etc/passwd", "author", "./data.csv"):
        with pytest.raises(ValueError, match="^artifact unavailable$"):
            workspace.read_file(path)
    with pytest.raises(ValueError, match="read-only"):
        workspace.run_shell("pwd", 1)
    with pytest.raises(ValueError, match="read-only"):
        workspace.write_file("data.csv", "x")


def test_bad_pdf_and_page_errors_are_returned_to_the_reviewer(drop_in_dir, assemble):
    configure_blind(drop_in_dir)
    judge = ScriptedModel(call("read_pdf", {"path": "data.csv", "page": 1}, "bad"),
                          call("read_pdf", {"path": "submission.pdf", "page": 2}, "page"),
                          say(json.dumps({"passed": False, "feedback": "no", "next_prompt": None})))
    scaffold = assemble(drop_in_dir, {"judge": judge})
    gate = scaffold.loop.gates["revision_7"]
    ctx = GateContext(scaffold.context, scaffold.drop_in, scaffold.runtime, "build", 1, "secret")
    result = asyncio.run(gate.check(ctx))
    assert not result.passed
    assert "error: cannot read PDF page" in str(judge.inputs[1])
    assert "error: PDF page must be between 1 and 1" in str(judge.inputs[2])
