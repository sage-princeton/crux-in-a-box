"""Provision with the fleet scripts, stage verified pilot inputs, and start the run."""

import argparse
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from preflight import read_status
from prepare import prepare

REMOTE_LAUNCH = r"""
set -eu
cd /srv/crux-run
if systemctl is-active --quiet crux-sdk-run; then
  echo 'Run is already active; refusing to replace its inputs.' >&2
  exit 2
fi
python3 - <<'PY'
import json
from pathlib import Path
state = Path('/srv/crux-run/state/state.json')
if state.exists() and json.loads(state.read_text()).get('usage', {}).get('started_at'):
    raise SystemExit('Run has already started; resume its service without restaging.')
PY
test -f /etc/crux-run.env
staging=$(mktemp -d /srv/crux-run/.contrastive-launch.XXXXXX)
echo 'Receiving the script-prepared research bundle.'
tar -xf - -C "$staging"
mv run-harness "$staging/template"
mv "$staging/run-harness" run-harness
cd run-harness/workspace/paper
latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  -p EnvironmentFile=/etc/crux-run.env \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
  --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
sudo systemctl start crux-sdk-run
systemctl is-active --quiet crux-sdk-run
systemctl show crux-sdk-run -p ActiveState -p SubState -p MainPID -p ExecMainStartTimestamp
""".strip()


def preflight_spend(workspace_scripts: Path) -> float:
    secrets = json.loads((workspace_scripts / "run-secrets-contrastive-rl.json").read_text())
    data = read_status("https://openrouter.ai/api/v1/key", secrets["OPENROUTER_API_KEY"])["data"]
    spent = data["usage"]
    if type(spent) not in (int, float) or not 0 <= spent < 100:
        raise ValueError("No inference budget remains for this pilot")
    return spent


def launch(root: Path, archive: Path, slug: str, needs_provisioning: bool) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
        raise ValueError("Run slug must contain lowercase letters, digits and hyphens")
    workspace_scripts = root.resolve().parents[1] / "src/ec2-workspaces"
    scanner = root.resolve().parents[1] / "utils/scan-secrets.py"
    with tempfile.TemporaryDirectory(prefix="contrastive-launch-") as temporary:
        bundle = Path(temporary) / "run-harness"
        prepare(root, bundle, archive)
        shutil.copy2(scanner, bundle / "scan-secrets.py")
        if needs_provisioning:
            subprocess.run([str(workspace_scripts / "make-new-workspace.sh"), slug,
                            "--base-config", str(workspace_scripts / "placeholders-contrastive-rl-base.txt"),
                            "--base-secrets", str(workspace_scripts / "run-secrets-contrastive-rl.json")], check=True)
        spent = preflight_spend(workspace_scripts)
        budget = {"inference_spent_usd": spent, "remaining_inference_usd": 100 - spent, "total_inference_usd": 100}
        (bundle / "workspace/PRELAUNCH.json").write_text(json.dumps(budget, indent=2) + "\n")
        config = bundle / "scaffold.toml"
        text = config.read_text()
        if text.count("max_cost_usd = 100\n") != 1:
            raise ValueError("Expected the nominal $100 pilot inference budget")
        config.write_text(text.replace("max_cost_usd = 100\n", f"max_cost_usd = {100 - spent:.8f}\n"))
        with (bundle / "workspace/PLAN.md").open("a") as ledger:
            ledger.write(f"\n## Prelaunch inference\nAlready spent ${spent:.8f} on probes; "
                         f"${100 - spent:.8f} remains within the $100 total. See PRELAUNCH.json.\n")
        print(f"Staging verified pilot bundle and starting {slug} through the launch script.", flush=True)
        with tempfile.TemporaryFile() as payload:
            with tarfile.open(fileobj=payload, mode="w:") as package:
                package.add(bundle, arcname="run-harness")
            payload.seek(0)
            subprocess.run(["ssh", slug, REMOTE_LAUNCH], stdin=payload, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--provisioned", action="store_true", help="Use a box already prepared by make-new-workspace.sh")
    args = parser.parse_args()
    try:
        launch(Path(__file__).resolve().parent, args.archive, args.slug, not args.provisioned)
    except (OSError, ValueError, subprocess.CalledProcessError):
        parser.exit(2, "Pilot launch failed; inspect script output. Existing research is never restaged.\n")


if __name__ == "__main__":
    main()
