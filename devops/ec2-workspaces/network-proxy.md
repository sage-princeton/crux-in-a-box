# Optional paper URL and search-result blacklist

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
an evaluation error rejects the request. A match returns HTTP 403 with
`Blocked by run network policy.`

Search results carry a blocked paper's title, snippet and URL even when every
direct fetch is blocked. Two optional keys check response bodies too:

```toml
inspect_hosts = ["bing.com", "duckduckgo.com", "github.com"]
content_blacklist = ['(?i)exact\s+title\s+of\s+the\s+paper']
```

A response from a listed domain or any of its subdomains is held until complete
and checked against every `blacklist` rule plus every `content_blacklist` rule; a
match replaces it with the same 403. The check covers the percent-decoded body,
its text without tags or HTML entities (search engines highlight query words
inside result titles), and the targets of Bing's encoded result links. Only text
responses up to 10 MB are held; event streams, binary files and larger bodies from
those hosts pass unchecked. Keep content rules to identifiers such as exact titles
and repository names: a results page repeats the query, so a phrase the agents
might search for would block their own searches.

Responses from other hosts are never held. mitmproxy otherwise holds every
response until it is complete, which would deliver model event streams only when
they finish and keep large downloads in memory.

The [inductive forecasting policy](network-policies/inductive-forecasting.toml)
contains the operator's OpenReview IDs and PDF hashes, NeurIPS page, author's
website and the anonymous and GitHub code repositories (including API, archive
and child routes). It checks responses from the main search engines, GitHub,
OpenReview, the NeurIPS site and the scholarly indexes for those identifiers and
the paper's title. Model, telemetry, Slack, RunPod and package hosts are not
inspected.

## Install on the run box

Copy `install-network-proxy.sh`, `network_proxy.py` and the chosen policy to
operator-controlled locations on the box, then run:

```sh
sudo bash /operator/install-network-proxy.sh [--allow-direct-tcp] /operator/network-policy.toml
sudo systemctl start crux-sdk-run
```

Add `--allow-direct-tcp` when the agents drive GPU pods over SSH or rsync. It lets
the agent account open TCP connections on ports other than 80 and 443 and resolve
names through the box's DNS resolver. Web traffic on ports 80 and 443 still has to
go through the proxy. A GPU pod has unfiltered internet access, so with this flag
the blacklist guards against accidental fetches from the run box, not against an
agent that deliberately reaches a blocked page another way.

The installer refuses to modify an active run. It installs mitmproxy 12.3.1,
creates separate `crux-agent` and `crux-proxy` accounts, and installs:

- `/etc/crux-network.toml`: root-owned policy, readable only by the proxy group.
- `crux-network-proxy.service`: HTTP proxy on `127.0.0.1:18080` with HTTPS inspection.
- `crux-network-firewall.service`: an `nftables` table rejecting all agent-account
  IP traffic except connections to that proxy, including IPv6 and direct DNS.
  With `--allow-direct-tcp` it also accepts the agent account's DNS queries and its
  TCP on ports other than 80 and 443.
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
blocked URL through the proxy and verify the policy's 403 response. With
`--allow-direct-tcp`, also check that
`sudo -u crux-agent timeout 5 bash -c '</dev/tcp/github.com/22' && echo direct TCP ok`
succeeds. Denial logs
contain the one-based rule number and hostname, without URL queries, headers or
bodies. Flow dumping is disabled. Preserve sanitized evidence outside source
checkouts.

To update the list, stop the run between turns, edit the operator's TOML and
rerun the installer. Reapply after ordinary reprovisioning, which restores
workspace ownership and the base service configuration. This first installer
targets the SDK service; ACP account/configuration migration is not implemented.
The SDK's main shell and the bundled Slack MCP configurations use the policy proxy.
The Slack declaration forwards the web proxy and Node CA settings for the initial
`npx` download. The installed server uses `SLACK_MCP_PROXY` for Slack API calls
and trusts the box's CA store. Other MCP clients and native worker sandboxes
may require their own transport configuration.

For an already staged run with the older SDK and installer, stop the SDK service
and add these entries to the existing `[mcp_servers.slack]` inline `env` table in
`/srv/crux-run/run-harness/scaffold.toml`, keeping the bot and channel entries:

```toml
HTTPS_PROXY = "${HTTPS_PROXY}", NODE_EXTRA_CA_CERTS = "${NODE_EXTRA_CA_CERTS}", SLACK_MCP_PROXY = "${HTTPS_PROXY}"
```

This uses the original policy installer's environment. Apply these required
references only on a proxy-enabled run. Start the service with its existing
state directory to resume.

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
identifiers, and search results from inspected hosts that show those identifiers
or a content rule. It does not establish zero contamination. Search engines that
are not listed, results that paraphrase the paper without an identifier, remote
browser/fetch services, unknown mirrors, traffic from GPU pods and web-search
models, previously downloaded files and model training knowledge remain outside
it.

Before using a revised policy, test allowed/blocked requests, encoded resource
IDs, direct connections, agent privileges, proxy failure and the actual model
and telemetry paths. Verify separately on EC2 before treating local OrbStack
results as production validation.
