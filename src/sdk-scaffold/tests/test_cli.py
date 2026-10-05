import signal

import pytest

from crux_scaffold import cli
from crux_scaffold.cli import main
from crux_scaffold.runtimes.base import RUNTIMES
from crux_scaffold.telemetry import LangfuseTelemetry, RunIdentity

from drop_ins import edit
from scripted import ScriptedModel, StoppedModel, say


def run_cli(drop_in_dir, tmp_path, models, command="run"):
    return main([command, "--drop-in", str(drop_in_dir), "--state-dir", str(tmp_path / "state")],
                env={"CRUX_MODEL": "unscripted"}, runtime_overrides={"models": models})


def test_check_prints_the_assembly_without_calling_a_model(drop_in_dir, tmp_path, capsys):
    models = {"pm": ScriptedModel(), "reviewer": ScriptedModel()}
    assert run_cli(drop_in_dir, tmp_path, models, "check") == 0
    out = capsys.readouterr().out
    assert "runtime openai-agents; orchestrator pm" in out
    assert "pm: tools [read_file, write_file, rest, engineer, reviewer] mcp []" in out
    assert "engineer: coding agent scripted" in out
    assert "phase main: gates [] max_iterations 1" in out
    assert models["pm"].inputs == []


def test_unresolved_placeholders_stop_the_run_before_any_model_call(drop_in_dir, tmp_path, capsys):
    edit(drop_in_dir, "PROMPT.md", "channel C123", "channel {{SLACK_CHANNEL_ID}}")
    pm = ScriptedModel(say("should not run"))
    assert run_cli(drop_in_dir, tmp_path, {"pm": pm}) == 2
    assert "PROMPT.md:1: {{SLACK_CHANNEL_ID}}" in capsys.readouterr().err
    assert pm.inputs == []


def test_run_prints_the_outcome_and_warns_when_tracing_is_off(drop_in_dir, tmp_path, capsys):
    assert run_cli(drop_in_dir, tmp_path, {"pm": ScriptedModel(say("All done."))}) == 0
    captured = capsys.readouterr()
    assert captured.out == "loop completed in phase main\nAll done.\n"
    assert "tracing off" in captured.err


def test_a_loop_that_does_not_complete_exits_3(drop_in_dir, tmp_path, capsys):
    with (drop_in_dir / "scaffold.toml").open("a") as toml:
        toml.write('\n[loop]\ntype = "phased"\n\n[[loop.phases]]\nname = "main"\nprompt = "PROMPT.md"\n'
                   'gates = ["never"]\n\n[gates.never]\ntype = "command"\ncommand = "false"\n')
    assert run_cli(drop_in_dir, tmp_path, {"pm": ScriptedModel(say("tried"))}) == 3
    assert capsys.readouterr().out.startswith("loop iterations_exhausted in phase main")


def test_sigterm_stops_the_run_cleanly_so_a_restart_resumes_it(drop_in_dir, tmp_path, capsys):
    assert run_cli(drop_in_dir, tmp_path, {"pm": StoppedModel()}) == 128 + signal.SIGTERM
    assert "stopped by SIGTERM" in capsys.readouterr().err
    assert not (tmp_path / "state" / "state.json").exists()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_probe_requires_langfuse(capsys):
    assert main(["probe"], env={}) == 1
    assert "LANGFUSE_PUBLIC_KEY" in capsys.readouterr().err


class AuthorizedClient:
    def auth_check(self):
        return True

    def flush(self):
        return None


@pytest.fixture
def probe_telemetry(monkeypatch):
    telemetry = LangfuseTelemetry(AuthorizedClient(), RunIdentity.from_env({}))
    monkeypatch.setattr(LangfuseTelemetry, "trace", lambda self, name: __import__("contextlib").nullcontext())
    return telemetry


def test_probe_passes_only_when_every_probed_agent_answers(monkeypatch, probe_telemetry, capsys):
    runtime = RUNTIMES.get("openai-agents")

    async def answer(env, prompt, telemetry):
        return cli.PROBE_MARKER

    async def coding_answer(type_name, env, telemetry, request):
        return "something else"

    monkeypatch.setattr(runtime, "probe", answer)
    assert main(["probe"], env={}, telemetry=probe_telemetry) == 0
    assert "SCAFFOLD-PROBE ok: openai-agents; traced to Langfuse environment=local" in capsys.readouterr().out
    monkeypatch.setattr(cli, "probe_coding_agent", coding_answer)
    assert main(["probe", "--coding-agent", "codex"], env={}, telemetry=probe_telemetry) == 1
    assert "codex did not answer SCAFFOLD-PROBE" in capsys.readouterr().err
