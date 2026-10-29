"""Extract and scrub SDK sessions and run-filtered Langfuse observations on the box."""

import argparse
import base64
import http.client
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path


def read_page(env: dict, query: dict) -> dict:
    url = urllib.parse.urlsplit(env["LANGFUSE_BASE_URL"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Langfuse requires an HTTPS base URL without credentials")
    auth = base64.b64encode((env["LANGFUSE_PUBLIC_KEY"] + ":" + env["LANGFUSE_SECRET_KEY"]).encode()).decode()
    path = url.path.rstrip("/") + "/api/public/observations?" + urllib.parse.urlencode(query)
    connection = http.client.HTTPSConnection(url.hostname, url.port, timeout=20)
    try:
        connection.request("GET", path, headers={"Authorization": "Basic " + auth})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Langfuse export request failed")
        body = response.read(16_777_217)
        if len(body) > 16_777_216:
            raise ValueError("Langfuse export page too large")
        return json.loads(body)
    finally:
        connection.close()


def observations(env: dict, start: str):
    environment = env["RUN_SLUG"].lower()
    query = {"environment": environment, "fromStartTime": start,
             "toStartTime": datetime.now(UTC).isoformat(), "limit": 50}
    for page in range(1, 1001):
        body = read_page(env, {**query, "page": page})
        for observation in body["data"]:
            if observation.get("environment") != environment:
                raise ValueError("Langfuse observation environment differs from this run")
            yield observation
        if page >= body["meta"]["totalPages"]:
            return
    raise ValueError("Langfuse export pagination limit reached")


def scrub(value, literals: list[str], patterns):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if isinstance(item, str) and re.search(
            r"(?i)(key|token|secret|password|credential)", key) else scrub(item, literals, patterns)
                for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, literals, patterns) for item in value]
    if isinstance(value, str):
        for literal in literals:
            value = value.replace(literal, "[REDACTED]")
        for _, pattern in patterns:
            value = pattern.sub("[REDACTED]", value)
    return value


def export_trace(state: Path, output: Path, env: dict, scanner: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ValueError("Export output already exists; choose a fresh directory")
    spec = importlib.util.spec_from_file_location("pilot_secret_scanner", scanner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    literals = sorted({value for key, value in env.items() if len(value) >= 8 and re.search(
        r"(?i)(key|token|secret|password|credential)", key)}, key=len, reverse=True)
    if not literals:
        raise ValueError("Load the service environment before exporting; no secret blacklist available")
    saved_state = json.loads((state / "state.json").read_text())
    start = saved_state["usage"]["started_at"]
    if not start:
        raise ValueError("Run has not started")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".contrastive-export-", dir=output.parent) as temporary:
        staged = Path(temporary) / "out"
        staged.mkdir(mode=0o700)
        count, observation_count = 0, 0
        with (staged / "run_events.jsonl").open("w") as stream:
            database_url = (state / "sessions.sqlite").resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(database_url, uri=True) as database:
                for session, created, message in database.execute(
                        "SELECT session_id, created_at, message_data FROM agent_messages ORDER BY id"):
                    event = {"source": "session", "session": session, "timestamp": created, "message": json.loads(message)}
                    stream.write(json.dumps(scrub(event, literals, module.COMPILED)) + "\n")
                    count += 1
            for observation in observations(env, start):
                event = {"source": "langfuse", "observation": observation}
                stream.write(json.dumps(scrub(event, literals, module.COMPILED)) + "\n")
                count += 1
                observation_count += 1
        if not observation_count:
            raise ValueError("No Langfuse observations available; allow trace ingestion before exporting")
        summary = {"run_slug": env["RUN_SLUG"], "from_timestamp": start, "events": count,
                   "state": saved_state, "exported_at": datetime.now(UTC).isoformat()}
        (staged / "run_summary.json").write_text(json.dumps(scrub(summary, literals, module.COMPILED), indent=2))
        blacklist = Path(temporary) / "blacklist.txt"
        blacklist.touch(mode=0o600)
        blacklist.write_text("\n".join(literals) + "\n")
        result = subprocess.run([sys.executable, str(scanner), "--quiet", "--blacklist", str(blacklist), str(staged)],
                                capture_output=True, check=False)
        if result.returncode:
            raise ValueError("Secret scan failed; export withheld on the box")
        staged.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("/srv/crux-run/state"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scanner", type=Path, required=True)
    args = parser.parse_args()
    try:
        export_trace(args.state_dir, args.output, dict(os.environ), args.scanner)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        parser.exit(2, "Export failed; no transferable output installed. Check service access, state and scanner.\n")
    print("Scrubbed, scanned SDK export ready; raw sessions and credentials stayed on the box.")


if __name__ == "__main__":
    main()
