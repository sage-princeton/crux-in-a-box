#!/usr/bin/env bash
# Opt-in URL filtering for an inactive SDK run. Never changes the host firewall outside this box.
set -euo pipefail

die() { echo "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "Run as root on the run box."
[ "$#" = 1 ] || die "Usage: install-network-proxy.sh /operator/path/network-policy.toml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY="$(realpath "$1")"
SERVICE=crux-sdk-run.service
[ "$(systemctl show -p LoadState --value "$SERVICE")" = loaded ] || die "Provision the SDK run first."
! systemctl is-active --quiet "$SERVICE" || die "Stop the SDK run between turns before changing network policy."
python3 "$SCRIPT_DIR/network_proxy.py" --check "$POLICY"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv nftables ca-certificates >/dev/null
python3 -m venv /opt/crux-network-proxy
/opt/crux-network-proxy/bin/pip install --quiet mitmproxy==12.3.1
for account in crux-agent crux-proxy; do
    id "$account" >/dev/null 2>&1 || useradd --system --user-group --create-home \
        --home-dir "/var/lib/$account" --shell /usr/sbin/nologin "$account"
done
RUN_UID="$(id -u crux-agent)"
install -d -m 700 -o crux-proxy -g crux-proxy /var/lib/crux-proxy
install -d -m 700 -o crux-agent -g crux-agent /var/lib/crux-agent /var/lib/crux-agent/cache
install -m 644 "$SCRIPT_DIR/network_proxy.py" /usr/local/lib/crux/network_proxy.py
install -m 640 -o root -g crux-proxy "$POLICY" /etc/crux-network.toml
/opt/crux-network-proxy/bin/python - <<'PY'
from mitmproxy.certs import CertStore
CertStore.from_store('/var/lib/crux-proxy', 'mitmproxy', 2048)
PY
chown -R crux-proxy:crux-proxy /var/lib/crux-proxy
install -m 644 /var/lib/crux-proxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/crux-network-proxy.crt
update-ca-certificates >/dev/null

# The entire replacement is one nft transaction; existing unrelated tables are preserved.
cat > /etc/crux-network.nft <<RULES
add table inet crux_network
delete table inet crux_network
table inet crux_network {
    chain output {
        type filter hook output priority -10; policy accept;
        meta skuid $RUN_UID ip daddr 127.0.0.1 tcp dport 18080 accept
        meta skuid $RUN_UID counter reject
    }
}
RULES
nft --check --file /etc/crux-network.nft
cat > /etc/systemd/system/crux-network-firewall.service <<'UNIT'
[Unit]
Description=CRUX agent egress through the policy proxy
Before=crux-sdk-run.service
[Service]
Type=oneshot
ExecStart=/usr/sbin/nft --file /etc/crux-network.nft
RemainAfterExit=yes
UNIT
cat > /etc/systemd/system/crux-network-proxy.service <<'UNIT'
[Unit]
Description=CRUX URL blacklist proxy
After=network-online.target
Wants=network-online.target
[Service]
User=crux-proxy
Group=crux-proxy
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ExecStart=/opt/crux-network-proxy/bin/mitmdump --listen-host 127.0.0.1 --listen-port 18080 --flow-detail 0 --set confdir=/var/lib/crux-proxy --set crux_network_policy=/etc/crux-network.toml --set ssl_verify_upstream_trusted_ca=/etc/ssl/certs/ca-certificates.crt --set rawtcp=false --set connection_strategy=lazy --set termlog_verbosity=warn --scripts /usr/local/lib/crux/network_proxy.py
Restart=on-failure
RestartSec=2
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/crux-proxy
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/crux-network-run.env <<'ENV'
HOME=/var/lib/crux-agent
XDG_CACHE_HOME=/var/lib/crux-agent/cache
HTTP_PROXY=http://127.0.0.1:18080
HTTPS_PROXY=http://127.0.0.1:18080
http_proxy=http://127.0.0.1:18080
https_proxy=http://127.0.0.1:18080
ALL_PROXY=http://127.0.0.1:18080
all_proxy=http://127.0.0.1:18080
NO_PROXY=
no_proxy=
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
CODEX_CA_CERTIFICATE=/etc/ssl/certs/ca-certificates.crt
ENV
chmod 600 /etc/crux-network-run.env
install -d /etc/systemd/system/crux-sdk-run.service.d
cat > /etc/systemd/system/crux-sdk-run.service.d/zz-network.conf <<'UNIT'
[Unit]
Requires=crux-network-firewall.service
Wants=crux-network-proxy.service
After=crux-network-firewall.service crux-network-proxy.service
[Service]
User=crux-agent
Group=crux-agent
EnvironmentFile=/etc/crux-network-run.env
ExecStartPre=/usr/bin/curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 --retry-max-time 15 --max-time 2 --proxy http://127.0.0.1:18080 http://crux-proxy.invalid/healthz
NoNewPrivileges=yes
CapabilityBoundingSet=
RestrictSUIDSGID=yes
InaccessiblePaths=-/source -/home/ubuntu -/root
UNIT
chown -R crux-agent:crux-agent /srv/crux-run/run-harness/workspace /srv/crux-run/state
systemctl daemon-reload
systemctl restart crux-network-firewall.service crux-network-proxy.service
systemctl enable crux-network-proxy.service >/dev/null
systemctl is-active --quiet crux-network-firewall.service crux-network-proxy.service
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
    --retry-max-time 15 --max-time 2 --proxy http://127.0.0.1:18080 http://crux-proxy.invalid/healthz
echo "Network policy installed. SDK run remains stopped; launch it normally."
