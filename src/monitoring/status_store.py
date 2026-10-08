"""Status inventory, history and work leases in a dedicated DynamoDB table."""

import json
from decimal import Decimal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from review import CoverageError, digest

RETENTION = 90 * 86400
LEASE_SECONDS = 1200  # Greater than the Batch attempt timeout.


def conditional(error):
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def workload_key(instance_id, workload_id):
    return digest([instance_id, workload_id])[:32]


def numbers(value):
    return json.loads(json.dumps(value, default=float), parse_float=Decimal)


class StatusStore:
    def __init__(self, table):
        self.table = table

    def get(self, pk, sk):
        return self.table.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item", {})

    def fleet(self):
        rows, args = [], {"KeyConditionExpression": Key("pk").eq("FLEET"), "ConsistentRead": True}
        while True:
            page = self.table.query(**args)
            rows.extend(page["Items"])
            if not page.get("LastEvaluatedKey"):
                return rows
            args["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def update(self, key, values, clock, stamp):
        """Merge one independently versioned projection without overwriting newer data."""
        values = {**values, clock: stamp}
        names = {f"#n{i}": k for i, k in enumerate(values)}
        attrs = {f":v{i}": numbers(v) for i, v in enumerate(values.values())}
        names["#clock"] = clock
        attrs[":stamp"] = stamp
        try:
            self.table.update_item(
                Key={"pk": "FLEET", "sk": key},
                UpdateExpression="SET " + ", ".join(f"#n{i}=:v{i}" for i in range(len(values))),
                ConditionExpression="attribute_not_exists(#clock) OR #clock <= :stamp",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=attrs,
            )
        except ClientError as error:
            if not conditional(error):
                raise

    def sync(self, key, target, now, stale_seconds):
        self.update(
            key,
            {k: target[k] for k in ("instance_id", "workload_id", "slug", "state")}
            | {"stale_seconds": stale_seconds},
            "inventory_at",
            now,
        )

    def claim(self, key, end, owner, now):
        try:
            self.table.update_item(
                Key={"pk": "JOB#" + key, "sk": str(end)},
                UpdateExpression="SET lease_owner=:owner, lease_until=:until, expires_at=:ttl ADD attempts :one",
                ConditionExpression="attribute_not_exists(completed) AND (attribute_not_exists(lease_until) OR lease_until < :now) AND (attribute_not_exists(attempts) OR attempts < :max)",
                ExpressionAttributeValues={
                    ":owner": owner,
                    ":until": now + LEASE_SECONDS,
                    ":ttl": end + RETENTION,
                    ":one": 1,
                    ":now": now,
                    ":max": 3,
                },
            )
        except ClientError as error:
            if conditional(error):
                return False
            raise
        return True

    def checkpoint(self, key, end, owner, result_key):
        self.table.update_item(
            Key={"pk": "JOB#" + key, "sk": str(end)},
            UpdateExpression="SET result_key=:result",
            ConditionExpression="lease_owner=:owner",
            ExpressionAttributeValues={":owner": owner, ":result": result_key},
        )

    def release(self, key, end, owner, completed=False):
        self.table.update_item(
            Key={"pk": "JOB#" + key, "sk": str(end)},
            UpdateExpression=("SET completed=:done " if completed else "")
            + "REMOVE lease_owner, lease_until",
            ConditionExpression="lease_owner=:owner",
            ExpressionAttributeValues={":owner": owner, **({":done": True} if completed else {})},
        )

    def reserve(self, amount, limit):
        if not 0 < amount <= limit <= 500_000_000:
            raise CoverageError("Invalid or insufficient status inference budget")
        try:
            self.table.update_item(
                Key={"pk": "BUDGET", "sk": "inference"},
                UpdateExpression="ADD reserved_microusd :amount",
                ConditionExpression="attribute_not_exists(reserved_microusd) OR reserved_microusd <= :remaining",
                ExpressionAttributeValues={":amount": amount, ":remaining": limit - amount},
            )
        except ClientError as error:
            if conditional(error):
                raise CoverageError("Status inference budget exhausted") from None
            raise

    def publish(self, key, end, result):
        """History is idempotent; failed checks retain the previous successful snapshot."""
        item = numbers({"pk": "STATUS#" + key, "sk": f"WINDOW#{end:012}", **result})
        item["expires_at"] = end + RETENTION
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk) OR (#outcome <> :completed AND checked_at <= :checked)",
                ExpressionAttributeNames={"#outcome": "outcome"},
                ExpressionAttributeValues={
                    ":completed": "completed",
                    ":checked": item["checked_at"],
                },
            )
        except ClientError as error:
            if not conditional(error):
                raise
            item = self.get(item["pk"], item["sk"])
        self.update(
            key,
            {
                "attempt": item["outcome"],
                "attempt_at": item["checked_at"],
                "error": item.get("error", ""),
            },
            "attempt_end",
            end,
        )
        if item["outcome"] == "completed":
            self.update(
                key,
                {"latest": {k: v for k, v in item.items() if k not in ("pk", "sk", "expires_at")}},
                "success_end",
                end,
            )

    def history(self, key, before=None):
        condition = Key("pk").eq("STATUS#" + key)
        if before:
            condition &= Key("sk").lt(before)
        page = self.table.query(
            KeyConditionExpression=condition, ScanIndexForward=False, Limit=20, ConsistentRead=True
        )
        return page["Items"], page.get("LastEvaluatedKey", {}).get("sk")

    def pending(self, key, now):
        args = {
            "KeyConditionExpression": Key("pk").eq("JOB#" + key) & Key("sk").gte(str(now - 86400)),
            "ConsistentRead": True,
        }
        while True:
            page = self.table.query(**args)
            for item in page["Items"]:
                if (
                    not item.get("completed")
                    and item.get("attempts", 0) < 3
                    and item.get("lease_until", 0) < now
                ):
                    yield int(item["sk"])
            if not page.get("LastEvaluatedKey"):
                return
            args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
