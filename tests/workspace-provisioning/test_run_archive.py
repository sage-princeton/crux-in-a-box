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

MODULE_PATH = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/create_archive.py"
spec = importlib.util.spec_from_file_location("create_archive", MODULE_PATH)
archive = importlib.util.module_from_spec(spec)
sys.modules["create_archive"] = archive
spec.loader.exec_module(archive)
sys.path.insert(0, str(MODULE_PATH.parent))
import store_workspace_archive as controller


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture state for each regression case."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"
        self.root.mkdir()
        (self.root / "project").mkdir()
        (self.root / "project/ignored.txt").write_text("required ignored asset\n")
        self.plan = {"version": 1, "required_paths": ["project/ignored.txt"],
                     "external_state": [], "restore_checks": ["cat project/ignored.txt"]}
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))

    def test_complete_copy_includes_git_history_and_ignored_files(self):
        """Verify complete copy includes git history and ignored files."""
        subprocess.run(["git", "init", "-q", str(self.root / "project")], check=True)
        (self.root / "project/.gitignore").write_text("ignored.txt\n")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload)
        self.assertTrue((payload / "run/project/.git/HEAD").is_file())
        self.assertTrue((payload / "run/project/ignored.txt").is_file())
        self.assertGreater(archive.verify_tree(payload), 4)

    def test_missing_or_empty_required_path_blocks_snapshot(self):
        """Verify missing or empty required path blocks snapshot."""
        for path in ("missing", "project/empty"):
            with self.subTest(path=path):
                (self.root / "project/empty").touch()
                self.plan["required_paths"] = [path]
                (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
                with self.assertRaises(archive.ArchiveError):
                    archive.snapshot(self.root, Path(self.temp.name) / "payload")

    def test_outside_symlink_blocks_archive(self):
        """Verify outside symlink blocks archive."""
        (self.root / "project/outside").symlink_to("/etc/passwd")
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "payload")

    def test_operational_credentials_are_excluded_and_cannot_be_required(self):
        """Verify operational credentials are excluded and cannot be required."""
        (self.root / ".mcp.json").write_text("credential file")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload)
        self.assertFalse((payload / "run/.mcp.json").exists())
        self.plan["required_paths"] = [".mcp.json"]
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "second")

    def test_changed_content_fails_restore_verification(self):
        """Verify changed content fails restore verification."""
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload)
        (payload / "run/project/ignored.txt").write_text("corrupt")
        with self.assertRaises(archive.ArchiveError):
            archive.verify_tree(payload)

    def test_extraction_rejects_traversal_and_links(self):
        """Verify extraction rejects traversal and links."""
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

    def test_required_raw_session_path_is_rejected(self):
        """Verify required raw session path is rejected."""
        (self.root / "state").mkdir()
        (self.root / "state/sessions.sqlite").write_text("private conversation")
        self.plan["required_paths"] = ["state/sessions.sqlite"]
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "payload")

    def test_active_service_is_not_interrupted(self):
        """Verify active service is not interrupted."""
        active = subprocess.CompletedProcess([], 0, "active\n", "")
        with patch.object(archive.subprocess, "run", return_value=active) as run, \
                self.assertRaises(archive.ArchiveError):
            archive.remote_capture(self.root, Path(self.temp.name) / "output.tar.gz")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("stop", run.call_args.args[0])

    def test_teardown_never_deletes_when_archival_fails(self):
        """Verify teardown never deletes when archival fails."""
        scripts = Path(self.temp.name) / "scripts"
        scripts.mkdir()
        source = MODULE_PATH.with_name("teardown-workspace-aws-resources.sh")
        (scripts / source.name).write_text(source.read_text())
        (scripts / "archive-workspace.sh").write_text("#!/bin/bash\nexit 1\n")
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
        """Create isolated fixture state for each regression case."""
        self.config = {"AWS_PROFILE": "main", "AWS_REGION": "us-east-1", "RUN_SLUG": "test"}

    def test_explicit_profile_and_main_account_boundary(self):
        """Verify explicit profile and main account boundary."""
        with patch.object(controller, "command", return_value=subprocess.CompletedProcess(
                [], 0, b'{"Account":"111111111111"}', b"")) as run:
            store = controller.Store(self.config)
        self.assertEqual(store.bucket, "crux-run-archives-111111111111-us-east-1")
        self.assertEqual(run.call_args.args[0][1:3], ["--profile", "main"])
        with patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}), \
                self.assertRaises(controller.ArchiveError):
            controller.Store(dict(self.config, AUX_RESOURCE_ACCOUNT_ID="111111111111"))

    def test_download_requires_exact_version_and_checksum(self):
        """Verify download requires exact version and checksum."""
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
        """Verify s3 policy has encryption versioning privacy and tls."""
        with patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}):
            store = controller.Store(self.config)
        responses = [{"Buckets": []}, {}, {}, {}, {}, {}, {}, {"Status": "Enabled"}]
        with patch.object(store, "aws", side_effect=responses) as run:
            store.ensure_bucket()
        calls = repr(run.call_args_list)
        for item in ("BucketOwnerEnforced", "BlockPublicAcls=true", "Status=Enabled", "AES256", "aws:SecureTransport"):
            self.assertIn(item, calls)

    def test_granted_archive_access_is_read_only_and_specific(self):
        """Verify granted archive access is read only and specific."""
        config = dict(self.config, ARCHIVE_READ_URIS="s3://crux-run-archives-111111111111-us-east-1/runs/old/id/")
        def aws(*args):
            """Call AWS with the explicit profile and region and decode its JSON result.

            :param args: Operation arguments.
            """
            if args[0] == "sts": return {"Account": "111111111111"}
            if args[1] == "list-roles": return {"Roles": [{"RoleName":"crux-run-test"}]}
            if args[1] == "list-instance-profiles-for-role": return {"InstanceProfiles": [{"InstanceProfileName":"crux-run-test"}]}
            return {}
        with patch.object(controller.Store, "aws", side_effect=aws) as run:
            controller.grant_read(config)
        policy = json.loads(run.call_args.args[-1])
        self.assertEqual(policy["Statement"][0]["Resource"], ["arn:aws:s3:::crux-run-archives-111111111111-us-east-1/runs/old/id/*"])
        self.assertNotIn("s3:Delete", json.dumps(policy))
        with patch.object(controller.Store,"aws") as run:
            controller.grant_read(self.config)
            run.assert_not_called()

    def test_trace_directories_are_included(self):
        """Verify trace directories are included."""
        self.assertFalse(any(archive.excluded(p) for p in (".codex/log", ".claude/log", "state/codex/log")))

    def test_wildcard_archive_selection_is_rejected(self):
        """Verify wildcard archive selection is rejected."""
        with self.assertRaises(controller.ArchiveError):
            controller.archive_prefix("s3://bucket/runs/*/")


@unittest.skipUnless(importlib.util.find_spec("trufflehog3"), "native scanner uses its isolated provisioning venv")
class NativeScannerTests(ArchiveTests):
    """Exercise the real native scanner on current files and deleted Git blobs."""

    def test_clean_trace_and_source_snapshot_scans(self):
        """Preserve useful trace text while scanning it like source."""
        (self.root / '.codex').mkdir()
        (self.root / '.codex/session.jsonl').write_text('{"event":"read source file"}\n')
        archive.scan(self.root)

    def test_current_and_deleted_secret_block_without_values(self):
        """Detect a key in the working tree and after deletion from Git HEAD."""
        secret = '-----BEGIN ' + 'RSA PRIVATE KEY-----\nfixture\n'
        path = self.root / 'project/key.txt'
        path.write_text(secret)
        with self.assertRaises(archive.ArchiveError) as error:
            archive.scan(self.root)
        self.assertNotIn('BEGIN', str(error.exception))
        for args in (['init','-q'],['add','.'],['-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','fixture']):
            subprocess.run(['git','-C',str(path.parent),*args],check=True)
        path.unlink()
        subprocess.run(['git','-C',str(path.parent),'add','-u'],check=True)
        subprocess.run(['git','-C',str(path.parent),'-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','remove'],check=True)
        with self.assertRaises(archive.ArchiveError):
            archive.scan(self.root)
