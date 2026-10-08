import hashlib
import io
import json
import tarfile

import pytest

import workspace_copy
from collection import workspace_view
from review import CoverageError
from workspace_copy import archive_workspace


def test_copy_preserves_the_whole_workspace_and_model_limits_never_shorten_archive(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".hidden").mkdir()
    (root / ".hidden/config.json").write_text('{"setting": "preserved"}')
    binary = bytes(range(256)) * 400
    (root / "artifact.bin").write_bytes(binary)
    long = "long research output\n" * 20000
    (root / "results.txt").write_text(long)
    outside = tmp_path / "outside-secret"
    outside.write_text("do not follow links outside the workspace")
    (root / "outside-link").symlink_to(outside)
    archive = tmp_path / "copy.tar.gz"
    info = archive_workspace(root, archive)
    with tarfile.open(archive) as saved:
        assert (
            saved.extractfile("workspace/.hidden/config.json").read() == b'{"setting": "preserved"}'
        )
        assert saved.extractfile("workspace/artifact.bin").read() == binary
        assert saved.extractfile("workspace/results.txt").read().decode() == long
        assert saved.getmember("workspace/outside-link").issym()
    sources, manifest, gaps = workspace_view(io.BytesIO(archive.read_bytes()), info["sha256"])
    assert {m["path"] for m in manifest} == {
        "workspace",
        "workspace/.hidden",
        "workspace/.hidden/config.json",
        "workspace/artifact.bin",
        "workspace/results.txt",
        "workspace/outside-link",
    }
    assert any(s["truncated"] for s in sources)
    assert all("outside-secret" not in s["data"] for s in sources)
    assert gaps and hashlib.sha256(archive.read_bytes()).hexdigest() == info["sha256"]


def test_corrupt_archive_is_not_accepted(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    archive = tmp_path / "copy.tar.gz"
    archive_workspace(root, archive)
    with pytest.raises(CoverageError, match="checksum"):
        workspace_view(io.BytesIO(archive.read_bytes()), "0" * 64)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_whole_archive_stream_is_acknowledged_only_after_upload_succeeds(
    tmp_path, monkeypatch, capsys, exit_code
):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".hidden").write_bytes(bytes(range(256)) * 1000)
    output, arguments = tmp_path / "uploaded.tar.gz", tmp_path / "arguments.json"
    aws = tmp_path / "aws"
    aws.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "assert 'SSM_Credentials' not in os.environ\n"
        "assert os.environ['AWS_ACCESS_KEY_ID'] == 'test-only-access'\n"
        "pathlib.Path(os.environ['TEST_UPLOAD']).write_bytes(sys.stdin.buffer.read())\n"
        "pathlib.Path(os.environ['TEST_ARGS']).write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(int(os.environ['TEST_EXIT']))\n"
    )
    aws.chmod(0o700)
    monkeypatch.setattr(workspace_copy, "ROOTS", (root,))
    monkeypatch.setattr(workspace_copy.shutil, "which", lambda name: str(aws))
    for name, value in {
        "SSM_UploadKey": "workspaces/i-abcdef/900/workspace.tar.gz",
        "SSM_Credentials": json.dumps(
            {
                "AccessKeyId": "test-only-access",
                "SecretAccessKey": "test-only-secret",
                "SessionToken": "test-only-token",
            }
        ),
        "CRUX_ARCHIVE_BUCKET": "private-test-evidence",
        "CRUX_ARCHIVE_REGION": "us-east-1",
        "TEST_UPLOAD": str(output),
        "TEST_ARGS": str(arguments),
        "TEST_EXIT": str(exit_code),
    }.items():
        monkeypatch.setenv(name, value)
    if exit_code:
        with pytest.raises(RuntimeError, match="upload failed"):
            workspace_copy.main()
        assert not capsys.readouterr().out
    else:
        workspace_copy.main()
        info = json.loads(capsys.readouterr().out)
        assert info["bytes"] == output.stat().st_size
        assert info["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
        with tarfile.open(output) as archive:
            assert (
                archive.extractfile("workspace/.hidden").read() == (root / ".hidden").read_bytes()
            )
        args = json.loads(arguments.read_text())
        assert args[:4] == [
            "s3",
            "cp",
            "-",
            "s3://private-test-evidence/workspaces/i-abcdef/900/workspace.tar.gz",
        ]
        assert "--expected-size" in args


def test_ssm_copy_uses_one_object_credentials_and_verifies_saved_snapshot(tmp_path):
    from types import SimpleNamespace

    import boto3
    import httpx
    from moto import mock_aws

    from collection import Collector

    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".evidence").write_text("complete evidence\n")
    archive = tmp_path / "snapshot.tar.gz"
    info = archive_workspace(root, archive)
    key = "workspaces/i-abcdef/900/workspace.tar.gz"

    def assume_role(**request):
        assert json.loads(request["Policy"])["Statement"][0]["Resource"] == (
            "arn:aws:s3:::monitoring-copy-test/" + key
        )
        return {
            "Credentials": {
                "AccessKeyId": "test-access",
                "SecretAccessKey": "test-secret",
                "SessionToken": "test-token",
            }
        }

    def send_command(**request):
        assert request["InstanceIds"] == ["i-abcdef"]
        assert request["DocumentName"] == "dedicated-copy-document"
        assert request["Parameters"]["UploadKey"] == [key]
        assert json.loads(request["Parameters"]["Credentials"][0])["SessionToken"] == "test-token"
        return {"Command": {"CommandId": "test-command"}}

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="monitoring-copy-test")
        s3.put_bucket_versioning(
            Bucket="monitoring-copy-test", VersioningConfiguration={"Status": "Enabled"}
        )
        s3.put_object(Bucket="monitoring-copy-test", Key=key, Body=archive.read_bytes())
        collector = Collector(
            None,
            SimpleNamespace(
                send_command=send_command,
                get_command_invocation=lambda **kwargs: {
                    "Status": "Success",
                    "StandardOutputContent": json.dumps(info),
                },
            ),
            s3,
            None,
            "monitoring-copy-test",
            "dedicated-copy-document",
            SimpleNamespace(assume_role=assume_role),
            "arn:aws:iam::123456789012:role/upload",
        )
        sources, snapshot, manifest, gaps = collector.workspace("i-abcdef", 900)
        assert sources[0]["data"] == "complete evidence\n"
        assert snapshot["version_id"] and snapshot["sha256"] == info["sha256"]
        assert "workspace/.evidence" in {entry["path"] for entry in manifest} and not gaps
        collector.http = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": [], "meta": {}})
            )
        )
        evidence = collector.collect(
            {
                "instance_id": "i-abcdef",
                "state": "running",
                "langfuse": {"environment": "test"},
            },
            900,
            {
                "MONITORING_LANGFUSE_BASE_URL": "https://langfuse.example",
                "MONITORING_LANGFUSE_PUBLIC_KEY": "test-public",
                "MONITORING_LANGFUSE_SECRET_KEY": "test-secret",
            },
        )
        assert any("no observations" in gap for gap in evidence["gaps"])
        assert (
            json.loads(
                s3.get_object(Bucket="monitoring-copy-test", Key=evidence["langfuse_key"])[
                    "Body"
                ].read()
            )
            == []
        )
        collector.http.close()


def test_busy_telemetry_is_archived_while_context_stays_bounded(monkeypatch):
    import boto3
    import httpx
    from moto import mock_aws

    from collection import Collector
    from review import encoded, input_size, select_reviewer

    telemetry = [
        {
            "id": "observation:" + str(index),
            "kind": "langfuse",
            "data": {"model": "gpt-example", "input": "x" * 8_000},
        }
        for index in range(600)
    ]
    telemetry.append(
        {"id": "observation:last", "kind": "langfuse", "data": {"model": "claude-example"}}
    )
    monkeypatch.setattr("collection.collect_langfuse", lambda *args: telemetry)
    with mock_aws(), httpx.Client() as http:
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="busy-test")
        collector = Collector(None, None, s3, http, "busy-test", None, None, None)
        monkeypatch.setattr(collector, "workspace", lambda *args: ([], {}, [], []))
        evidence = collector.collect(
            {"instance_id": "i-test", "state": "running", "langfuse": {"environment": "test"}},
            900,
            {},
        )
        saved = s3.get_object(Bucket="busy-test", Key=evidence["langfuse_key"])["Body"].read()
        assert json.loads(saved) == telemetry
        assert input_size(evidence) < 128 * 1024 and evidence["gaps"]
        assert len(encoded(evidence["sources"])) < 66 * 1024
        # An omitted later model must still prevent a reviewer from evaluating its own family.
        with pytest.raises(CoverageError, match="different model family"):
            select_reviewer(["anthropic/claude-example"], evidence["sources"], [])
