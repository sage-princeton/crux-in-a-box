# Run software and service declarations

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
| `services` | Optional named services with `environment`, `required_env`, and a required `check` (`argv`, timeout and hint as above). |

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
  --property=EnvironmentFile=/etc/crux-run.env --property=UMask=0077 \
  --working-directory=/srv/crux-run/run-harness/workspace \
  /usr/bin/python3 /usr/local/lib/crux/run_provision.py check \
  --manifest /etc/crux-provision.json
```

## Service authentication

The example opts into an operator-provisioned review Gmail mailbox. The CLI
version check proves installation; `gog --no-input --json gmail labels list`
performs an authenticated, read-only Gmail request, including for an empty
mailbox. Missing credentials, an expired/revoked login, or network failure block
SDK launch with the service's name and remediation hint. Neither probe output
nor credential contents reach provisioner logs. No account is created.

Each service's `environment` contains nonsecret literals; `{service_dir}` expands
to `/home/ubuntu/.config/crux-services/<name>`. Required secret environment values
use the existing per-run secrets JSON. Environment keys must be unique and cannot
override the run's managed variables or another secret. A service's optional
`SERVICE_AUTH.<name>` entry is a base64-encoded gzip tar containing its CLI state
and configuration. It is transported in the per-run secrets file, never exported
as an environment variable. Bundles for undeclared services fail preflight.
Services using only environment credentials need no bundle.

For Gmail, first export `GOG_ACCOUNT` and `GOG_KEYRING_PASSWORD` in your operator
shell and authorize the dedicated mailbox with the existing bootstrap utility.
Keep the OAuth client, bundle, and secrets JSON outside the repo:

```bash
utils/bootstrap-gog.sh /secure/client.json "$GOG_ACCOUNT" /secure/review-mailbox.tar.gz
python3 - /secure/run-secrets.json /secure/review-mailbox.tar.gz <<'PY'
import base64, json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
values = json.loads(path.read_text())  # existing provider key and run secrets
values.update({key: os.environ[key] for key in ("GOG_ACCOUNT", "GOG_KEYRING_PASSWORD")})
values.setdefault("SERVICE_AUTH", {})["review-mailbox"] = base64.b64encode(
    pathlib.Path(sys.argv[2]).read_bytes()).decode()
with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600), "w") as output:
    os.fchmod(output.fileno(), 0o600)
    json.dump(values, output)
PY
```

Copy `provision.json.example` into your selected drop-in as `provision.json`,
commit it, then provision with `--base-secrets /secure/run-secrets.json` (or
`--secrets` for `provision-workspace-aws-resources.sh`). Software is installed
before secrets arrive. SCP delivers secrets into a mode-700 directory; configure
deletes that transport file after reading it and seeds only regular auth files
into the service directory (files 600, directories 700, owned by the run user).
The SDK service and probes use umask 0077 for newly created authentication state.
Archive paths cannot escape that directory; links and special files are refused.
Bundles are bounded to 8 MiB compressed, 32 MiB expanded, and 2,048 entries.

Repeated configuration preserves existing files, including rotated refresh
tokens, and repairs private permissions. Recreate the base environment via
`configure-run.sh` before calling the configure helper again. To replace expired
auth, stop the run between turns, reauthorize the mailbox, remove only that
service's state directory on the box, and reprovision with the new bundle and
matching keyring password. Removing a declaration stops provisioning/checking
that service; it does not revoke credentials or delete its files.

Give the intended agent access through the existing `command` tool or an MCP
adapter. For example, expose this fixed read-only command and list `review_mailbox`
in the intended agent's `tools` in `scaffold.toml`:

```toml
[tools.review_mailbox]
type = "command"
description = "Check the review mailbox's Gmail labels."
command = "gog --no-input --json gmail labels list"
timeout_seconds = 30
```

Command tools inherit the service environment. An MCP adapter must explicitly
map the variables it needs (for example `GOG_HOME = "${GOG_HOME}"`,
`GOG_KEYRING_BACKEND = "${GOG_KEYRING_BACKEND}"`, `GOG_ACCOUNT = "${GOG_ACCOUNT}"`,
and `GOG_KEYRING_PASSWORD = "${GOG_KEYRING_PASSWORD}"` in its `env` table). Codex
shells apply separate secret filtering and sandbox rules; use the command/MCP
adapter for this example. Provisioning does not change coding-agent permissions.
