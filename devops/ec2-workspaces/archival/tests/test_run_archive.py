"""Exercise the manual archive CLI, snapshot contract and versioned storage."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive_workspace as controller
import local_archive_utils as archive


class WorkspaceFixture(unittest.TestCase):
    """Supply a temporary Run tree and restore plan to archive and scanner tests."""

    def setUp(self) -> None:
        """Create isolated fixture state for each regression case."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"
        self.root.mkdir()
        (self.root / "project").mkdir()
        (self.root / "project/ignored.txt").write_text("required ignored asset\n")
        self.plan: archive.ArchivePlan = {
            "plan_version": 1,
            "required_paths": ["project/ignored.txt"],
            "restore_checks": ["cat project/ignored.txt"],
        }
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))


class ArchiveTests(WorkspaceFixture):
    """Exercise the local archive format in a private directory."""

    def test_complete_copy_includes_git_history_and_ignored_files(self) -> None:
        """Verify complete copy includes git history and ignored files."""
        subprocess.run(["git", "init", "-q", str(self.root / "project")], check=True)
        (self.root / "project/.gitignore").write_text("ignored.txt\n")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload, self.plan)
        self.assertTrue((payload / "run/project/.git/HEAD").is_file())
        self.assertTrue((payload / "run/project/ignored.txt").is_file())
        self.assertGreater(archive.verify_tree(payload), 4)

    def test_plan_requires_integer_plan_version_one(self) -> None:
        """Reject missing, obsolete and unsupported plan format markers."""
        plan = dict(self.plan)
        del plan["plan_version"]
        for fields in (
            {},
            {"version": 1},
            {"plan_version": True},
            {"plan_version": 2},
            {"plan_version": 1, "version": 1},
        ):
            with self.subTest(fields=fields), self.assertRaisesRegex(archive.ArchiveError, "plan_version"):
                archive.validate_plan(self.root, {**plan, **fields})

    def test_missing_or_empty_required_path_blocks_snapshot(self) -> None:
        """Verify missing or empty required path blocks snapshot."""
        for path in ("missing", "project/empty"):
            with self.subTest(path=path):
                (self.root / "project/empty").touch()
                self.plan["required_paths"] = [path]
                (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
                with self.assertRaises(archive.ArchiveError):
                    archive.snapshot(self.root, Path(self.temp.name) / "payload", self.plan)

    def test_outside_symlink_blocks_archive(self) -> None:
        """Verify outside symlink blocks archive."""
        (self.root / "project/outside").symlink_to("/etc/passwd")
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "payload", self.plan)

    def test_operational_credentials_are_excluded_and_cannot_be_required(self) -> None:
        """Verify operational credentials are excluded and cannot be required."""
        (self.root / ".mcp.json").write_text("credential file")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload, self.plan)
        self.assertFalse((payload / "run/.mcp.json").exists())
        self.plan["required_paths"] = [".mcp.json"]
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "second", self.plan)

    def test_changed_content_fails_restore_verification(self) -> None:
        """Verify changed content fails restore verification."""
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload, self.plan)
        (payload / "run/project/ignored.txt").write_text("corrupt")
        with self.assertRaises(archive.ArchiveError):
            archive.verify_tree(payload)

    def test_extraction_rejects_traversal_and_links(self) -> None:
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

    def test_required_raw_session_path_is_rejected(self) -> None:
        """Verify required raw session path is rejected."""
        (self.root / "state").mkdir()
        (self.root / "state/sessions.sqlite").write_text("private conversation")
        self.plan["required_paths"] = ["state/sessions.sqlite"]
        (self.root / "ARCHIVE.json").write_text(json.dumps(self.plan))
        with self.assertRaises(archive.ArchiveError):
            archive.snapshot(self.root, Path(self.temp.name) / "payload", self.plan)


class StoreTests(unittest.TestCase):
    """Exercise the operator storage interface without cloud credentials."""

    def setUp(self) -> None:
        """Create isolated fixture state for each regression case."""
        self.config = {"AWS_PROFILE": "main", "AWS_REGION": "us-east-1", "RUN_SLUG": "test"}

    def test_explicit_profile_and_main_account_boundary(self) -> None:
        """Verify explicit profile and main account boundary."""
        with patch.object(
            controller, "command", return_value=subprocess.CompletedProcess([], 0, b'{"Account":"111111111111"}', b"")
        ) as run:
            store = controller.Store(self.config)
        self.assertEqual(store.bucket, "crux-run-archives-111111111111-us-east-1")
        self.assertEqual(run.call_args_list[-1].args[0][1:3], ["--profile", "main"])
        with (
            patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}),
            self.assertRaises(archive.ArchiveError),
        ):
            controller.Store(dict(self.config, AUX_RESOURCE_ACCOUNT_ID="111111111111"))

    def test_download_requires_exact_version_and_checksum(self) -> None:
        """Verify download requires exact version and checksum."""
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}),
        ):
            store = controller.Store(self.config)
            path = Path(temp) / "bundle"
            path.write_bytes(b"corrupt")
            receipt: controller.BundleVersion = {
                "bucket": store.bucket,
                "key": "runs/test/id/bundle.tar.gz",
                "version_id": "v1",
                "sha256": "wrong",
            }
            with self.assertRaises(archive.ArchiveError):
                store.download(receipt, path)
            receipt.pop("version_id")
            with self.assertRaises(archive.ArchiveError):
                store.download(receipt, path)

    def test_s3_policy_has_encryption_versioning_privacy_and_tls(self) -> None:
        """Verify s3 policy has encryption versioning privacy and tls."""
        with patch.object(controller.Store, "aws", return_value={"Account": "111111111111"}):
            store = controller.Store(self.config)
        responses = [{"Buckets": []}, {}, {}, {}, {}, {}, {}, {"Status": "Enabled"}]
        with patch.object(store, "aws", side_effect=responses) as run:
            store.ensure_bucket()
        calls = repr(run.call_args_list)
        for item in ("BucketOwnerEnforced", "BlockPublicAcls=true", "Status=Enabled", "AES256", "aws:SecureTransport"):
            self.assertIn(item, calls)

    def test_publish_retry_preserves_existing_version(self) -> None:
        """Replay the publication after an interrupted verification."""
        from unittest.mock import Mock

        store = Mock()
        store.aws.return_value = {"Contents": [{"Key": "runs/test/key"}]}
        controller.publish_once(store, "runs/test/key", Path("unused"))
        self.assertEqual([call.args[1] for call in store.aws.call_args_list], ["list-objects-v2"])

    def test_trace_directories_are_included(self) -> None:
        """Verify trace directories are included."""
        self.assertFalse(any(archive.is_excluded(p) for p in (".codex/log", ".claude/log", "state/codex/log")))


@unittest.skipUnless(importlib.util.find_spec("trufflehog3"), "install archival/requirements.txt for scanner tests")
class NativeScannerTests(WorkspaceFixture):
    """Exercise the real native scanner on current files and deleted Git blobs."""

    def test_clean_trace_and_source_snapshot_scans(self) -> None:
        """Preserve useful trace text while scanning it like source."""
        (self.root / ".codex").mkdir()
        (self.root / ".codex/session.jsonl").write_text('{"event":"read source file"}\n')
        archive.scan(self.root)

    def test_current_and_deleted_secret_block_without_values(self) -> None:
        """Detect a key in the working tree and after deletion from Git HEAD."""
        secret = "-----BEGIN " + "RSA PRIVATE KEY-----\nfixture\n"
        path = self.root / "project/key.txt"
        path.write_text(secret)
        with self.assertRaises(archive.ArchiveError) as error:
            archive.scan(self.root)
        self.assertNotIn("BEGIN", str(error.exception))
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"],
        ):
            subprocess.run(["git", "-C", str(path.parent), *args], check=True)
        path.unlink()
        subprocess.run(["git", "-C", str(path.parent), "add", "-u"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(path.parent),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "remove",
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(path.parent), "gc"], check=True)
        with self.assertRaisesRegex(archive.SecretsFound, "trufflehog-redactor"):
            archive.scan(self.root)

    def test_provider_tokens_block_upload(self) -> None:
        """Block synthetic provider tokens without exposing their value in the exception."""
        for prefix, length in (
            ("rp_", 42),
            ("rpa_", 46),
            ("hf_", 34),
            ("api_org_", 34),
            ("tgp_v1_", 43),
            ("TOGETHER_API_KEY=", 64),
        ):
            with self.subTest(prefix=prefix):
                token = prefix + "a" * length
                (self.root / "trace").write_text(token)
                with self.assertRaisesRegex(archive.SecretsFound, "trufflehog-redactor") as error:
                    archive.scan(self.root)
                self.assertNotIn(token, str(error.exception))


class ArchiveStageTests(unittest.TestCase):
    """Exercise operator-visible progress, retries and the pre-upload scan boundary."""

    def setUp(self) -> None:
        """Create an outside-Git private stage and operator plan in temporary storage."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.stage = controller.ArchiveStage(self.base / "stage", {"RUN_SLUG": "fixture"})
        self.input_plan = self.base / "plan.json"
        self.input_plan.write_text(
            json.dumps(
                {
                    "plan_version": 1,
                    "required_paths": ["trace"],
                    "restore_checks": ["read trace"],
                }
            )
        )

    def copy_fixture(self) -> Path:
        """Stub the transfer and populate the stage with a synthetic trace.

        :returns: Path to the synthetic trace in the staged Run.
        """
        with patch.object(controller, "command"):
            self.stage.copy(self.input_plan)
        trace = self.stage.directory / "run/trace"
        trace.write_text("rpa_" + "a" * 30)
        return trace

    def test_failed_publication_can_retry_the_same_stage(self) -> None:
        """Preserve an operator-reviewed stage and retry a failed S3 publication."""
        self.copy_fixture().write_text("clean")
        with (
            patch.object(controller, "create_bundle"),
            patch.object(controller, "version", return_value="fixture"),
            patch.object(controller, "Store"),
            patch.object(
                controller, "archive_workspace", side_effect=[archive.ArchiveError("storage failure"), "s3://receipt"]
            ),
        ):
            with self.assertRaisesRegex(archive.ArchiveError, "storage failure"):
                self.stage.upload(self.base)
            self.assertEqual(self.stage.read_plan()["status"], "upload_failed")
            self.assertEqual(self.stage.upload(self.base), "s3://receipt")
            self.assertEqual(self.stage.read_plan()["status"], "verified")

    def test_failed_copy_cannot_upload_and_can_resume(self) -> None:
        """Record incomplete transfers and allow the same copy command to finish them."""
        with (
            patch.object(controller, "command", side_effect=archive.ArchiveError),
            self.assertRaisesRegex(archive.ArchiveError, "retry copy"),
        ):
            self.stage.copy(self.input_plan)
        self.assertEqual(self.stage.read_plan()["status"], "copy_failed")
        with patch.object(controller, "Store") as store, self.assertRaisesRegex(archive.ArchiveError, "completed"):
            self.stage.upload(self.base)
        store.assert_not_called()
        self.copy_fixture()
        self.assertEqual(self.stage.read_plan()["status"], "copied")

    @unittest.skipUnless(importlib.util.find_spec("trufflehog3"), "install archival/requirements.txt")
    def test_shared_finding_blocks_storage_and_redacted_retry_rescans(self) -> None:
        """Keep reported secrets private and preserve staged redactions across retries."""
        trace = self.copy_fixture()
        with patch.object(controller, "Store") as store, self.assertRaises(archive.SecretsFound):
            self.stage.upload(self.base)
        store.assert_not_called()
        self.assertEqual(self.stage.read_plan()["status"], "redaction_required")
        self.assertNotIn(trace.read_text(), self.stage.state_file.read_text())
        trace.write_text("redacted")
        with patch.object(controller, "command") as transfer:
            self.stage.copy(self.input_plan)
            transfer.assert_not_called()
        with (
            patch.object(controller, "Store"),
            patch.object(controller, "archive_workspace", return_value="s3://receipt"),
        ):
            self.assertEqual(self.stage.upload(self.base), "s3://receipt")
        self.assertEqual(self.stage.read_plan()["status"], "verified")
        trace.write_text("hf_" + "b" * 34)
        with patch.object(controller, "Store") as store, self.assertRaises(archive.SecretsFound):
            self.stage.upload(self.base)
        store.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("trufflehog_redactor"), "install archival/requirements.txt")
    def test_redactor_applies_shared_rules_in_staged_files(self) -> None:
        """Use the real redactor on synthetic working files and keep upload gated on a rescan."""
        trace = self.copy_fixture()
        trace.write_text(trace.read_text() + "\n" + "hf_" + "a" * 34)
        with self.assertRaises(archive.SecretsFound):
            self.stage.upload(self.base)
        popen = subprocess.Popen

        def confirm_fixture(argv: list[str], *, stdin: int, text: Literal[True]) -> subprocess.Popen[str]:
            """Auto-confirm the synthetic replacements in the redactor subprocess.

            :param argv: Redactor executable and arguments.
            :param stdin: Pipe requested by the archive code for transmitting findings.
            :param text: Enable text mode for the redactor input.
            :returns: The running redactor with confirmation disabled for the synthetic fixture.
            :raises OSError: The redactor cannot be started.
            """
            return popen([*argv, "--no-confirm"], stdin=stdin, text=text)

        with patch.object(subprocess, "Popen", side_effect=confirm_fixture):
            self.stage.redact()
        self.assertEqual(trace.read_text().splitlines(), ["*" * 34, "*" * 37])
        self.assertEqual(self.stage.read_plan()["status"], "redaction_required")

    @unittest.skipUnless(importlib.util.find_spec("trufflehog3"), "install archival/requirements.txt")
    def test_missing_or_invalid_detector_blocks_storage(self) -> None:
        """Reject missing rules and malformed files before making any remote request."""
        self.copy_fixture().write_text("clean")
        for contents in (None, "not a rule", "[]"):
            with self.subTest(contents=contents):
                rules = self.base / "detectors.yml"
                if contents is None:
                    rules.unlink(missing_ok=True)
                else:
                    rules.write_text(contents)
                with (
                    patch.object(archive, "DETECTORS_FILE", rules),
                    patch.object(controller, "Store") as store,
                    self.assertRaises(archive.ArchiveError),
                ):
                    self.stage.upload(self.base)
                store.assert_not_called()
                self.assertEqual(self.stage.read_plan()["status"], "scan_failed")

    def test_per_archive_rules_require_migration_before_copy_or_upload(self) -> None:
        """Fail before transfer or publication when legacy metadata selects a private rule file."""
        old_option = {"custom_detectors": ["private.yml"]}
        plan = json.loads(self.input_plan.read_text())
        self.input_plan.write_text(json.dumps({**plan, **old_option}))
        with (
            patch.object(controller, "command") as transfer,
            self.assertRaisesRegex(archive.ArchiveError, "detectors.yml"),
        ):
            self.stage.copy(self.input_plan)
        transfer.assert_not_called()
        self.input_plan.write_text(json.dumps(plan))
        self.copy_fixture()
        self.stage.state_file.write_text(json.dumps({**self.stage.read_plan(), **old_option}))
        with patch.object(controller, "Store") as store, self.assertRaisesRegex(archive.ArchiveError, "detectors.yml"):
            self.stage.upload(self.base)
        store.assert_not_called()

    def test_empty_legacy_stage_can_resume_but_new_plans_use_shared_rules(self) -> None:
        """Accept the empty field written by old stages and reject it on a newly supplied plan."""
        self.copy_fixture()
        self.stage.state_file.write_text(json.dumps({**self.stage.read_plan(), "custom_detectors": []}))
        with patch.object(controller, "command") as transfer:
            self.stage.copy(self.input_plan)
            transfer.assert_not_called()
        plan = json.loads(self.input_plan.read_text())
        self.input_plan.write_text(json.dumps({**plan, "custom_detectors": []}))
        with self.assertRaisesRegex(archive.ArchiveError, "detectors.yml"):
            self.stage.copy(self.input_plan)
