# Optional paper URL blacklist

Install an HTTPS inspection proxy around an SDK run when a task must avoid
specific paper locations. Other HTTP(S) destinations remain available.
This is an explicit operator step after ordinary provisioning and before launch.
An ordinary run has no proxy or firewall changes.

The operator supplies a separate TOML file, never staged into the agent workspace:

```toml
[network]
blacklist = [
    '(?i)OpenReviewResourceId',
    'example\.org/papers/held-out-paper(?:[/?#]|$)',
]
```

Each entry is a Python regular expression matched against the complete request
URL after repeated percent-decoding (at most eight rounds). Matching is case
sensitive unless the expression supplies `(?i)`. Use stable resource IDs to
cover PDF, HTML, API and attachment routes together. An empty list permits all
HTTP(S) URLs through the proxy. Missing or malformed policy stops proxy startup;
an evaluation error rejects the request. Request and response bodies are not
searched. A match returns HTTP 403 with `Blocked by run network policy.`

The [inductive forecasting policy](network-policies/inductive-forecasting.toml)
contains the operator's OpenReview IDs and PDF hashes, NeurIPS page, author's
website and anonymous code repository (including API, archive and child routes).
It leaves title searches, other papers and mirrors with different identifiers
accessible so a discovery run can identify gaps.

## Install on the run box

Copy `install-network-proxy.sh`, `network_proxy.py` and the chosen policy to
operator-controlled locations on the box, then run:

```sh
sudo bash /operator/install-network-proxy.sh /operator/network-policy.toml
sudo systemctl start crux-sdk-run
```

The installer refuses to modify an active run. It installs mitmproxy 12.3.1,
creates separate `crux-agent` and `crux-proxy` accounts, and installs:

- `/etc/crux-network.toml`: root-owned policy, readable only by the proxy group.
- `crux-network-proxy.service`: HTTP proxy on `127.0.0.1:18080` with HTTPS inspection.
- `crux-network-firewall.service`: an `nftables` table rejecting all agent-account
  IP traffic except connections to that proxy, including IPv6 and direct DNS.
- An SDK service override selecting `crux-agent`, proxy and CA environment,
  no elevated privileges, and no access to the operator's home or local `/source`.

The agent's workspace and state ownership move to `crux-agent`. The operator
account and other services keep their existing networking. Only the dedicated
`crux_network` firewall table is managed. The proxy has a private CA key; its
public certificate is added to the **run box's** trust store. The proxy verifies
upstream certificates. No macOS trust settings change.

The SDK service waits for the firewall and checks that the proxy is active.
Direct egress stays blocked if the proxy subsequently fails. A proxy restart
does not intentionally stop or restart the SDK run. The firewall is restored
before the SDK service starts after a reboot.

## Observe and change the policy

```sh
sudo journalctl -u crux-network-proxy --since '10 minutes ago'
sudo nft list table inet crux_network
sudo -u crux-agent curl --proxy http://127.0.0.1:18080 https://example.com/
sudo -u crux-agent curl --noproxy '*' --connect-timeout 5 https://example.com/
```

The proxied request should succeed; the direct request should fail. Check a
blocked URL through the proxy and verify the policy's 403 response. Denial logs
contain the one-based rule number and hostname, without URL queries, headers or
bodies. Flow dumping is disabled. Preserve sanitized evidence outside source
checkouts.

To update the list, stop the run between turns, edit the operator's TOML and
rerun the installer. Reapply after ordinary reprovisioning, which restores
workspace ownership and the base service configuration. This first installer
targets the SDK service; ACP account/configuration migration is not implemented.
The SDK's main shell is covered. Native worker sandboxes and MCP clients that
do not inherit proxy/trust settings may fail to connect and require explicit
transport configuration; they do not gain direct egress.

To restore ordinary SDK execution, stop the run between turns, remove
`/etc/systemd/system/crux-sdk-run.service.d/zz-network.conf`, restore workspace
and state ownership to `ubuntu:ubuntu`, and reload systemd. Then stop/disable
`crux-network-proxy`, stop `crux-network-firewall`, and delete only the
`inet crux_network` nftables table. Remove
`/usr/local/share/ca-certificates/crux-network-proxy.crt` and run
`update-ca-certificates` to restore the box's earlier trust store. Leave the SDK
stopped until the operator has checked its restored configuration. Discarding a
disposable test box also removes these changes.

## What this establishes

This blocks configured URL locations, including embedded URLs preserving their
identifiers. It does not establish zero contamination. Search snippets, POST
search bodies, GitHub GraphQL, remote browser/fetch services, unknown mirrors,
previously downloaded files and model training knowledge need separate policies.
The initial live search is intended to measure these gaps before expanding rules.

Before using a revised policy, test allowed/blocked requests, encoded resource
IDs, direct connections, agent privileges, proxy failure and the actual model
and telemetry paths. Verify separately on EC2 before treating local OrbStack
results as production validation.
