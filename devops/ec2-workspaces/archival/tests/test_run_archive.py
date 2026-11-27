"""Exercise the manual archive CLI, snapshot contract and versioned storage."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import uuid
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path
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

    def test_portable_links_preserve_shard_views_and_missing_draft_sources(self) -> None:
        """Retain internal link targets across relocation and keep required files mandatory."""
        project = self.root / "project"
        (project / "view").symlink_to("/srv/crux-run/project/ignored.txt")
        (project / "draft").symlink_to("retired.bib")
        controller.make_links_relative(self.root, Path("/srv/crux-run"))
        self.assertEqual((project / "view").readlink(), Path("ignored.txt"))
        with self.assertRaisesRegex(archive.ArchiveError, "Broken symlink"):
            archive.inventory(self.root)
        self.plan["portable_symlinks"] = True
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload, self.plan)
        self.assertGreater(archive.verify_tree(payload), 0)
        self.assertEqual((payload / "run/project/draft").readlink(), Path("retired.bib"))
        self.plan["required_paths"] = ["project/draft"]
        with self.assertRaisesRegex(archive.ArchiveError, "missing"):
            archive.validate_plan(self.root, self.plan)

    def test_portable_links_still_reject_external_targets(self) -> None:
        """Preserve the Run boundary for absolute, traversal and chained links."""
        project = self.root / "project"
        for target in ("/etc/passwd", "/srv/crux-run/../../etc/passwd", "../../outside"):
            with self.subTest(target=target):
                (project / "outside").symlink_to(target)
                (project / "view").symlink_to("/srv/crux-run/project/outside")
                controller.make_links_relative(self.root, Path("/srv/crux-run"))
                with self.assertRaisesRegex(archive.ArchiveError, "escapes"):
                    archive.inventory(self.root, allow_dangling=True)
                (project / "view").unlink()
                (project / "outside").unlink()

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

    def test_staged_exclusions_spare_sidecars_and_reject_reintroduced_payloads(self) -> None:
        """Enforce the transfer selection again before scanning or accepting restored contents."""
        self.plan["excluded_paths"] = ["project/*/*.npz"]
        data = self.root / "project/data"
        data.mkdir()
        (data / "split.json").write_text("{}")
        (self.root / "project/results.npz").write_bytes(b"scientific result")
        payload = Path(self.temp.name) / "payload"
        archive.snapshot(self.root, payload, self.plan)
        self.assertGreater(archive.verify_tree(payload), 0)
        (data / "split.npz").write_bytes(b"raw input")
        with self.assertRaisesRegex(archive.ArchiveError, "Excluded path is present"):
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


@unittest.skipUnless(shutil.which("trufflehog"), "install the official TruffleHog CLI")
class NativeScannerTests(WorkspaceFixture):
    """Exercise the real native scanner on current files and deleted Git blobs."""

    def test_clean_trace_and_source_snapshot_scans(self) -> None:
        """Preserve useful trace text while scanning it like source."""
        (self.root / ".codex").mkdir()
        (self.root / ".codex/session.jsonl").write_text('{"event":"read source file"}\n')
        archive.scan(self.root, archive.scanner_config([]))

    def test_current_and_deleted_secret_block_without_values(self) -> None:
        """Detect a key in the working tree and after deletion from Git HEAD."""
        secret = "fixture.quoted?[*]" + secrets.token_hex(12)
        path = self.root / "project/key.txt"
        path.write_text(secret)
        with self.assertRaises(archive.SecretsFound) as error:
            archive.scan(self.root, archive.scanner_config([secret]))
        self.assertNotIn(secret, str(error.exception))
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
            archive.scan(self.root, archive.scanner_config([secret]))

    def test_provider_tokens_block_upload(self) -> None:
        """Block synthetic provider tokens without exposing their value in the exception."""
        for token in (
            "rp_" + secrets.token_hex(21),
            "rpa_" + secrets.token_hex(23),
            "hf_" + secrets.token_hex(17),
            "api_org_" + secrets.token_hex(17),
            "tgp_v1_" + secrets.token_urlsafe(33),
            "TOGETHER_API_KEY=" + secrets.token_hex(32),
            "sk-or-v1-" + secrets.token_hex(32),
            "sk-proj-" + secrets.token_hex(24) + "T3BlbkFJ" + secrets.token_hex(23)[:45],
            "sk-ant-api03-" + secrets.token_hex(47)[:93] + "AA",
            "xoxb-" + "13725946810-21987654321-" + secrets.token_hex(12),
            "sk-lf-" + str(uuid.uuid4()),
        ):
            with self.subTest(prefix=token[:5]):
                (self.root / "trace").write_text(token)
                with self.assertRaisesRegex(archive.SecretsFound, "trufflehog-redactor") as error:
                    archive.scan(self.root, archive.scanner_config([]))
                self.assertNotIn(token, str(error.exception))


class ArchiveStageTests(unittest.TestCase):
    """Exercise operator-visible progress, retries and the pre-upload scan boundary."""

    def setUp(self) -> None:
        """Create an outside-Git private stage and operator plan in temporary storage."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.stage = controller.ArchiveStage(
            self.base / "stage", {"RUN_SLUG": "fixture", "AWS_PROFILE": "fixture", "AWS_REGION": "us-east-1"}, self.base
        )
        self.run_token = r"Run?[*]\E." + secrets.token_hex(12)
        self.system_token = "Sys+?" + secrets.token_hex(12)
        self.run_secrets = self.base / "run-secrets-fixture.json"
        self.run_secrets.write_text(json.dumps({"TABPFN_TOKEN": self.run_token}))
        self.system_json = json.dumps(
            {"COST_TRACKER_TOKEN": self.system_token, "LANGFUSE_BASE_URL": "https://public.test"}
        )
        self.original_command = controller.command
        self.enterContext(patch.object(controller, "command", side_effect=self.fixture_command))
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

    def fixture_command(self, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        """Serve only the expected decrypted parameter and delegate local commands.

        :param args: Command issued by the operator CLI.
        :returns: Synthetic AWS response or local command result.
        :raises AssertionError: The AWS request has the wrong account, region or parameter.
        :raises archive.ArchiveError: A local command fails.
        """
        if args[0] == "aws":
            self.assertEqual(
                list(args[1:]),
                [
                    "--profile",
                    "fixture",
                    "--region",
                    "us-east-1",
                    "--no-cli-pager",
                    "--output",
                    "json",
                    "ssm",
                    "get-parameter",
                    "--name",
                    "/crux/system/env",
                    "--with-decryption",
                ],
            )
            return subprocess.CompletedProcess(args, 0, json.dumps({"Parameter": {"Value": self.system_json}}).encode())
        if args == ["trufflehog", "--version"]:
            return subprocess.CompletedProcess(args, 0, b"trufflehog 3.99.7")
        return self.original_command(args)

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

    def test_dry_run_reports_selection_without_touching_stage(self) -> None:
        """Use the same filters for preview and copy, even when an existing stage is supplied."""
        plan = json.loads(self.input_plan.read_text())
        plan["excluded_paths"] = ["downloads/*/raw"]
        self.input_plan.write_text(json.dumps(plan))
        config = self.base / "config"
        config.write_text("RUN_SLUG=fixture")
        output = io.StringIO()
        listing = b"ENTRY|>f+++++++++|7|root file\nENTRY|>f+++++++++|11|data/a|b\nENTRY|cL+++++++++|4|data/link\n"
        stage = self.base / "existing"
        stage.mkdir()
        marker = stage / "marker"
        marker.write_text("original")
        with patch.object(controller, "command", return_value=subprocess.CompletedProcess([], 0, listing)) as transfer:
            with redirect_stdout(output):
                self.assertEqual(
                    controller.main(["copy", str(config), str(stage), "--plan", str(self.input_plan), "--dry-run"]), 0
                )
            preview = transfer.call_args.args[0]
            self.stage.copy(self.input_plan)
            actual = transfer.call_args.args[0]
        self.assertEqual(
            [x for x in preview if x.startswith("--exclude=")], [x for x in actual if x.startswith("--exclude=")]
        )
        self.assertIn("--exclude=/downloads/*/raw", preview)
        self.assertIn('"data/a|b"', output.getvalue())
        self.assertIn('1\t11\t"data"', output.getvalue())
        self.assertIn("2\t18\tTOTAL", output.getvalue())
        self.assertEqual(list(stage.iterdir()), [marker])
        self.assertEqual(marker.read_text(), "original")
        with self.assertRaises(SystemExit):
            controller.parse_arguments(["copy", str(config), "--plan", str(self.input_plan)])

    def test_plan_filters_copy_and_rejects_changed_selection_on_resume(self) -> None:
        """Copy with anchored exclusions and bind the stage to the same selection on retry."""
        original = self.input_plan.read_text()
        plan = json.loads(original)
        plan["excluded_paths"] = ["project/data"]
        self.input_plan.write_text(json.dumps(plan))
        with patch.object(controller, "command") as transfer:
            self.stage.copy(self.input_plan)
        self.assertIn("--exclude=/project/data", transfer.call_args.args[0])
        self.assertEqual(self.stage.read_plan()["excluded_paths"], ["project/data"])
        self.input_plan.write_text(original)
        with self.assertRaisesRegex(archive.ArchiveError, "different plan"):
            self.stage.copy(self.input_plan)

    def test_invalid_exclusions_fail_before_transfer(self) -> None:
        """Reject unsafe, unsupported or conflicting patterns before invoking SSH."""
        plan = json.loads(self.input_plan.read_text())
        for invalid in ("data", [3], ["../data"], ["/data"], ["."], ["project/**"], ["project/.git/objects"], ["tr*"]):
            with self.subTest(invalid=invalid), patch.object(controller, "command") as transfer:
                self.input_plan.write_text(json.dumps({**plan, "excluded_paths": invalid}))
                with self.assertRaises(archive.ArchiveError):
                    self.stage.copy(self.input_plan)
                transfer.assert_not_called()

    @unittest.skipUnless(shutil.which("trufflehog"), "install the official TruffleHog CLI")
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

    @unittest.skipUnless(
        shutil.which("trufflehog") and importlib.util.find_spec("trufflehog_redactor"),
        "install archival/requirements.txt",
    )
    def test_redactor_applies_shared_rules_in_staged_files(self) -> None:
        """Use the real redactor on synthetic working files and keep upload gated on a rescan."""
        trace = self.copy_fixture()
        trace.write_text(trace.read_text() + "\n" + "hf_" + "a" * 34)
        with self.assertRaises(archive.SecretsFound):
            self.stage.upload(self.base)
        real_redactor = shutil.which("trufflehog-redactor")
        assert real_redactor is not None
        wrapper = self.base / "trufflehog-redactor"
        wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(real_redactor)} "$@" --no-confirm\n')
        wrapper.chmod(0o700)
        trace.write_text(trace.read_text() + "\n" + self.run_token)
        with patch.dict(os.environ, PATH=str(self.base) + os.pathsep + os.environ["PATH"]):
            self.stage.redact()
        self.assertEqual(trace.read_text().splitlines(), ["*" * 34, "*" * 37, "*" * len(self.run_token)])
        self.assertEqual(self.stage.read_plan()["status"], "redaction_required")

    @unittest.skipUnless(shutil.which("trufflehog"), "install the official TruffleHog CLI")
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

    @unittest.skipUnless(shutil.which("trufflehog"), "install the official TruffleHog CLI")
    def test_exact_values_in_binary_and_provenance(self) -> None:
        """Catch unlabeled secrets in binary files and metadata, with literal punctuation."""
        trace = self.copy_fixture()
        trace.write_bytes(b"\x00\xff" + self.run_token.encode() + b"\x00")
        with self.assertRaises(archive.SecretsFound) as found:
            self.stage.upload(self.base)
        self.assertNotIn(self.run_token, str(found.exception))
        trace.write_text(self.run_token.replace("?", "X") + " https://public.test")
        provenance = self.stage.directory / "provenance/crux-source.json"
        provenance.write_text(json.dumps({"value": self.system_token}))
        with self.assertRaises(archive.SecretsFound):
            self.stage.upload(self.base)
        provenance.unlink()
        with patch.object(controller, "Store"), patch.object(controller, "archive_workspace", return_value="receipt"):
            self.assertEqual(self.stage.upload(self.base), "receipt")

    def test_missing_or_malformed_secret_sources_block_publication(self) -> None:
        """Fail closed on each secret source without publishing or leaking its contents."""
        self.copy_fixture()
        self.system_json = "invalid " + self.system_token
        for missing in (False, True):
            with self.subTest(missing_run=missing), patch.object(controller, "Store") as store:
                if missing:
                    self.run_secrets.unlink()
                with self.assertRaises(archive.ArchiveError) as error:
                    self.stage.upload(self.base)
                self.assertNotIn(self.system_token, str(error.exception))
                self.assertEqual(self.stage.read_plan()["status"], "scan_failed")
                store.assert_not_called()

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
