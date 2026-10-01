# Incident app security review — 1 October 2026

## 1. Scope and result

Reviewed the Python monitoring service and incident website in PR #21, including
SAML authentication, sessions, status mutations, public/private rendering,
deployment commands, runtime dependencies and container operating-system packages.
This is a source review and automated scan, not a penetration-test certification.

Local validation used Trivy 0.75.0 with the vulnerability database updated at
2026-10-01 19:00 UTC, Bandit 1.9.4, both Linux/amd64 images, and 45 passing Python
tests. No medium/high Bandit findings or source secrets were detected. Both
patched images have zero **fixable high/critical** vulnerability findings.
Unfixed vulnerabilities and infrastructure hardening findings remain; the images
are not vulnerability-free.

## 2. Findings remediated

| Finding | Severity / impact | Change and verification |
| --- | --- | --- |
| PCRE2 `CVE-2026-103111` and OpenSSL `CVE-2026-75804`, `CVE-2026-84782` in the pinned Debian image | Scanner high; applicability depends on use of the affected library functionality | Apply Debian package updates during both image builds; rescan installed packages. |
| Flask `CVE-2026-27205` | Scanner low, session-cache information disclosure; the app also sets `Cache-Control: no-store` | Upgrade Flask 3.1.2 to 3.1.3; signed SAML and web behavior tests pass. |
| Old pip plus vulnerable dependencies bundled inside the latest pip | Scanner medium/high, primarily package-installation paths | Use pip 26.2.1 during the build, then remove pip and the ensurepip bootstrap bundle from runtime images. Application dependencies remain installed. Verify both runtime entrypoints still work and installer imports are absent. No scanner exclusions hide these packages. |
| Non-ASCII `RelayState` or CSRF values raise `TypeError` in `compare_digest` | Low; individual malformed requests produce 500 errors, with no demonstrated authentication bypass | Reject non-ASCII and oversized security tokens with 403. Regression test reproduced the original error and now passes; valid signed SAML still works. |
| Deployment page-content verification used `assert` | Low; Python optimization can remove the check | Use an explicit conditional and `RuntimeError` so verification remains active under optimization. |

## 3. Remaining findings and operating limits

Each patched image has 44 high, 57 medium, 61 low and 2 unknown package/CVE
occurrences at scan time. The 44 high occurrences represent **8 unique Debian
CVEs**, all without a distribution fix in this database:

- util-linux: `CVE-2026-76642`, `CVE-2026-78408`, `CVE-2026-78409`, `CVE-2026-78410`.
- acl: `CVE-2026-54369`.
- ncurses: `CVE-2025-69720`.
- systemd: `CVE-2026-16742`.
- perl/Archive-Tar: `CVE-2026-9538`.

These are component-presence findings, not proof of reachable remote exploits.
Both applications run as UID 10001; this review does not establish that every
affected OS-library path is unreachable. Rebuild and rescan when Debian publishes
fixes. No blanket CVE suppression file was added.

Python runtime dependencies have one remaining low finding:
Paramiko 4.0.0 `CVE-2026-44405`, with no fix reported. Production SFTP pins host
keys and uses `RejectPolicy`; the web app bundles the shared dependency but does
not expose SSH functionality.

Configuration scanning identifies additional hardening opportunities:

| Area | Scanner result / assessment | Follow-up |
| --- | --- | --- |
| HTTPS egress to `0.0.0.0/0` | Critical scanner classification; worker/web rules restrict the port to TCP 443, but allow arbitrary HTTPS destinations | Consider an egress proxy or endpoint allowlist compatible with AWS APIs, the IdP and model provider. This is a network-design change, not an authentication bypass found in the app. |
| Public subnet addressing | High; public web hosting and worker internet access are intentional in the current deployment | Consider private workers and controlled outbound access. |
| S3 uses AES256 instead of a customer-managed key | High scanner policy finding; objects are encrypted at rest, and the public bucket policy permits only the legacy incident-link object | Consider customer-managed keys if required by the organization's key-control policy. |
| VPC flow logs / S3 access logs | Medium/low | Add traffic/access audit collection with an explicit retention policy. |
| AWS-managed encryption keys / Docker HEALTHCHECK | Low | Evaluate CMK requirements; current deployment uses application HTTPS health verification instead of Docker HEALTHCHECK. |
| Public login endpoint has no application rate limiter | Manual review, medium availability/cost risk | Add shared rate limiting or an edge control before wider public exposure; request-size limits, worker timeouts and expiring login records are not rate limiting. |
| Revoked operator assignments can retain an existing session for up to one hour | Documented session policy | Delete the user's server-side session records when immediate revocation is required. |

Terraform scan results depend on variable values and do not replace a live AWS
policy audit. The source scan excludes test-only SSH fixtures, whose root user is
needed to test the SSH boundary. Generated state/plan files were excluded from
the local source snapshot; CI scans a clean checkout.

## 4. Application controls reviewed

| Area | Evidence and limits |
| --- | --- |
| Authentication and replay | Strict SAML validation, configured issuer/certificate, signed assertions, audience/recipient checks, request ID and browser-bound relay nonce. Pending login consumption and session creation share a conditional DynamoDB transaction. Signed-response, tampering and replay tests pass. |
| Authorization and CSRF | Public routes render explicit summary/history fields. Private observations and actor/note details require a valid server-side session. Status and logout POSTs require exact Origin and session-bound CSRF tokens; missing, foreign and `null` origins fail. All HAL members intentionally share operator permissions. |
| Sessions and cryptography | Random session tokens are hashed for database lookup; cookies use `__Host-`, Secure and HttpOnly. Sessions expire in at most one hour. Only the short-lived SAML binding cookie uses SameSite=None. Runtime secrets remain in SSM SecureStrings. |
| Injection and XSS | DynamoDB SDK expressions avoid concatenating user inputs into query syntax. Jinja autoescaping remains enabled; no untrusted `safe` rendering. CSP denies scripts and framing. The CI subprocess wrapper uses argument arrays without a shell; its two low Bandit findings were manually reviewed. |
| Input and failure handling | Request bodies are capped at 128 KiB; incident IDs use UUID routes; statuses and note lengths are validated. Conditional version checks prevent stale writes. SAML errors log categories rather than assertions, identities or tokens. |
| SSRF and evidence access | Web routes do not fetch user-supplied URLs. IdP and origin configuration are administrator-controlled. Evidence links target the AWS S3 console and require separate AWS access. Production SFTP checks pinned host keys, normalized paths, regular-file type and symlink traversal. |
| Supply chain and deployment | Actions are pinned by commit. Images use a pinned base and immutable release digests. The exact ECR digest is scanned before Terraform applies; failed scans abort deployment. Dependency resolution is still performed at build time, so the digest scan is essential. |

## 5. Continuous checks

The Monitoring checks workflow installs pinned Trivy 0.75.0 and Bandit 1.9.4.
It scans source secrets, Terraform/Docker configuration and both complete runtime
images. Medium/high Bandit findings, detected source secrets, scanner execution
errors and fixable high/critical image vulnerabilities fail the job. Terraform
misconfigurations and lower-severity/unfixed vulnerabilities remain reportable
findings rather than being silently ignored or automatically treated as accepted.

Full JSON reports, including all vulnerability severities, are retained for
14 days as `monitoring-security-reports`. Deployment repeats the image gate on
the exact ECR digests before infrastructure updates and retains
`deployed-image-security-reports`. Artifact upload runs after scan failures too.

Local gate validation rejected the old web image with seven fixable high/critical
package/CVE occurrences and accepted both patched images. Actionlint, ShellCheck,
the repository secret scanner and all 45 Python tests passed.

The initial GitHub verification is tracked in
[run 36935157908](https://github.com/sage-princeton/crux-in-a-box/actions/runs/36935157908).

## 6. Priorities

1. Keep the image gates active; rebuild when distribution fixes become available.
2. Add login rate limiting and decide the acceptable public-service exposure.
3. Review egress restrictions, audit-log retention and customer-managed-key needs
   with the infrastructure owner before changing the live network or encryption.
