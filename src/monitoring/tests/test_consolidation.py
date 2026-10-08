import boto3
import pytest
from moto import mock_aws

from ci.consolidate_state import consolidate


def test_consolidation_preserves_history_budget_and_ttl_and_is_idempotent():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        for table, keys in [("old", ["pk"]), ("shared", ["pk", "sk"])]:
            client.create_table(
                TableName=table,
                BillingMode="PAY_PER_REQUEST",
                KeySchema=[
                    {"AttributeName": k, "KeyType": "HASH" if k == "pk" else "RANGE"} for k in keys
                ],
                AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in keys],
            )
        source = {
            "pk": {"S": "BUDGET#inference"},
            "reserved_microusd": {"N": "3699950"},
            "expires_at": {"N": "9999999999"},
        }
        history = {"pk": source["pk"], "sk": {"S": "STATE"}, "status": {"S": "closed"}}
        client.put_item(TableName="old", Item=source)
        client.put_item(TableName="shared", Item=history)
        backup = client.create_backup(TableName="old", BackupName="before-consolidation")[
            "BackupDetails"
        ]["BackupArn"]
        first = consolidate(client, "old", "shared", backup)
        assert first["verified_records"] == 1
        assert consolidate(client, "old", "shared", backup) == first
        items = client.scan(TableName="shared")["Items"]
        assert history in items
        assert {**source, "sk": {"S": "OPERATION"}} in items
        assert client.scan(TableName="old")["Items"] == [source]
        client.put_item(
            TableName="shared",
            Item={**source, "sk": {"S": "OPERATION"}, "reserved_microusd": {"N": "5000000"}},
        )
        with pytest.raises(RuntimeError, match="conflict"):
            consolidate(client, "old", "shared", backup)
        assert client.get_item(
            TableName="shared", Key={"pk": source["pk"], "sk": {"S": "OPERATION"}}
        )["Item"]["reserved_microusd"] == {"N": "5000000"}
