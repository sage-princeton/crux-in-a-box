"""Report pilot readiness without starting agents or allocating resources."""

import argparse
import http.client
import json
import stat
import urllib.parse
from pathlib import Path

REQUIRED_CREDENTIALS = ("OPENROUTER_API_KEY", "RUNPOD_API_KEY", "COST_TRACKER_URL", "COST_TRACKER_TOKEN")


def read_status(url: str, credential: str) -> dict:
    parsed = urllib.parse.urlsplit(url)
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=15)
    try:
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request("GET", path, headers={"Authorization": "Bearer " + credential})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Read-only API check failed")
        return json.loads(response.read(1_048_576))
    finally:
        connection.close()


def readiness(root: Path, secret_path: Path) -> dict:
    secrets = json.loads(secret_path.read_text())
    report = {
        "missing_credentials": [name for name in REQUIRED_CREDENTIALS
                                if not isinstance(secrets.get(name), str) or not secrets[name].strip()
                                or secrets[name].startswith("<")],
        "private_secrets_file": stat.S_IMODE(secret_path.stat().st_mode) == 0o600,
        "source_staged": (root / "workspace/SOURCE.json").is_file()
                         and (root / "workspace/crtr/runner.py").is_file(),
    }
    report["ready"] = (not report["missing_credentials"] and report["private_secrets_file"]
                       and report["source_staged"])
    return report


def check_services(secrets: dict) -> dict:
    requests = {
        "openrouter": ("https://openrouter.ai/api/v1/key", "OPENROUTER_API_KEY"),
        "runpod": ("https://api.runpod.io/v2/pods?limit=1", "RUNPOD_API_KEY"),
    }
    results = {}
    for name, (url, key) in requests.items():
        if not secrets.get(key):
            results[name] = {"ready": False, "error": "credential missing"}
            continue
        try:
            body = read_status(url, secrets[key])
            is_ready = isinstance(body.get("pods"), list)
            if name == "openrouter":
                data = body["data"]
                remaining = data.get("limit_remaining")
                is_ready = remaining is None or remaining > 0
                results[name] = {"ready": is_ready, "limit": data.get("limit"),
                                 "limit_reset": data.get("limit_reset"), "usage": data.get("usage")}
            else:
                results[name] = {"ready": is_ready}
        except (OSError, ValueError, KeyError):
            results[name] = {"ready": False, "error": "read-only API check failed"}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--secrets", required=True, type=Path)
    parser.add_argument("--check-services", action="store_true", help="Read-only API checks; no model calls")
    args = parser.parse_args()
    try:
        report = readiness(args.root, args.secrets)
        if args.check_services:
            report["services"] = check_services(json.loads(args.secrets.read_text()))
            report["ready"] = report["ready"] and all(value["ready"] for value in report["services"].values())
    except (OSError, ValueError, TypeError):
        parser.exit(2, "Readiness failed: verify the bundle and private secrets JSON.\n")
    print(json.dumps(report, indent=2))
    parser.exit(0 if report["ready"] else 2)


if __name__ == "__main__":
    main()
