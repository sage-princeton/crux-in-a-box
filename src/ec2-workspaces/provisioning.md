# Run software declarations

Commit an optional `provision.json` at the root of the directory selected by
`DROP_IN_PATH`, next to `scaffold.toml`. The shared EC2 provisioner validates the
committed declaration before creating resources, stages a root-owned snapshot at
`/etc/crux-provision.json`, and installs software before delivering credentials.
Omitting the file selects no extra dependencies. Reprovisioning without it clears
the declaration; it does not uninstall software or delete existing state.

Start with [`provision.json.example`](provision.json.example). It installs the
distribution's PDF tools and a SHA-256-pinned `gog` v0.28.0 Linux **amd64** release.
For an ARM instance, select the ARM release URL and its matching digest instead.
The digest was verified against the [upstream release](https://github.com/openclaw/gogcli/releases/tag/v0.28.0).
Keep credentials out of this committed file, including URLs, command arguments,
and diagnostic hints.

| Field | Contract |
|---|---|
| `version` | Required integer `1`; unknown fields fail validation. |
| `apt_packages` | Optional package names, optionally `name=version`. Unpinned OS packages follow Ubuntu's security updates. |
| `tools` | Optional entries with unique `name`, HTTPS `url`, and required `sha256`. Each installs one executable. |
| `tools[].member` | Exact regular-file member of a gzip tar archive; omit for a direct executable download. Symlinks and duplicate members are refused. |
| `checks` | Optional named checks with literal `argv`, `timeout_seconds` (1–300, default 30), and optional actionable `hint`. No shell expansion. |

Downloads go to `/opt/crux-run-tools/bin`, owned by root. Repeated installation
checks both the archive pin and the installed binary's digest before skipping;
a changed or damaged binary is replaced atomically. An invalid download leaves
the existing executable intact. Declare runtime libraries in `apt_packages` and
a version/help command in `checks` so a missing loader or library fails early.
Packages and archives are installed through the shared layer, so ACP drop-ins
can use them too; automatic launch checks below apply to SDK runs.

`configure-run.sh` sets `HOME=/home/ubuntu`, prepends the tools directory to `PATH`,
and creates a private, writable `XDG_CACHE_HOME=/home/ubuntu/.cache/crux-run`.
The SDK checks run as `ubuntu` with the service's `/etc/crux-run.env` and its
workspace directory. Provisioning checks them before the model probe. Each SDK
start checks them again before replacing the wrapper with the scaffold process.
A missing executable, failed check, or timeout exits 2 and prevents model calls;
systemd leaves it stopped for the operator rather than retrying that error.
Checks discard command output and report only their name and remediation hint.

Validate locally (no downloads, model calls, or cloud resources):

```bash
python3 src/ec2-workspaces/run_provision.py validate --manifest path/to/drop-in/provision.json
python3 -m unittest discover -s tests/workspace-provisioning -v
```

For an existing box, stop the run between turns before reprovisioning. The
provisioner stages the helper at `/usr/local/lib/crux/run_provision.py`; manual
software reinstall uses `sudo python3 /usr/local/lib/crux/run_provision.py install
--manifest /etc/crux-provision.json`. To diagnose the checks with the service's
actual user and environment:

```bash
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  --property=EnvironmentFile=/etc/crux-run.env \
  --working-directory=/srv/crux-run/run-harness/workspace \
  /usr/bin/python3 /usr/local/lib/crux/run_provision.py check \
  --manifest /etc/crux-provision.json
```
