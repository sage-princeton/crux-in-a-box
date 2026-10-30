# Run software declarations

`DROP_IN_PATH` stages the committed drop-in, including its workspace. Optional
`WORKSPACE_DIR` copies a local working directory over that workspace, including
prepared research inputs kept outside Git. Relative paths resolve from the
repository root. Git metadata, virtual environments and Python caches are omitted.
Keep credentials and raw sessions outside the selected directory. Without this
setting, provisioning stages only the committed workspace.

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

## Manual gog authentication

Provisioning installs `gog`; the operator authorizes the mailbox before starting
`crux-sdk-run`. Keep manifest `checks` limited to installation checks, since they
also run during provisioning, before this manual setup.

Enable the Gmail API and create a **Desktop app** OAuth client in Google Cloud.
Securely copy its client JSON to the box outside the checkout (mode 600).
For long runs, avoid Google's seven-day refresh-token expiry for apps in Testing;
see the [pinned upstream setup guide](https://github.com/openclaw/gogcli/blob/v0.28.0/README.md#avoid-the-seven-day-oauth-expiry).

SSH into the box **as `ubuntu`**, with the run stopped, then authorize:

```bash
umask 077
export PATH="/opt/crux-run-tools/bin:$PATH"
export GOG_HOME="$HOME/.config/crux-gog" GOG_KEYRING_BACKEND=file
export GOG_ACCOUNT='review-mailbox@example.com' # replace with your mailbox
read -rsp 'Choose a gog keyring password: ' GOG_KEYRING_PASSWORD
printf '\n'
export GOG_KEYRING_PASSWORD
install -d -m 700 "$GOG_HOME"
gog auth credentials /secure/client_secret.json # your copied JSON
gog auth add "$GOG_ACCOUNT" --services gmail --manual
```

Open the printed consent URL in your local browser and follow gog's instructions
to paste the resulting redirect URL back into SSH. The client JSON alone does
not grant mailbox access; this consent stores the refresh token.

Shell exports do not reach systemd. Create a separate root-only environment file
(`sudo install -m 600 /dev/null /etc/crux-gog.env`, then
`sudoedit /etc/crux-gog.env`) containing the same values:

```ini
GOG_HOME=/home/ubuntu/.config/crux-gog
GOG_KEYRING_BACKEND=file
GOG_ACCOUNT=review-mailbox@example.com
GOG_KEYRING_PASSWORD="the password chosen above"
```

Keep this file, the client JSON, and gog's state out of git and logs. Do not add
these values to `/etc/crux-run.env`, which reprovisioning overwrites. Install a
systemd drop-in to load the separate file and keep refreshed state private:

```bash
sudo install -d /etc/systemd/system/crux-sdk-run.service.d
sudo tee /etc/systemd/system/crux-sdk-run.service.d/gog.conf >/dev/null <<'EOF'
[Service]
EnvironmentFile=/etc/crux-gog.env
UMask=0077
EOF
sudo systemctl daemon-reload
```

After the remaining prelaunch steps, check read-only Gmail access as the run user
with both environment files and the workspace directory; start only on success:

```bash
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  --property=EnvironmentFile=/etc/crux-run.env \
  --property=EnvironmentFile=/etc/crux-gog.env \
  --property=UMask=0077 \
  --working-directory=/srv/crux-run/run-harness/workspace \
  /opt/crux-run-tools/bin/gog --no-input --json gmail labels list >/dev/null &&
  sudo systemctl start crux-sdk-run
```

Expose gog through a drop-in `command` tool and include it in the intended
agent's `tools` list, for example:

```toml
[tools.review_mailbox]
type = "command"
description = "List the review mailbox's Gmail labels."
command = "gog --no-input --json gmail labels list"
timeout_seconds = 30
```

Command tools inherit the service environment; MCP servers need explicit
`GOG_*` environment mappings. Codex shells have separate secret filtering and
sandbox rules, so use the command/MCP tool for mailbox access. If access fails,
leave the run stopped, repeat authorization as `ubuntu` with the same home and
keyring password, and rerun the access check. Reprovisioning preserves this
manual state and service drop-in; a replacement box needs setup again.
