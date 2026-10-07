import pytest


@pytest.fixture(autouse=True)
def isolate_github_output_files(tmp_path, monkeypatch):
    # CI helpers must write test reports to scratch files, never the runner's files.
    for name in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
        monkeypatch.setenv(name, str(tmp_path / name))
