"""Retire the old monitoring pipelines and delete their data before the SQL rollout."""

import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def reset(config):
    if boto3.client("sts").get_caller_identity()["Account"] != config["account_id"]:
        raise ValueError("Unexpected reset account")
    name = config["name"]
    if name != "crux-monitoring-ae211":
        raise ValueError("Reset is limited to the retired monitoring namespace")
    scheduler, batch, dynamodb = (
        boto3.client(service) for service in ("scheduler", "batch", "dynamodb")
    )
    retired_queues = []
    for suffix in ("", "-status"):
        resource = name + suffix
        try:
            schedule = scheduler.get_schedule(Name=resource, GroupName=resource)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        else:
            scheduler.update_schedule(
                Name=resource,
                GroupName=resource,
                State="DISABLED",
                ScheduleExpression=schedule["ScheduleExpression"],
                FlexibleTimeWindow=schedule["FlexibleTimeWindow"],
                Target=schedule["Target"],
            )
        queues = batch.describe_job_queues(jobQueues=[resource])["jobQueues"]
        for queue in queues:
            retired_queues.append(queue["jobQueueArn"])
            batch.update_job_queue(jobQueue=queue["jobQueueArn"], state="DISABLED")
            for status in ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"):
                for page in batch.get_paginator("list_jobs").paginate(
                    jobQueue=queue["jobQueueArn"], jobStatus=status
                ):
                    for job in page["jobSummaryList"]:
                        batch.terminate_job(
                            jobId=job["jobId"], reason="Approved fresh monitoring reset"
                        )
    deadline = time.monotonic() + 180
    while retired_queues:
        active = any(
            batch.list_jobs(jobQueue=queue, jobStatus=status)["jobSummaryList"]
            for queue in retired_queues
            for status in ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")
        )
        if not active:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Retired monitoring jobs have not stopped; refusing to delete data")
        time.sleep(5)
    for suffix in ("-incidents", "-status"):
        table = name + suffix
        try:
            dynamodb.describe_table(TableName=table)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
            continue
        tags = dynamodb.list_tags_of_resource(
            ResourceArn=f"arn:aws:dynamodb:{config['region']}:{config['account_id']}:table/{table}"
        )["Tags"]
        if not any(tag["Key"] == "Project" and tag["Value"] == "crux-monitoring" for tag in tags):
            raise ValueError("Refusing to retire an unowned table")
        dynamodb.update_table(TableName=table, DeletionProtectionEnabled=False)
        dynamodb.get_waiter("table_exists").wait(TableName=table)
    # Only retired prefixes are deleted. Repeated releases cannot erase SQL-era snapshots.
    s3 = boto3.client("s3")
    bucket = f"{name}-{config['account_id']}-{config['region']}"
    for prefix in (
        "config/deployment.json",
        "config/registry.json",
        "config/status.json",
        "inventory/",
        "reviews/",
        "status_checks/",
    ):
        for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objects:
                result = s3.delete_objects(
                    Bucket=bucket, Delete={"Objects": objects, "Quiet": True}
                )
                if result.get("Errors"):
                    raise RuntimeError("Retired monitoring artifacts could not be deleted")
    print("Retired monitoring stopped; old tables are ready for Terraform destruction.")


if __name__ == "__main__":
    reset(
        json.loads(
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("terraform/production.auto.tfvars.json")
            .read_text()
        )
    )
