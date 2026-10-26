#!/usr/bin/env python3
"""Delete an owned auxiliary bucket, including historical versions and delete markers."""

import argparse
import json
import subprocess
import sys
import tempfile


class CleanupError(Exception):
    pass


def aws(profile, region, *args):
    try:
        result = subprocess.run(["aws", "--profile", profile, "--region", region,
                                 "--no-cli-pager", "--output", "json", *args],
                                capture_output=True, text=True, timeout=120, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CleanupError(f"AWS {args[0]} {args[1]} failed in {region}. "
                           "Check permissions/authentication; bucket cleanup was not completed.") from None


def delete_bucket(profile, seed_region, bucket, account):
    owner = ["--bucket", bucket, "--expected-bucket-owner", account]
    location = aws(profile, seed_region, "s3api", "get-bucket-location", *owner).get("LocationConstraint")
    region = {None: "us-east-1", "EU": "eu-west-1"}.get(location, location)

    def drain(versions):
        previous = None
        while True:
            operation = "list-object-versions" if versions else "list-objects-v2"
            page = aws(profile, region, "s3api", operation, *owner, "--no-paginate")
            entries = page.get("Versions", []) + page.get("DeleteMarkers", []) if versions else page.get("Contents", [])
            objects = [{"Key": entry["Key"], **({"VersionId": entry["VersionId"]} if versions else {})}
                       for entry in entries]
            if not objects:
                if page.get("IsTruncated"):
                    raise CleanupError("S3 returned an empty truncated page; bucket cleanup was not completed.")
                return
            current = frozenset((item["Key"], item.get("VersionId")) for item in objects)
            if current == previous:
                raise CleanupError("S3 contents did not change after deletion; bucket cleanup was not completed.")
            previous = current
            for offset in range(0, len(objects), 1000):
                with tempfile.NamedTemporaryFile(mode="w", prefix="crux-delete-bucket-", suffix=".json") as stream:
                    json.dump({"Objects": objects[offset:offset + 1000], "Quiet": True}, stream)
                    stream.flush()
                    response = aws(profile, region, "s3api", "delete-objects", *owner, "--delete", "file://" + stream.name)
                if response.get("Errors"):
                    raise CleanupError("S3 refused one or more object/version deletions. Check Object Lock, MFA Delete "
                                       "and permissions; bucket cleanup was not completed.")
            # Re-list from the beginning: deleting a page can invalidate pagination positions.

    drain(versions=True)
    drain(versions=False)
    for operation, fields in (("list-object-versions", ("Versions", "DeleteMarkers")), ("list-objects-v2", ("Contents",))):
        remaining = aws(profile, region, "s3api", operation, *owner, "--no-paginate")
        if remaining.get("IsTruncated") or any(remaining.get(field) for field in fields):
            raise CleanupError("Bucket changed during cleanup; stop remaining writers and retry.")
    aws(profile, region, "s3api", "delete-bucket", *owner)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "region", "bucket", "account"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        delete_bucket(args.profile, args.region, args.bucket, args.account)
    except (CleanupError, KeyError, TypeError) as error:
        print(str(error) if isinstance(error, CleanupError) else "Unexpected S3 response; cleanup was not completed.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
