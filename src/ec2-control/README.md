# Controller provisioning

Run `make-control-box.sh` locally to provision or update the AgentRQ controller.
It creates the instance, key pair, security groups, IAM role, Elastic IP and
persistent data volume. It also adds an SSH alias and runs `configure-control.sh`.
Use `--dry-run` to inspect the plan.

## Network access

`OPERATOR_CIDR` controls SSH access. Each entry must be a `/32` address.
`TLS_INGRESS_CIDR` controls HTTPS access and accepts address ranges.
Both settings accept comma-separated entries and optional labels:

```ini
OPERATOR_CIDR=203.0.113.10/32=andrew,198.51.100.7/32=alice
```

Labels appear in AWS security-group rule descriptions.
Caddy serves HTTPS with a Let's Encrypt certificate. Port 80 must remain
publicly accessible for HTTP-01 certificate validation and renewal.

AgentRQ uses `AGENTRQ_DOMAIN` for host routing and authentication cookies.
Workspace instances resolve this hostname to the controller's private IP
through `/etc/hosts`, preserving TLS validation and private network access.
