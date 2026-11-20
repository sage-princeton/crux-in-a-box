"""Install and operate the one-off TabPFN study on an isolated Ubuntu 24.04 host."""
import argparse
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

from budget import DEFAULT_RUNPOD_USD
from prepare import HERE, VERSION, install_cli
from resources import OPTIONAL

STUDY = Path("/var/lib/tabpfn-study")
PUBLIC = STUDY / "public"
CONF = Path("/etc/tabpfn-study")
ACCOUNT = "tabpfn-research"
CONTROL = "tabpfn-review"
HOME_DIR = Path("/home") / ACCOUNT
WORK = HOME_DIR / "research"
BRAIN = HOME_DIR / ".gemini/antigravity-cli/brain"
SDK = HERE.parents[1] / "scaffolds/sdk"
VENV = Path("/opt/tabpfn-study-venv")


def run(*args: object) -> None:
    """Execute an installation or service-management step and propagate failure.

    :param args: Command and arguments for the checked subprocess.
    """
    subprocess.run([str(arg) for arg in args], check=True)


def directory(path: Path, user: str, mode: int) -> None:
    """Create a fixed service directory with explicit owner, group and permissions.

    :param path: Filesystem destination.
    :param user: Unix account running the research process.
    :param mode: Unix permission bits.
    """
    run("install", "-d", "-m", f"{mode:o}", "-o", user, "-g", user, path)


def service_files(slug: str) -> dict[str, str]:
    """Render systemd definitions using the installed checkout and dedicated host identities.

    :param slug: Study identifier shared by traces and blind review calls.
    :returns: Unit names mapped to their systemd definitions.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
        raise ValueError("Run ID must use lowercase letters, digits and hyphens")
    common = f"""[Unit]
PartOf=tabpfn-study.target
After=network-online.target
ConditionPathExists={STUDY}/budget.json
[Service]
Restart=on-failure
RestartSec=10
KillMode=control-group
TimeoutStopSec=20
WorkingDirectory={HERE}
"""
    units = {}
    units["tabpfn-budget.service"] = common + f"""ExecStart=/usr/bin/python3 {HERE}/budget.py watch {STUDY} {PUBLIC}/BUDGET.json tabpfn-research.service tabpfn-review.service
"""
    units["tabpfn-runpod.service"] = common + f"""User={CONTROL}
EnvironmentFile={CONF}/runpod.env
Environment=RUN_SLUG={slug}
Environment=PYTHONPATH={SDK}:{SDK}/run-harness/tabpfn-full
ExecStart={VENV}/bin/python {HERE}/runpod_meter.py {STUDY}
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
InaccessiblePaths={CONF}
ReadWritePaths={STUDY}
"""
    units["tabpfn-traces.service"] = common + f"""EnvironmentFile={CONF}/langfuse.env
ExecStart=/usr/bin/python3 {HERE}/host.py trace {slug}
ProtectSystem=strict
ProtectHome=tmpfs
BindReadOnlyPaths={BRAIN}
InaccessiblePaths={CONF}
ReadWritePaths={STUDY}
"""
    units["tabpfn-review.service"] = common + f"""User={CONTROL}
EnvironmentFile={CONF}/review.env
Environment=PYTHONPATH={SDK}:{SDK}/run-harness/tabpfn-full
ExecStart={VENV}/bin/python {HERE}/review.py --workspace {WORK} --state {STUDY} --run {slug}
AmbientCapabilities=CAP_DAC_READ_SEARCH
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
NoNewPrivileges=true
ProtectHome=tmpfs
BindReadOnlyPaths={WORK}
ProtectSystem=strict
InaccessiblePaths={CONF}
ReadWritePaths={STUDY}
UMask=0022
"""
    units["tabpfn-research.service"] = common.replace("[Service]", f"""After=tabpfn-budget.service tabpfn-traces.service
ConditionPathExists=!{PUBLIC}/RUN_OUTCOME.json
ConditionPathExists=!{STUDY}/stop.json
[Service]""") + f"""EnvironmentFile={CONF}/research.env
Environment=RUN_SLUG={slug}
ExecStart=/usr/bin/python3 {HERE}/loop.py --state {STUDY} --workspace {WORK} --user {ACCOUNT}
ProtectSystem=full
NoNewPrivileges=true
InaccessiblePaths={CONF} {HERE}/review.py {SDK}/run-harness/tabpfn-full/prompts/review.md {SDK}/run-harness/tabpfn-full/scaffold.toml
PrivateTmp=true
"""
    units["tabpfn-study.target"] = """[Unit]
Description=TabPFN Antigravity research experiment
Wants=network-online.target tabpfn-runpod.service tabpfn-budget.service tabpfn-traces.service tabpfn-review.service tabpfn-research.service
[Install]
WantedBy=multi-user.target
"""
    return units


def install(slug: str) -> None:
    """Provision Linux dependencies, research inputs and services without starting an experiment.

    :param slug: Unique study and EC2 ownership tag.
    """
    units = service_files(slug)
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get", "update", "-qq")
    run("apt-get", "install", "-y", "--no-install-recommends", "python3-venv", "python3-pip", "git", "build-essential",
        "latexmk", "texlive-latex-extra", "texlive-fonts-recommended", "texlive-science", "poppler-utils",
        "fonts-dejavu-core", "curl", "ca-certificates")
    for user, home in ((ACCOUNT, HOME_DIR), (CONTROL, STUDY)):
        try:
            pwd.getpwnam(user)
        except KeyError:
            run("useradd", "--system", "--user-group", "--home-dir", home, "--no-create-home",
                "--shell", "/usr/sbin/nologin", user)
    directory(HOME_DIR, ACCOUNT, 0o750)
    directory(STUDY, CONTROL, 0o711)
    directory(PUBLIC, CONTROL, 0o755)
    directory(PUBLIC / "reviews", CONTROL, 0o755)
    directory(CONF, "root", 0o700)
    for sub in (HOME_DIR / ".gemini", HOME_DIR / ".gemini/antigravity-cli", BRAIN, HOME_DIR / ".gemini/config"):
        run("runuser", "-u", ACCOUNT, "--", "install", "-d", "-m", "700", sub)
    binary = Path("/usr/local/bin/agy")
    if not binary.exists() or subprocess.check_output([binary, "--version"], text=True).strip() != VERSION:
        install_cli(binary)
    run("python3", "-m", "venv", VENV)
    run(VENV / "bin/pip", "install", "--disable-pip-version-check", "-r", SDK / "requirements.txt")
    run("runuser", "-u", ACCOUNT, "--", "python3", HERE / "prepare.py", "--seed", WORK)
    run("runuser", "-u", ACCOUNT, "--", "git", "-C", WORK, "init", "-q")
    for key, value in (("user.name", "Anonymous Researcher"), ("user.email", "research@localhost")):
        run("runuser", "-u", ACCOUNT, "--", "git", "-C", WORK, "config", key, value)
    for name in ("REVIEW_STATUS.json", "REVIEW_HISTORY.md", "REPRODUCTION_STATUS.json", "RUN_OUTCOME.json", "reviews", "BUDGET.json"):
        path = WORK / name
        if not path.is_symlink() and not path.exists():
            run("runuser", "-u", ACCOUNT, "--", "ln", "-s", PUBLIC / name, path)
    config = HOME_DIR / ".gemini/config/hooks.json"
    hook = {"tabpfn-budget": {"PreInvocation": [{"type": "command", "command":
            f"/usr/bin/python3 {HERE}/budget.py hook {PUBLIC}/BUDGET.json"}]}}
    hook["tabpfn-tools"] = {"PreToolUse": [{"matcher": "", "hooks": [
        {"type": "command", "command": f"/usr/bin/python3 {HERE}/tool_hook.py"}]}]}
    # Write workspace-owned paths as the research user, including when provisioning is retried.
    subprocess.run(["runuser", "-u", ACCOUNT, "--", "tee", str(config)], input=json.dumps(hook),
                   text=True, stdout=subprocess.DEVNULL, check=True)
    for name, content in units.items():
        (Path("/etc/systemd/system") / name).write_text(content)
    run("systemctl", "daemon-reload")


def credentials(values: dict) -> None:
    """Write separate root-readable systemd environment files from an operator's stdin.

    :param values: Credential fields received over SSH stdin.
    """
    directory(CONF, "root", 0o700)
    groups = {"research": ["GEMINI_API_KEY"], "review": ["OPENROUTER_API_KEY"],
              "langfuse": ["LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]}
    required = {key for keys in groups.values() for key in keys}
    if not required.issubset(values) or set(values) - required - set(OPTIONAL):
        raise ValueError("Missing required or unexpected study credential fields")
    groups["runpod"] = ["RUNPOD_API_KEY"] if "RUNPOD_API_KEY" in values else []
    groups["research"].extend(key for key in OPTIONAL if key in values)
    if not all(isinstance(s, str) and s and all(c.isprintable() for c in s) for s in values.values()):
        raise ValueError("Invalid environment field")
    for group, keys in groups.items():
        path = CONF / f"{group}.env"
        fd = os.open(path.with_suffix(".new"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as out:
            for key in keys:
                out.write(f"{key}={json.dumps(values[key], ensure_ascii=False)}\n")
        path.with_suffix(".new").replace(path)


def main() -> None:
    """Install, configure, start/resume or trace the isolated study.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("install").add_argument("slug")
    actions.add_parser("credentials")
    start = actions.add_parser("start")
    start.add_argument("--seconds", type=int, default=604800)
    start.add_argument("--usd", type=float, default=15000)
    start.add_argument("--runpod-usd", type=float, default=DEFAULT_RUNPOD_USD)
    actions.add_parser("trace").add_argument("slug")
    args = parser.parse_args()
    if args.action == "install":
        install(args.slug)
    elif args.action == "credentials":
        credentials(json.load(sys.stdin))
    elif args.action == "start":
        if any(not (CONF / name).is_file() for name in ("research.env", "review.env", "langfuse.env", "runpod.env")):
            raise ValueError("Configure study credentials before starting")
        run("runuser", "-u", CONTROL, "--", "python3", HERE / "budget.py", "start", STUDY,
            "--seconds", args.seconds, "--usd", args.usd, "--runpod-usd", args.runpod_usd)
        run("systemctl", "enable", "--now", "tabpfn-study.target")
    else:
        epoch = json.loads((STUDY / "budget.json").read_text())["started_at"]
        os.execv(sys.executable, [sys.executable, str(HERE / "telemetry.py"), "--brain", str(BRAIN),
                                  "--db", str(STUDY / "traces.db"), "--since", str(int(epoch)), "--run", args.slug])


if __name__ == "__main__":
    main()
