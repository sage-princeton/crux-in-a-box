"""Copy quiescent legacy operational state into the shared incident table.

Requires an AVAILABLE on-demand source backup. Never overwrites differing rows;
does not delete the source. Run only while the scheduler and workers are stopped.
"""

import argparse
import hashlib
import json

import boto3
from botocore.exceptions import ClientError


def rows(client, table):
    result = {}
    for page in client.get_paginator("scan").paginate(TableName=table, ConsistentRead=True):
        for item in page["Items"]:
            if "sk" in item:
                raise ValueError("Source must be the legacy partition-key-only table")
            result[item["pk"]["S"]] = item
    return result


def consolidate(client, source, destination, backup):
    if source == destination:
        raise ValueError("Source and destination must differ")
    details = client.describe_backup(BackupArn=backup)["BackupDescription"]
    if (
        details["SourceTableDetails"]["TableName"] != source
        or details["BackupDetails"]["BackupStatus"] != "AVAILABLE"
    ):
        raise ValueError("An available source backup is required")
    before = rows(client, source)
    for pk, original in before.items():
        copied = {**original, "sk": {"S": "OPERATION"}}
        try:
            client.put_item(
                TableName=destination,
                Item=copied,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        saved = client.get_item(
            TableName=destination,
            Key={"pk": {"S": pk}, "sk": {"S": "OPERATION"}},
            ConsistentRead=True,
        ).get("Item")
        if saved != copied:
            raise RuntimeError("Destination conflict; no existing record was overwritten")
    if rows(client, source) != before:
        raise RuntimeError(
            "Source changed during migration; keep source and reconcile before cutover"
        )
    # Actual values were compared above. Return only count and a fingerprint, never data.
    return {
        "verified_records": len(before),
        "source_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            consolidate(boto3.client("dynamodb"), args.source, args.destination, args.backup)
        )
    )
