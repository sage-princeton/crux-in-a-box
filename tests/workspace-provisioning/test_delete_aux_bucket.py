import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/delete_aux_bucket.py"
spec = importlib.util.spec_from_file_location("delete_aux_bucket", SOURCE)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class BucketAPI:
    def __init__(self, count=0, location="us-east-2", errors=False):
        """Initialize explicit account, region and run identity for this operation.

        :param count: Count for this operation.
        :param location: Location for this operation.
        :param errors: Errors for this operation.
        """
        self.location = location
        self.objects = [{"Key": f"media/{index}", "VersionId": f"v{index}"} for index in range(count)]
        self.markers = [{"Key": 'media/unicode-☃-"\n', "VersionId": "marker-1"}]
        self.calls = []
        self.batches = []
        self.errors = errors
        self.deleted = False

    def __call__(self, profile, region, *args):
        """Call.

        :param profile: Profile for this operation.
        :param region: Region for this operation.
        :param args: Operation arguments.
        """
        assert profile == "aux"
        self.calls.append((region, args))
        assert args[0] == "s3api"
        assert args[args.index("--expected-bucket-owner") + 1] == "222222222222"
        operation = args[1]
        if operation == "get-bucket-location":
            return {"LocationConstraint": self.location}
        if operation == "list-object-versions":
            assert "--no-paginate" in args
            return {"Versions": self.objects[:1000], "DeleteMarkers": self.markers,
                    "IsTruncated": len(self.objects) > 1000}
        if operation == "list-objects-v2":
            return {"Contents": []}
        if operation == "delete-objects":
            document = Path(args[args.index("--delete") + 1].removeprefix("file://"))
            assert document.stat().st_mode & 0o077 == 0
            batch = json.loads(document.read_text())["Objects"]
            self.batches.append(batch)
            assert len(batch) <= 1000
            if self.errors:
                return {"Errors": [{"Key": "do-not-print-sensitive-key", "Code": "AccessDenied"}]}
            self.objects = [item for item in self.objects if item not in batch]
            self.markers = [item for item in self.markers if item not in batch]
            return {}
        if operation == "delete-bucket":
            assert not self.objects and not self.markers
            self.deleted = True
            return {}
        raise AssertionError(operation)


def test_all_versions_and_markers_are_deleted_in_bounded_batches(monkeypatch):
    """Verify all versions and markers are deleted in bounded batches.

    :param monkeypatch: Monkeypatch for this operation.
    """
    api = BucketAPI(2501)
    monkeypatch.setattr(cleanup, "aws", api)
    cleanup.delete_bucket("aux", "us-east-1", "media", "222222222222")
    assert api.deleted
    assert sum(map(len, api.batches)) == 2502
    assert {region for region, args in api.calls if args[1] != "get-bucket-location"} == {"us-east-2"}


@pytest.mark.parametrize(("location", "expected"), [(None, "us-east-1"), ("EU", "eu-west-1")])
def test_bucket_region_is_discovered_independently_of_config(monkeypatch, location, expected):
    """Verify bucket region is discovered independently of config.

    :param monkeypatch: Monkeypatch for this operation.
    :param location: Location for this operation.
    :param expected: Expected for this operation.
    """
    api = BucketAPI(location=location)
    monkeypatch.setattr(cleanup, "aws", api)
    cleanup.delete_bucket("aux", "us-west-2", "media", "222222222222")
    assert api.calls[-1][0] == expected


def test_partial_delete_error_blocks_bucket_deletion_and_does_not_echo_keys(monkeypatch):
    """Verify partial delete error blocks bucket deletion and does not echo keys.

    :param monkeypatch: Monkeypatch for this operation.
    """
    api = BucketAPI(errors=True)
    monkeypatch.setattr(cleanup, "aws", api)
    with pytest.raises(cleanup.CleanupError) as error:
        cleanup.delete_bucket("aux", "us-east-1", "media", "222222222222")
    assert not api.deleted
    assert "do-not-print-sensitive-key" not in str(error.value)


def test_transport_errors_are_not_treated_as_empty_buckets(monkeypatch):
    """Verify transport errors are not treated as empty buckets.

    :param monkeypatch: Monkeypatch for this operation.
    """
    def failed(*args):
        """Failed.

        :param args: Operation arguments.
        """
        raise cleanup.CleanupError("AWS failed")
    monkeypatch.setattr(cleanup, "aws", failed)
    with pytest.raises(cleanup.CleanupError):
        cleanup.delete_bucket("aux", "us-east-1", "media", "222222222222")


def test_aws_cli_always_uses_explicit_profile_and_parses_json(monkeypatch):
    """Verify aws cli always uses explicit profile and parses json.

    :param monkeypatch: Monkeypatch for this operation.
    """
    calls = []
    def completed(args, **kwargs):
        """Completed.

        :param args: Operation arguments.
        :param kwargs: Additional subprocess options.
        """
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "{}", "")
    monkeypatch.setattr(cleanup.subprocess, "run", completed)
    cleanup.aws("aux", "us-east-2", "s3api", "list-buckets")
    assert calls[0][1:5] == ["--profile", "aux", "--region", "us-east-2"]
