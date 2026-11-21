"""Drive fresh, key-authenticated Antigravity CLI iterations over one persistent TabPFN workspace."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import signal
import sqlite3
import stat
import subprocess
import time
from pathlib import Path
from uuid import UUID

from budget import Budget, publish
from prepare import VERSION
from render import render


def cli_command(prompt: str, seconds: int) -> list[str]:
    """Build a fresh headless invocation pinned to the direct Gemini provider and Neon xhigh.

    :param prompt: Rendered instructions for this iteration.
    :param seconds: Wall-clock allocation in seconds.
    :returns: Headless CLI argument vector.
    """
    return ["agy", "--settings", '{"modelProvider":"gemini"}', "--model", "gemini-4-neon-xhigh",
            "--effort", "xhigh", "--dangerously-skip-permissions", "--output-format", "json",
            "--print-timeout", f"{seconds}s", "--print", prompt]


class Journal:
    """Record each attempted iteration before starting its CLI process.

    :ivar db: SQLite connection containing iteration attempts.
    """

    def __init__(self, state: Path):
        """Open the operator-owned iteration journal.

        :param state: Trusted study directory containing durable controller records.
        """
        self.db = sqlite3.connect(state / "iterations.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS iterations(
            number INTEGER PRIMARY KEY, phase TEXT, status TEXT, started REAL, ended REAL)""")

    def recover(self) -> None:
        """Mark interrupted invocations after systemd has reaped the old service cgroup.

        :returns: None after recording interrupted attempts.
        """
        with self.db:
            self.db.execute("UPDATE iterations SET status='interrupted',ended=? WHERE status='running'", (time.time(),))

    def begin(self, phase: str) -> int:
        """Allocate a unique, durable iteration number.

        :param phase: Current study phase.
        :returns: New iteration number.
        """
        with self.db:
            return self.db.execute("INSERT INTO iterations(phase,status,started) VALUES(?,'running',?)",
                                   (phase, time.time())).lastrowid

    def finish(self, number: int, status: str) -> None:
        """Close an iteration with its observed CLI status.

        :param number: Journal iteration number.
        :param status: Terminal event or iteration status.
        """
        with self.db:
            self.db.execute("UPDATE iterations SET status=?,ended=? WHERE number=?", (status, time.time(), number))

    def rows(self) -> list[sqlite3.Row]:
        """Return the iteration history in execution order.

        :returns: Attempts ordered by iteration number.
        """
        return self.db.execute("SELECT * FROM iterations ORDER BY number").fetchall()


class ResearchFiles:
    """Read regular artifacts beneath the research workspace without following writable links.

    :ivar root: Workspace directory for all artifact reads.
    """

    def __init__(self, workspace: Path):
        """Bind artifact operations to the dedicated workspace.

        :param workspace: Research worktree shared by the CLI and its workers.
        """
        self.root = workspace

    @contextlib.contextmanager
    def open(self, relative: str):
        """Open a file through directory descriptors and reject devices, FIFOs and symlinks.

        :param relative: Workspace-relative regular file path.
        :returns: Context manager yielding a binary stream.
        """
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Invalid artifact path")
        with contextlib.ExitStack() as stack:
            directory = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for part in path.parts[:-1]:
                directory = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            stream = stack.enter_context(os.fdopen(fd, "rb"))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Expected a regular artifact")
            yield stream

    def text(self, name: str, limit: int = 128_000) -> str:
        """Read bounded UTF-8 research notes or a small machine-readable manifest.

        :param name: File or event name.
        :param limit: Maximum admitted byte count.
        :returns: Decoded file contents.
        """
        with self.open(name) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"Artifact exceeds text limit: {name}")
        return data.decode()

    def digest(self, name: str) -> str:
        """Hash the exact artifact bytes in bounded memory.

        :param name: File or event name.
        :returns: Lowercase SHA-256 hex digest.
        """
        with self.open(name) as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def check_package(self, verdict: dict) -> None:
        """Verify the current paper, deliverables and each claim's artifact digest.

        :param verdict: Trusted panel verdict for the submitted revision.
        """
        if self.digest("paper/paper.pdf") != verdict["paper_sha256"]:
            raise ValueError("Submit the current PDF for blind review.")
        for name in ("README.md", "reproduce.sh", "paper/paper.tex", "results/index.html", "COMPLETION_REPORT.md"):
            with self.open(name) as source:
                if not source.read(8192).strip():
                    raise ValueError(f"Empty final artifact: {name}")
        report = self.text("COMPLETION_REPORT.md")
        if any(verdict[key] not in report for key in ("paper_sha256", "review_path")):
            raise ValueError("Record the current PDF digest and blind review path in the completion report.")
        claims = json.loads(self.text("evidence/claims.json"))
        if not isinstance(claims, list) or not claims:
            raise ValueError("Missing claim evidence")
        for entry in claims:
            if not entry["claim"] or not entry["command"] or self.digest(entry["artifact"]) != entry["sha256"]:
                raise ValueError("Claim evidence does not match its recorded digest")


class ResearchProcess:
    """Run model turns and reproduction as the unprivileged research user.

    :ivar account: Unprivileged research account.
    """

    def __init__(self, user: str, files: ResearchFiles, state: Path):
        """Keep the study user, workspace and private output paths separate.

        :param user: Unix account running the research process.
        :param files: Workspace artifact reader.
        :param state: Trusted study directory containing durable controller records.
        """
        self.account, self.files, self.state = pwd.getpwnam(user), files, state
        if self.account.pw_uid == 0:
            raise ValueError("The research user must be unprivileged")

    def run(self, argv: list[str], log: str, seconds: int, *, inference: bool) -> int:
        """Launch in its own process group, with the dedicated key only for model turns.

        :param argv: Executable and arguments for the unprivileged child.
        :param log: Output basename in the private state directory.
        :param seconds: Wall-clock allocation in seconds.
        :param inference: Whether the child needs the research model credential.
        :returns: Child exit status or 124 on timeout.
        """
        env = {"HOME": self.account.pw_dir, "USER": self.account.pw_name,
               "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
        if inference:
            env["GEMINI_API_KEY"] = os.environ["GEMINI_API_KEY"]
        with (
            (self.state / log).open("wb") as out,
            (self.state / (log + ".err")).open("wb") as err,
            subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL, cwd=self.files.root, env=env,
                             user=self.account.pw_uid, group=self.account.pw_gid, extra_groups=[],
                             start_new_session=True) as proc,
        ):
            try:
                return proc.wait(timeout=seconds + 2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return 124


class ResearchLoop:
    """Advance research by observed evidence and trusted panel decisions.

    :ivar journal: Durable state for CLI iteration attempts.
    """

    def __init__(self, state: Path, workspace: Path, user: str, turn_seconds: int):
        """Load the run clock and bind research execution to the selected user.

        :param state: Trusted study directory containing durable controller records.
        :param workspace: Research worktree shared by the CLI and its workers.
        :param user: Unix account running the research process.
        :param turn_seconds: Maximum wall time of a single CLI iteration.
        """
        self.state, self.files = state, ResearchFiles(workspace)
        self.process = ResearchProcess(user, self.files, state)
        self.journal, self.budget = Journal(state), Budget(state)
        self.turn_seconds = turn_seconds
        self.public = state / "public"

    def finalize(self, verdict: dict, remaining: int) -> str:
        """Reproduce as the research user, then recheck every deliverable before publishing.

        :param verdict: Trusted panel verdict for the submitted revision.
        :param remaining: Wall time left for reproduction, in seconds.
        :returns: An empty string on completion or the repair feedback for the next iteration.
        """
        try:
            self.files.check_package(verdict)
            code = self.process.run(["/bin/bash", "reproduce.sh"], "reproduction.log", remaining, inference=False)
            publish(self.public / "REPRODUCTION_STATUS.json", {"exit_code": code})
            if code:
                return render("repair.md", code=code)
            self.files.check_package(verdict)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return render("package.md", error=exc)
        publish(self.public / "RUN_OUTCOME.json", {"accepted": verdict["passed"], "paper_sha256": verdict["paper_sha256"],
                "review_path": verdict["review_path"], "reproduction_exit_code": 0})
        return ""

    def run(self) -> None:
        """Hold the single controller lock across fresh CLI iterations until completion or exhaustion.

        :returns: None when the study is packaged.
        """
        if not os.environ.get("GEMINI_API_KEY") or subprocess.check_output(["agy", "--version"], text=True).strip() != VERSION:
            raise RuntimeError("The pinned CLI and dedicated GEMINI_API_KEY are required")
        if self.turn_seconds <= 0:
            raise ValueError("Iteration timeout must be positive")
        self.public.mkdir(exist_ok=True)
        with (self.state / "loop.lock").open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.journal.recover()
            brief = self.files.text("BRIEF.md")
            feedback = render("handoff.md")
            failures = 0
            while not (self.public / "RUN_OUTCOME.json").exists():
                b = self.budget.status()
                if b["phase"] in {"stopped", "accounting-hold"}:
                    raise RuntimeError(f"Budget controller reports {b['phase']}")
                remaining = max(1, int(b["remaining_seconds"]))
                phase = b["phase"]
                review_path = self.public / "REVIEW_STATUS.json"
                verdict = json.loads(review_path.read_text()) if review_path.is_file() else {}
                if verdict.get("pending"):
                    # Allow the external panel to finish reading the submitted revision.
                    try:
                        same_paper = self.files.digest("paper/paper.pdf") == verdict.get("paper_sha256")
                    except (ValueError, OSError):
                        same_paper = False
                    if same_paper:
                        time.sleep(min(5, remaining))
                        continue
                if verdict.get("passed") or (self.state / "finalizing").exists():
                    phase = "finalize"
                if phase == "finalize":
                    (self.state / "finalizing").touch()
                    if verdict.get("complete"):
                        feedback = self.finalize(verdict, remaining)
                        if not feedback:
                            return
                number = self.journal.begin(phase)
                continuation = render(f"{phase}-continue.md", iteration=number, phase=phase, feedback=feedback)
                prompt = render("iteration.md", brief=brief, continuation=continuation, phase=phase, iteration=number,
                                **{"remaining-seconds": remaining, "spent-usd": f"{b['metered_usd']:.4f}",
                                   "limit-usd": f"{b['limit_usd']:.2f}"})
                log = f"iteration-{number:06d}.json"
                seconds = min(self.turn_seconds, remaining)
                if phase == "research":
                    reserve_at = self.budget.start_time + 0.9 * self.budget.seconds
                    seconds = max(1, min(seconds, int(reserve_at - time.time())))
                code = self.process.run(cli_command(prompt, seconds), log, seconds, inference=True)
                success = False
                if code == 0:
                    try:
                        envelope = json.loads((self.state / log).read_bytes())
                        success = (envelope.get("status") or (envelope.get("result") or {}).get("status")) == "SUCCESS"
                        if success:
                            conversation = str(UUID(envelope["conversation_id"]))
                            brain = Path(self.process.account.pw_dir) / ".gemini/antigravity-cli/brain"
                            success = (brain / conversation / ".system_generated/swarm/swarm.json").is_file()
                    except (ValueError, KeyError, AttributeError, TypeError):
                        pass
                self.journal.finish(number, "returned" if success else "failed")
                failures = 0 if success else failures + 1
                feedback = render("handoff.md" if success else "failed.md")
                time.sleep(min(60, 2 ** min(failures, 6), remaining))


def main() -> None:
    """Start the production controller as a systemd service in the run cgroup.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--turn-seconds", type=int, default=3600)
    args = parser.parse_args()
    os.umask(0o077)
    ResearchLoop(args.state, args.workspace, args.user, args.turn_seconds).run()


if __name__ == "__main__":
    main()
