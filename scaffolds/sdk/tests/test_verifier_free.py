import importlib.util
import json
from pathlib import Path

import pytest

from crux_scaffold.cli import main

ROOT = Path(__file__).resolve().parents[1] / "run-harness" / "vftts"
SMOKE = ROOT / "smoke"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "workspace" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_profiles_assemble_without_network(tmp_path, capsys):
    env = {"CRUX_MODEL": "openai/gpt-6.1-sol", "OPENROUTER_API_KEY": "fixture-agent",
           "OPENROUTER_EXPERIMENT_API_KEY": "fixture-experiment", "OPENROUTER_EXPERIMENT_BUDGET_USD": "4",
           "RUN_SLUG": "fixture", "COST_TRACKER_URL": "https://cost.example/cost", "COST_TRACKER_TOKEN": "fixture"}
    for profile in (ROOT, SMOKE):
        assert main(["check", "--drop-in", str(profile), "--state-dir", str(tmp_path / profile.name)], env=env) == 0
    assert "openrouter-experiments" in capsys.readouterr().out


def test_budget_profiles_do_not_spend_full_research_allocation_in_smoke():
    import tomllib
    full = tomllib.loads((ROOT / "scaffold.toml").read_text())
    smoke = tomllib.loads((SMOKE / "scaffold.toml").read_text())
    assert full["budget"]["max_cost_usd"] == 15000
    assert smoke["budget"]["max_cost_usd"] == 8
    assert smoke["budget"]["max_wall_seconds"] == 1200
    assert "limits" not in full["resources"]["experiments"]


def test_failed_review_feedback_reaches_every_phase_continuation():
    import tomllib

    from crux_scaffold.config import PhaseConfig
    from crux_scaffold.drop_in import DropInDirectory
    from crux_scaffold.gates import GateResult
    from crux_scaffold.loop import PhasedLoop, PhasedOptions

    feedback = "Fixture correction: report the actual experimental receipt cost."
    for profile in (ROOT, SMOKE):
        drop_in = DropInDirectory.load(profile)
        for raw in tomllib.loads((profile / "scaffold.toml").read_text())["loop"]["phases"]:
            phase = PhaseConfig.model_validate({**raw, "gates": []})
            loop = PhasedLoop("fixture", PhasedOptions(phases=[phase]), gates={})
            prompt = loop.continuation(drop_in, phase, 1, [GateResult(gate="review", passed=False, feedback=feedback)])
            assert feedback in prompt


def test_aggregation_is_label_free_and_can_overturn_shared_wrong_majority():
    aggregate = load("baselines").aggregate
    samples = [{"answer": "A", "prediction": {"A": .9, "B": .1}, "confidence": .7},
               {"answer": "A", "prediction": {"A": .9, "B": .1}, "confidence": .8},
               {"answer": "B", "prediction": {"A": .8, "B": .2}, "confidence": .9}]
    result = aggregate(samples, ["A", "B"])
    assert result["majority"] == "A"
    assert result["sp_gap"] == result["sp_ratio"] == result["best_of_n_confidence"] == "B"
    with pytest.raises(ValueError):
        aggregate([{**samples[0], "prediction": {"A": .2, "B": .1}}], ["A", "B"])


def test_gpqa_shuffle_and_prompt_do_not_leak_labels():
    prepare = load("prepare_data")
    row = {"Question": "Private fixture question", "Correct Answer": "Right", "Incorrect Answer 1": "Wrong1",
           "Incorrect Answer 2": "Wrong2", "Incorrect Answer 3": "Wrong3"}
    public, label = prepare.gpqa(row)
    assert public == prepare.gpqa(row)[0]
    assert public["choices"][label] == "Right"
    assert not {"label", "answer", "rationale", "Correct Answer"} & public.keys()


def test_hle_subset_excludes_uncertain_images_and_non_mc():
    prepare = load("prepare_data")
    row = {"id": "fixture", "Verified_Classes": "Gold subset", "question": "Fixture\nAnswer Choices:\nA. x\nB. y",
           "answer": "B", "json": json.dumps({"answer_type": "multipleChoice", "image": ""})}
    public, label = prepare.hle(row)
    assert public["id"] == "fixture" and label == "B"
    for changes in ({"Verified_Classes": "Uncertain subset"},
                    {"json": json.dumps({"answer_type": "exactMatch"})},
                    {"json": json.dumps({"answer_type": "multipleChoice", "image": "base64-fixture"})}):
        assert prepare.hle({**row, **changes}) is None


def test_dataset_redirects_strip_cross_host_credentials_and_reject_http():
    from urllib.error import HTTPError
    from urllib.request import Request

    prepare = load("prepare_data")
    handler = prepare.DatasetRedirect()
    request = Request("https://huggingface.co/datasets/fixture", headers={"Authorization": "Bearer fixture-hf"})
    for target, expected in [("https://huggingface.co/redirect", "Bearer fixture-hf"),
                             ("https://cdn.example/fixture", None)]:
        redirected = handler.redirect_request(request, None, 302, "Found", {}, target)
        assert redirected.get_header("Authorization") == expected
    with pytest.raises(HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, "http://cdn.example/fixture")
