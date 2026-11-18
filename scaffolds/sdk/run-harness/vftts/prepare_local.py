"""Prepare private inputs for a 20-minute ciab-devenv Linux rehearsal; creates no cloud resources."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def prepare(credentials: Path, output: Path, provider_backstop: float, billing: str = "credits") -> None:
    output = output.resolve()
    if not math.isfinite(provider_backstop) or provider_backstop <= 0:
        raise ValueError("A positive, operator-verified provider backstop is required")
    ancestor = output
    while not ancestor.exists():
        ancestor = ancestor.parent
    if subprocess.run(["git", "-C", str(ancestor), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True, check=False).returncode == 0:
        raise ValueError("Private run inputs must be outside Git checkouts")
    raw = credentials.read_text()
    try:
        source = json.loads(raw)
        agent, experiment = source["OPENROUTER_API_KEY"], source["OPENROUTER_EXPERIMENT_API_KEY"]
    except json.JSONDecodeError:
        keys = re.findall(r"sk-or-v1-[A-Za-z0-9_-]+", raw)
        if len(keys) != 2:
            raise ValueError("Expected JSON credential names or exactly two keys: inference first, experiments second")
        agent, experiment = keys
    if not agent or not experiment or agent == experiment:
        raise ValueError("Distinct nonempty inference and experiment credentials required")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    shutil.copytree(ROOT / "smoke", output / "drop-in")
    values = {"OPENROUTER_API_KEY": agent, "OPENROUTER_EXPERIMENT_API_KEY": experiment,
              "OPENROUTER_EXPERIMENT_BUDGET_USD": "4", "OPENROUTER_EXPERIMENT_KEY_CAP_USD": str(provider_backstop),
              "OPENROUTER_EXPERIMENT_BILLING_MODE": billing,
              "OPENROUTER_EXPERIMENT_STATE_DIR": "/srv/crux-run/state/experiments-" + billing}
    descriptor = os.open(output / "run-secrets.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(values, stream)
    print(json.dumps({"private_inputs": str(output), "wall_seconds": 1200,
                      "inference_allowance_usd": 8, "experiment_allowance_usd": 4,
                      "experiment_provider_backstop_usd": provider_backstop}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment-provider-backstop-usd", type=float, required=True)
    parser.add_argument("--experiment-billing", choices=["credits", "byok"], default="credits")
    args = parser.parse_args()
    prepare(args.credentials, args.output, args.experiment_provider_backstop_usd, args.experiment_billing)
