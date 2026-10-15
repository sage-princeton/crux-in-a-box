import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/run_service_auth.py"
SPEC = importlib.util.spec_from_file_location("run_service_auth", SCRIPT)
auth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auth)


class ServiceAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.env_file = self.home / "run.env"
        self.env_file.write_text('PATH="/usr/bin"\nGH_TOKEN="fixture-secret"\n')
        self.service = dict(name="gog", environment={"GOG_CONFIG_DIR": "{service_dir}/config"},
                            check=dict(argv=["gog", "auth", "list"], hint="Refresh the service login."))
        self.directory = self.home / ".config/crux-services/gog"

    def bundle(self, entries=None):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            for name, kind in entries or [("./", tarfile.DIRTYPE), ("./config", tarfile.DIRTYPE),
                                           ("./config/token.json", tarfile.REGTYPE)]:
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, "../fixture-private-target"
                payload = b'{"refresh":"fixture-private-token"}' if kind == tarfile.REGTYPE else b""
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        return base64.b64encode(data.getvalue()).decode()

    def configure(self, bundle=None, services=None):
        auth.configure_services(services if services is not None else [self.service],
                                {} if bundle is None else {"SERVICE_AUTH": {"gog": bundle}},
                                self.home, os.getuid(), os.getgid(), self.env_file)

    def test_seed_permissions_ownership_environment_and_preserve_refresh(self):
        self.configure(self.bundle())
        credential = self.directory / "config/token.json"
        self.assertIn("fixture-private-token", credential.read_text())
        for path, mode in [(self.directory, 0o700), (credential.parent, 0o700), (credential, 0o600)]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (os.getuid(), os.getgid()))
        self.assertIn('GOG_CONFIG_DIR=' + json.dumps(str(self.directory / "config")), self.env_file.read_text())
        credential.write_text("rotated-auth")
        credential.chmod(0o644)
        credential.parent.chmod(0o755)
        self.directory.chmod(0o755)
        (self.home / ".config").chmod(0o755)
        unrelated = self.directory / "unrelated"
        unrelated.write_text("keep")
        self.env_file.write_text('PATH="/usr/bin"\n')
        self.configure(self.bundle())
        self.assertEqual(credential.read_text(), "rotated-auth")
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(credential.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.home / ".config").stat().st_mode), 0o755)

    def test_env_only_and_no_services_need_no_bundle(self):
        self.configure(services=[])
        self.assertFalse(self.directory.exists())
        self.configure()
        self.assertTrue(self.directory.is_dir())
        env = {"GH_TOKEN": "fixture-private-token"}
        service = {**self.service, "required_env": ["GH_TOKEN"]}
        self.assertEqual(auth.service_checks([service], env)[0]["argv"], ["gog", "auth", "list"])
        with self.assertRaisesRegex(auth.ServiceError, "gog requires GH_TOKEN.*Refresh"):
            auth.service_checks([service], {})

    def test_utf8_literals_only_expand_service_dir(self):
        self.service["environment"] = {"GOG_LABEL": 'café {literal} "quoted" $HOME {service_dir}'}
        self.configure()
        value = json.loads(self.env_file.read_text().split("GOG_LABEL=", 1)[1])
        self.assertEqual(value, f'café {{literal}} "quoted" $HOME {self.directory}')
        self.assertIn("café", self.env_file.read_text())

    def test_rejects_invalid_services_and_environment_collisions(self):
        invalid = [None, [dict(name="../bad", check={})], [dict(name="gog")],
                   [{**self.service, "extra": True}], [self.service, self.service],
                   [{**self.service, "environment": {"HOME": "/elsewhere"}}],
                   [{**self.service, "required_env": ["bad-name"]}],
                   [{**self.service, "check": {"argv": []}}],
                   [{**self.service, "check": {"argv": ["true"], "timeout_seconds": True}}],
                   [self.service, {**self.service, "name": "other"}]]
        for services in invalid:
            with self.subTest(services=services), self.assertRaises(auth.ServiceError):
                auth.validate_services(services)
        self.service["environment"] = {"GH_TOKEN": "fixture-private-token"}
        with self.assertRaisesRegex(auth.ServiceError, "conflicts with GH_TOKEN"):
            self.configure(self.bundle())
        self.assertFalse(self.directory.exists())

    def test_rejects_all_bad_archives_before_creating_files(self):
        bad = ["not-base64", base64.b64encode(b"not gzip").decode()]
        for entries in [[("../fixture-private-path", tarfile.REGTYPE)], [("/outside", tarfile.REGTYPE)],
                        [("a", tarfile.REGTYPE), ("./a", tarfile.REGTYPE)],
                        [("a", tarfile.REGTYPE), ("a/b", tarfile.REGTYPE)],
                        *[[("fixture-private-path", kind)] for kind in
                          (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE)]]:
            bad.append(self.bundle(entries))
        output = io.StringIO()
        for bundle in bad:
            with self.subTest(bundle=bad.index(bundle)), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(auth.ServiceError) as error:
                    self.configure(bundle)
                self.assertNotIn("fixture-private", str(error.exception))
                self.assertFalse(self.directory.exists())
        self.assertEqual(output.getvalue(), "")
        for limit in ("MAX_COMPRESSED", "MAX_EXPANDED", "MAX_MEMBERS"):
            with patch.object(auth, limit, 1), self.assertRaises(auth.ServiceError):
                self.configure(self.bundle())

    def test_existing_symlink_ancestors_and_files_are_never_followed(self):
        outside = self.home / "outside"
        outside.mkdir()
        config = self.home / ".config"
        config.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, auth.ServiceError)):
            self.configure(self.bundle())
        self.assertEqual(list(outside.iterdir()), [])
        config.unlink()
        (self.directory / "config").mkdir(parents=True)
        target = outside / "target"
        target.write_text("unchanged")
        (self.directory / "config/token.json").symlink_to(target)
        with self.assertRaises(auth.ServiceError):
            self.configure(self.bundle())
        self.assertEqual(target.read_text(), "unchanged")
        self.env_file.unlink()
        self.env_file.symlink_to(target)
        with self.assertRaises(OSError):
            self.configure()
        self.assertEqual(target.read_text(), "unchanged")

    def test_rejects_existing_hardlinks_without_changing_other_file(self):
        (self.directory / "config").mkdir(parents=True)
        target = self.home / "outside-config"
        target.write_text("untouched")
        target.chmod(0o644)
        os.link(target, self.directory / "config/token.json")
        with self.assertRaises(auth.ServiceError):
            self.configure(self.bundle())
        self.assertEqual(target.read_text(), "untouched")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_interrupted_seed_has_no_partial_destination(self):
        with patch.object(auth.os, "fsync", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.configure(self.bundle())
        self.assertFalse((self.directory / "config/token.json").exists())
        self.assertEqual(list(self.directory.rglob(".seed-*")), [])
        self.configure(self.bundle())
        self.assertIn("fixture-private-token", (self.directory / "config/token.json").read_text())

    def test_auth_validation_is_read_only_and_rejects_non_object_secrets(self):
        entries = auth.validate_auth([self.service], {"SERVICE_AUTH": {"gog": self.bundle()}})
        self.assertIn(("config", "token.json"), entries["gog"])
        self.assertFalse(self.directory.exists())
        for secrets in ([], "invalid", {"SERVICE_AUTH": []}):
            with self.assertRaises(auth.ServiceError):
                auth.validate_auth([self.service], secrets)

    def test_rejects_undeclared_auth_bundles(self):
        with self.assertRaises(auth.ServiceError):
            self.configure(self.bundle(), services=[])
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()
