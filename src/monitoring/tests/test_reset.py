import boto3
import pytest
from moto import mock_aws

from ci.reset import reset


def test_reset_deletes_only_retired_monitoring_data_and_leaves_sql_snapshots(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        name = "crux-monitoring-ae211"
        config = {"name": name, "account_id": "123456789012", "region": "us-east-1"}
        s3 = boto3.client("s3")
        bucket = f"{name}-123456789012-us-east-1"
        s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        for key in (
            "reviews/old.json",
            "inventory/old.json",
            "config/deployment.json",
            "workspaces/new.tar.gz",
            "collections/new.json",
        ):
            for version in (b"first", b"second"):
                s3.put_object(Bucket=bucket, Key=key, Body=version)
        db = boto3.client("dynamodb")
        for table in (name + "-incidents", "research-data"):
            db.create_table(
                TableName=table,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                Tags=[{"Key": "Project", "Value": "crux-monitoring"}],
                DeletionProtectionEnabled=True,
            )
        reset(config)
        reset(config)
        versions = s3.list_object_versions(Bucket=bucket)["Versions"]
        assert {v["Key"] for v in versions} == {"workspaces/new.tar.gz", "collections/new.json"}
        assert (
            db.describe_table(TableName=name + "-incidents")["Table"]["DeletionProtectionEnabled"]
            is False
        )
        assert (
            db.describe_table(TableName="research-data")["Table"]["DeletionProtectionEnabled"]
            is True
        )


def test_reset_refuses_another_account(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        with pytest.raises(ValueError, match="account"):
            reset({"name": "crux-monitoring-ae211", "account_id": "000000000000"})
