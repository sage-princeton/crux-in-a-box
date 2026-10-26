import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/run_archive.py"
spec = importlib.util.spec_from_file_location("run_archive", MODULE_PATH)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
sys.path.insert(0, str(MODULE_PATH.parent))
import archive_workspace as controller


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"
        self.root.mkdir()
        (self.root / "project").mkdir()
        (self.root / "project/ignored.txt").write_text("required ignored asset\n")
        self.plan = {"version": 1, "required_paths": ["project/ignored.txt"],
                     "external_state": [], "secret_refs": [], "restore_checks": ["cat project/ignored.txt"]}
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))

    def test_complete_copy_includes_git_history_and_ignored_files(self):
        subprocess.run(["git", "init", "-q", str(self.root / "project")], check=True)
        (self.root / "project/.gitignore").write_text("ignored.txt\n")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload)
        self.assertTrue((payload / "run/project/.git/HEAD").is_file())
        self.assertTrue((payload / "run/project/ignored.txt").is_file())
        self.assertGreater(archive.verify_tree(payload), 4)

    def test_missing_or_empty_required_path_blocks_snapshot(self):
        for path in ("missing", "project/empty"):
            with self.subTest(path=path):
                (self.root / "project/empty").touch()
                self.plan["required_paths"] = [path]
                (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
                with self.assertRaises(archive.ArchiveError):
                    archive.snapshot(self.root, Path(self.temp.name) / "payload")

    def test_outside_symlink_blocks_archive(self):
        (self.root / "project/outside").symlink_to("/etc/passwd")
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "payload")

    def test_operational_credentials_are_excluded_and_cannot_be_required(self):
        (self.root / ".mcp.json").write_text("credential file")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload)
        self.assertFalse((payload / "run/.mcp.json").exists())
        self.plan["required_paths"] = [".mcp.json"]
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "second")

    def test_changed_content_fails_restore_verification(self):
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload)
        (payload / "run/project/ignored.txt").write_text("corrupt")
        with self.assertRaises(archive.ArchiveError):
            archive.verify_tree(payload)

    def test_scanner_never_reports_secret_values(self):
        finding = {"Raw": "never-print-this-value", "SourceMetadata": {"Data": {
            "Filesystem": {"file": str(self.root / "project/ignored.txt")}}}}
        result = subprocess.CompletedProcess([], 183, json.dumps(finding), "secret in diagnostic")
        with patch.object(archive.subprocess, "run", return_value=result), self.assertRaises(archive.ArchiveError) as error:
            archive.scan(self.root)
        self.assertIn("project/ignored.txt", str(error.exception))
        self.assertNotIn("never-print", str(error.exception))
        self.assertNotIn("diagnostic", str(error.exception))

    def test_extraction_rejects_traversal_and_links(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            with self.subTest(name=name):
                bundle = Path(self.temp.name) / "bad.tar.gz"
                with tarfile.open(bundle, "w:gz") as tar:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    item.linkname = "/etc/passwd"
                    tar.addfile(item)
                with self.assertRaises(archive.ArchiveError):
                    archive.restore(bundle, Path(self.temp.name) / "restored")

    def test_scanner_errors_and_malformed_output_fail_closed(self):
        for code, output in ((1, ""), (0, "invalid json"), (183, "")):
            with self.subTest(code=code, output=output), patch.object(
                    archive.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "")), \
                    self.assertRaises(archive.ArchiveError):
                archive.scan(self.root)

    def test_filesystem_and_all_git_history_are_scanned(self):
        (self.root / "project/.git").mkdir()
        with patch.object(archive.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            archive.scan(self.root)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][1], "git")
        for call in run.call_args_list:
            self.assertIn("--fail-on-scan-errors", call.args[0])
            self.assertIn("--no-verification", call.args[0])

    def test_required_raw_session_path_is_rejected(self):
        (self.root / "state").mkdir()
        (self.root / "state/sessions.sqlite").write_text("private conversation")
        self.plan["required_paths"] = ["state/sessions.sqlite"]
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "payload")

    def test_active_service_is_not_interrupted(self):
        active = subprocess.CompletedProcess([], 0, "active\n", "")
        with patch.object(archive.subprocess, "run", return_value=active) as run, \
                self.assertRaises(archive.ArchiveError):
            archive.remote_capture(self.root, Path(self.temp.name) / "output.tar.gz")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("stop", run.call_args.args[0])

    def test_teardown_never_deletes_when_archival_fails(self):
        scripts = Path(self.temp.name) / "scripts"
        scripts.mkdir()
        source = MODULE_PATH.with_name("teardown-workspace-aws-resources.sh")
        (scripts / source.name).write_text(source.read_text())
        (scripts / "archive_workspace.py").write_text("raise SystemExit(1)\n")
        marker = scripts / "unexpected-delete"
        (scripts / "aux_instance_access.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
        (scripts / "teardown-aux-aws-resources.sh").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        (scripts / "teardown-aux-aws-resources.sh").chmod(0o755)
        bin_dir = scripts / "bin"
        bin_dir.mkdir()
        log = scripts / "aws.log"
        stub = bin_dir / "aws"
        stub.write_text(f"#!/bin/sh\necho \"$*\" >> '{log}'\ncase \"$*\" in\n"
                        "*describe-instances*) echo i-test;;\n*describe-addresses*) echo eip-test;;\nesac\n")
        stub.chmod(0o755)
        config = scripts / "config"
        config.write_text("AWS_PROFILE=main\nAWS_REGION=us-east-1\nRUN_SLUG=test\n")
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
        for flags in (["--yes"], ["--yes", "--keep-aux"]):
            with self.subTest(flags=flags):
                result = subprocess.run(["bash", str(scripts / source.name), str(config), *flags],
                                        env=env, capture_output=True, text=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())
                calls = log.read_text()
                for operation in ("terminate-instances", "release-address", "delete-parameter"):
                    self.assertNotIn(operation, calls)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.config = {"AWS_PROFILE": "main", "AWS_REGION": "us-east-1", "RUN_SLUG": "test"}

    def test_explicit_profile_and_main_account_boundary(self):
        with patch.object(controller, "command", return_value=subprocess.CompletedProcess(
                [], 0, b'{"Account":"111111111111"}', b"")) as run:
            store = controller.Store(self.config)
        self.assertEqual(store.bucket, "crux-run-archives-111111111111-us-east-1")
        self.assertEqual(run.call_args.args[0][1:3], ["--profile", "main"])
        with patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}), \
                self.assertRaises(controller.ArchiveError):
            controller.Store(dict(self.config, AUX_RESOURCE_ACCOUNT_ID="111111111111"))

    def test_download_requires_exact_version_and_checksum(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(controller.Store, "aws",
                return_value={"Account": "111111111111"}):
            store = controller.Store(self.config)
            path = Path(temp) / "bundle"
            path.write_bytes(b"corrupt")
            receipt = {"bucket": store.bucket, "key": "runs/test/id/bundle.tar.gz", "version_id": "v1",
                       "sha256": "wrong"}
            with self.assertRaises(controller.ArchiveError):
                store.download(receipt, path)
            receipt.pop("version_id")
            with self.assertRaises(controller.ArchiveError):
                store.download(receipt, path)

    def test_s3_policy_has_encryption_versioning_privacy_and_tls(self):
        with patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}):
            store = controller.Store(self.config)
        responses = [{"Buckets": []}, {}, {}, {}, {}, {}, {}, {"Status": "Enabled"}]
        with patch.object(store, "aws", side_effect=responses) as run:
            store.ensure_bucket()
        calls = repr(run.call_args_list)
        for item in ("BucketOwnerEnforced", "BlockPublicAcls=true", "Status=Enabled", "AES256", "aws:SecureTransport"):
            self.assertIn(item, calls)

    def test_granted_archive_access_is_read_only_and_prefix_scoped(self):
        with patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}):
            store = controller.Store(self.config)
        with patch.object(store, "aws") as run:
            store.grant_read(dict(self.config, AUX_RESOURCE_ACCOUNT_ID="222222222222"))
        args = run.call_args.args
        self.assertIn("crux-run-test", args)
        policy = json.loads(args[-1])
        self.assertIn("/runs/*", policy["Statement"][0]["Resource"])
        self.assertNotIn("s3:Delete", json.dumps(policy))
