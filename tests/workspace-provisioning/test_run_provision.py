import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/run_provision.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("run_provision", SCRIPT)
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)


class RunProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest_path = self.root / "provision.json"
        self.bin = self.root / "bin"
        self.binary = b"#!/bin/sh\nexit 0\n"

    def tool(self, payload=None, **kwargs):
        return dict(name="fixture", url="https://example.test/v1/fixture", sha256=hashlib.sha256(
            self.binary if payload is None else payload).hexdigest(), **kwargs)

    def manifest(self, **kwargs):
        self.manifest_path.write_text(json.dumps(dict(version=1, **kwargs)))
        return provision.load_manifest(self.manifest_path)

    def install(self, tool, payload=None):
        with patch.object(provision, "download", side_effect=lambda url, path: path.write_bytes(
                self.binary if payload is None else payload)) as download:
            provision.install(self.manifest(tools=[tool]), self.bin)
            return download.call_count

    def test_missing_manifest_and_empty_manifest_are_opt_in(self):
        self.assertEqual(provision.load_manifest(self.manifest_path), {"version": 1})
        with patch.object(provision, "run_quiet") as command:
            provision.install(self.manifest(), self.bin)
        command.assert_not_called()
        self.assertFalse(self.bin.exists())

    def test_rejects_invalid_declarations(self):
        for values in [{"extra": True}, {"version": True}, {"apt_packages": ["--option"]},
                       {"apt_packages": "curl"}, {"tools": [self.tool() | {"name": "../x"}]},
                       {"tools": [self.tool() | {"sha256": "bad"}]},
                       *[{"tools": [self.tool() | {"url": url}]} for url in
                         ("http://example.test/x", "https://user:pass@example.test/x", "https://example.test/x?token=x")],
                       {"tools": [self.tool(member="../x")]}, {"tools": [self.tool(), self.tool()]},
                       {"checks": [{"name": "probe", "argv": "echo"}]},
                       {"checks": [{"name": "probe", "argv": ["echo"], "timeout_seconds": True}]}]:
            with self.subTest(values=values), self.assertRaises(provision.ProvisionError):
                self.manifest_path.write_text(json.dumps({"version": 1} | values))
                provision.load_manifest(self.manifest_path)

    def test_packages_use_literal_argv_and_quiet_execution(self):
        with patch.object(provision, "run_quiet") as command:
            provision.install(self.manifest(apt_packages=["poppler-utils", "curl=1.0-1"]), self.bin)
        self.assertEqual(command.call_args.args[0][-3:], ["--", "poppler-utils", "curl=1.0-1"])
        self.assertEqual(command.call_args.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")

    def test_binary_install_is_idempotent_and_repairs_tampering(self):
        tool = self.tool()
        self.assertEqual(self.install(tool), 1)
        self.assertEqual(self.install(tool), 0)
        (self.bin / "fixture").write_text("tampered")
        self.assertEqual(self.install(tool), 1)
        provision.check(self.manifest(tools=[tool], checks=[{"name": "fixture", "argv": ["fixture"]}]), self.bin)

    def test_hash_failure_preserves_existing_binary(self):
        self.install(self.tool())
        with self.assertRaises(provision.ProvisionError):
            self.install(self.tool() | {"sha256": "0" * 64})
        self.assertEqual((self.bin / "fixture").read_bytes(), self.binary)
        self.assertEqual(list(self.bin.glob(".install-*")), [])

    def archive(self, member_type=tarfile.REGTYPE, duplicate=False):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            entry = tarfile.TarInfo("release/fixture")
            entry.type, entry.size = member_type, len(self.binary)
            entry.linkname = "../../outside"
            archive.addfile(entry, io.BytesIO(self.binary))
            if duplicate:
                archive.addfile(entry, io.BytesIO(self.binary))
        return data.getvalue()

    def test_extracts_only_exact_regular_member(self):
        payload = self.archive()
        self.install(self.tool(payload, member="release/fixture"), payload)
        self.assertEqual((self.bin / "fixture").read_bytes(), self.binary)
        for payload in (self.archive(tarfile.SYMTYPE), self.archive(tarfile.LNKTYPE), self.archive(duplicate=True)):
            with self.assertRaises(provision.ProvisionError):
                self.install(self.tool(payload, member="release/fixture"), payload)

    def test_checks_fail_closed_without_output_or_argument_leaks(self):
        for script, timeout in [("print('secret-output'); raise SystemExit(1)", 30),
                                ("import time; time.sleep(30)", 1)]:
            self.manifest(checks=[{"name": "service", "argv": [sys.executable, "-c", script, "secret-argument"],
                                       "timeout_seconds": timeout, "hint": "Refresh the service login."}])
            result = subprocess.run([sys.executable, str(SCRIPT), "check", "--manifest", str(self.manifest_path)],
                                    capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("Refresh the service login.", result.stderr)
            self.assertNotIn("secret", result.stderr)
        with self.assertRaisesRegex(provision.ProvisionError, "unavailable"):
            provision.check(self.manifest(tools=[self.tool()]), self.bin)

    def test_exec_forwards_argv_and_environment_after_check(self):
        self.manifest()
        with patch.object(provision.os, "execvpe") as execute:
            self.assertEqual(provision.main(["exec", "--manifest", str(self.manifest_path), "--tools-dir", str(self.bin),
                                            "--", "agent", "--manifest", "literal value"]), 0)
        executable, argv, env = execute.call_args.args
        self.assertEqual((executable, argv), ("agent", ["agent", "--manifest", "literal value"]))
        self.assertEqual(env["PATH"].split(":")[0], str(self.bin))
        self.manifest(checks=[{"name": "missing", "argv": ["nonexistent-crux-fixture"]}])
        with patch.object(provision.os, "execvpe") as execute, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(provision.main(["exec", "--manifest", str(self.manifest_path), "--", "agent"]), 2)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
