"""Check auxiliary provisioning through its public account and file interfaces."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "utilities/aux-aws"))
import provision
from run_environment import role_settings, write_settings


@pytest.fixture
def accounts():
    """Supply independent main and auxiliary account clients without network access."""
    main, aux = Mock(), Mock()
    main.call.return_value = {"Account": "111111111111"}
    aux.call.return_value = {"Account": "222222222222"}
    cfg = {
        "AWS_PROFILE": "main",
        "AUX_RESOURCE_PROFILE": "aux",
        "AWS_REGION": "us-east-1",
        "RUN_SLUG": "test",
        "PROVISION_S3": "1",
        "PROVISION_EC2": "1",
    }
    with patch.object(provision, "Aws", side_effect=(main, aux)):
        yield provision.AuxiliaryAccount(cfg)


def test_boundary_rerun_preserves_default_version(accounts):
    """Verify that an already matching policy creates no new IAM version."""
    accounts.aux.optional.return_value = {"Policy": {"DefaultVersionId": "v1"}}
    accounts.aux.iam.return_value = {"PolicyVersion": {"Document": json.loads(accounts.policy("crux-app-boundary"))}}
    accounts.reconcile_boundary()
    assert [call.args[0] for call in accounts.aux.iam.call_args_list] == ["get-policy-version"]


@pytest.mark.parametrize("profile", [None, "crux-system-profile", "crux-run-other"])
def test_shared_or_unrelated_profile_rejected_before_grant(accounts, profile):
    """Reject a workspace that cannot own the selected auxiliary role."""
    accounts.main.call.return_value = {"Reservations": [{"Instances": [{"IamInstanceProfile": {"Arn": profile}}]}]}
    with pytest.raises(ValueError, match="ISOLATED_RUN_IDENTITY"):
        accounts.workspace_ip()
    accounts.aux.iam.assert_not_called()


def test_rerecording_environment_preserves_credentials_and_file(tmp_path):
    """Replay an interrupted handoff without duplicating environment entries or rewriting secrets."""
    path = tmp_path / "env"
    path.write_text("OPERATOR_SETTING=example\n")
    assert write_settings(path, role_settings("222222222222"))
    before = path.stat()
    assert not write_settings(path, role_settings("222222222222"))
    assert path.stat().st_ino == before.st_ino
    assert path.read_text().startswith("OPERATOR_SETTING=example\n")
    assert path.stat().st_mode & 0o777 == 0o600
