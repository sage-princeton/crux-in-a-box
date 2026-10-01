# CRUX incident management — implementation and verification report

Date: 1 October 2026

Status: Deployed and accepted — public access, real AWS sign-in, close/reopen and CI release verified

Decision addressed: Whether the public incident log can support authenticated closure and reopening, durable incident identity, and repeatable deployment.

## 1. Result and acceptance status

The public incident service is available at [the CRUX incident log](https://34-193-109-221.sslip.io). It runs Flask and Jinja on a dedicated EC2 instance, with DynamoDB as the authoritative incident store. Public visitors can read open and closed incidents. Assigned AWS Identity Center users can manage incident status after signing in.

The implementation remains on [PR #21](https://github.com/sage-princeton/crux-in-a-box/pull/21). It has not been merged. The CI pipeline has completed a production deployment. The operator completed a real authenticated close/reopen cycle. Independent public-history checks confirmed the saved transitions and unchanged incident identity.

This report uses numbered sections, explicit scope and acceptance evidence. It does not claim conformity to an ISO standard.

## 2. Scope and delivered behaviour

### 2.1 Incident identity and state

Each incident has a permanent UUID derived from the workload, detector and source-event identity. The reviewer's explanation does not participate in identity. Repeated observations of the same event accumulate under the same incident; changing the wording does not reopen it or create another incident.

Reviews and observations are stored separately from mutable incident status. Stored provenance includes model identifiers, prompt digest, detector version and references to private S3 evidence. Closing and reopening use conditional version checks and an atomic audit event. A stale form is rejected rather than overwriting a newer decision. Reopening changes the open count without increasing the cumulative number of discovered incidents.

The historical import processed 231 saved reviews into 15 incidents across 14 workloads. Two known resolutions were retained as closed incidents. The import also established the Slack discovery baseline, so historical records were not announced as new incidents. Imported records without a unique primary event retain an explicit legacy source-set identity; the migration does not invent event precision absent from the original evidence.

### 2.2 Public information and operator access

Public pages expose a defined field allowlist: incident ID, instance ID and workload label, fixed detector title and summary, severity, confidence, status, timestamps, review and observation counts, and incident kind. Raw findings, evidence references, operator identities and private notes require authentication. Raw S3 evidence additionally requires the viewer's AWS permissions.

The interface follows the supplied dashboard references: light gray canvas, white summary cards, blue actions and links, status tabs, and compact bordered incident tables grouped by instance ID. Desktop cards stack on mobile, and the table scrolls within its own region. Detail pages use matching white panels. Summary counts identify their scope explicitly.

The public list offers Open, Closed and All views. It retains historical incidents and groups stopped workloads behind a disclosure. Controller, monitoring worker and incident web infrastructure remain excluded from the monitored workload list.

AWS Identity Center supplies signed SAML assertions. The application checks issuer, audience, recipient, request binding and assertion validity. Login requests and sessions are stored server-side with expiry; session cookies are secure and HTTP-only. State changes require an authenticated operator, matching request origin, CSRF token and current incident version. There is no shared password or AWS access-key form.

### 2.3 Deployment and secret handling

GitHub Actions runs monitoring behaviour tests, real read-only SFTP checks, container builds and Terraform validation. Explicit deployment runs proceed only after those checks pass. The deployment environment is restricted to `main` and the current review branch; deployments are serialized.

CI authenticates to AWS through GitHub OIDC. The role trust matches the repository's immutable owner/repository IDs and the `crux-monitoring` environment. GitHub stores deployment variables rather than long-lived AWS credentials. Runtime credentials remain in dedicated SSM SecureStrings, read by the worker or web instance role. The pipeline does not retrieve their contents.

Terraform state was migrated to the private, versioned state-account S3 bucket. All 49 pre-migration resource records were compared and retained unchanged. The backend uses KMS encryption and S3 lockfiles. Ordinary CI releases permit updates to existing application release resources; creation, replacement and unrelated infrastructure changes require a separately reviewed operator apply.

The release pipeline builds immutable worker and web images, applies the shared Terraform state, updates the web service through SSM, and verifies the public HTTPS endpoint reports the deployed commit. Updating the host's boot script preserves the selected image across reboot. No SSH ingress is required for the web service.

## 3. Deployed architecture

| Component | Responsibility | Location |
| --- | --- | --- |
| AWS Batch monitoring workers | Read approved evidence, create reviews and observations, publish the fleet digest | CRUX account `881004720495`, `us-east-1` |
| Incident web EC2 | Public HTML, SAML callback, authenticated status changes | `crux-incident-web`, `i-02d24c35065046688` |
| Incident DynamoDB table | Incident state, review/observation records, audit history and expiring login/session records | `crux-monitoring-ae211-incidents` |
| Monitoring DynamoDB table | Existing scheduling, leases, inference reservations and delivery checkpoints | `crux-monitoring-ae211` |
| Evidence S3 bucket | Private reports, Markdown summaries, evidence and non-secret deployment inputs | `crux-monitoring-ae211-881004720495-us-east-1` |
| AWS Identity Center | Operator identity and application assignment | Organization account `805370850700`; Andrew assigned |
| Terraform backend | Versioned infrastructure state and deployment locking | State account `869937524494`, `crux/monitoring/terraform.tfstate` |
| GitHub environment | Restricted OIDC deployment identity and release coordination | `crux-monitoring` |

The older public S3 incident URL links to the live service. It no longer publishes the mutable incident state as a standalone HTML snapshot.

## 4. Verification evidence

The final code revision `5508f37` passed all 44 Python tests and 5 Terraform tests. Native browser close, reopen and logout passed against a local HTTPS fixture. The operator then confirmed sign-in and status changes online, and independent public-history requests verified the result.

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Public HTTPS service is available | Live health and index requests; deployed revision returned | Passed |
| Both open and closed incidents are public | Compared public listings against DynamoDB: 13 open and 2 closed after import | Passed |
| Unauthenticated users cannot change state | Live status POST rejected with HTTP 401 | Passed |
| Invalid SAML is rejected | Live invalid callback rejected with HTTP 403; local signed assertion, tamper and replay checks | Passed |
| Private deployment data remains private | Anonymous S3 request for deployment configuration rejected with HTTP 403 | Passed |
| Historical identity and resolutions survive migration | Idempotent import checks; imported counts and retained closed states verified | Passed |
| Rewording, repeated reviews and reopening preserve identity | Behaviour tests exercise closure, reworded later review, stale update rejection and reopening | Passed locally |
| Real AWS sign-in and close/reopen work online | Operator confirmed success; independent public history shows close → reopen → close for `59a7d248-f92b-5ec8-b710-b00183498ce1`, with 15 total incidents retained | Passed |
| CI deploys a tested revision using OIDC | [Production deployment 36932901053](https://github.com/sage-princeton/crux-in-a-box/actions/runs/36932901053) completed tests, Terraform and deployment with public revision verification | Passed |
| Public layout preserves historical workloads | Final deployed dashboard checked at 1440 px and 390 px; running instances first, three historical groups collapsed, aligned table columns and no document overflow. Empty filter and distinct-instance grouping covered | Passed |
| Infrastructure is valid | Five Terraform tests passed; formatting and validation passed | Passed |

### 4.1 Live state-transition proof

On 1 October 2026, [incident `59a7d248`](https://34-193-109-221.sslip.io/incidents/59a7d248-f92b-5ec8-b710-b00183498ce1) recorded **Closed at 18:06 ET → Reopened at 18:06 ET → Closed at 18:07 ET**. An unauthenticated read of the public history independently confirmed all three events after the operator reported success. The final state is closed; the ID and total count of 15 incidents are unchanged. Public filters then showed 12 open and 3 closed incidents. This verifies a real authenticated browser mutation, persisted audit history and public visibility, separately from local fixtures.

### 4.2 SAML interoperability correction

The failed AWS login returned an empty `AttributeStatement`, which strict SAML schema validation rejected. A signed local fixture reproduced the HTTP 403 and schema error. The Identity Center application now maps both Subject (emailAddress) and `email` (basic) to the user email. AWS confirmed the mapping update. Regression coverage accepts a populated statement and rejects an empty one while preserving tamper and replay checks. No assertion payload or session token is retained in this report. The operator confirmed that AWS sign-in now succeeds.

### 4.3 Native form origin correction

The initial `Referrer-Policy: no-referrer` header caused native browser form submissions to send `Origin: null`, which the strict origin check correctly rejected. A local HTTPS browser fixture reproduced the 403, then successfully closed and reopened the same incident with `Referrer-Policy: same-origin`. This preserves the origin for local forms and omits referrers to other sites; see [MDN’s documented effect on Origin](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referrer-Policy#effect_on_the_origin_header). Origin and CSRF checks remain enforced, including rejection of null and foreign origins.

## 5. Operating limits and follow-up

The monitoring schedule retains its approved end time of **2 October 2026, 19:33:56 UTC**. CI does not extend that window. Monitoring remains passive: it reports findings and does not block agent commands.

The web service currently uses one EC2 instance. An application release can briefly interrupt access while the service restarts. DynamoDB has point-in-time recovery and deletion protection; incident and audit records have no automatic expiry. Login/session records expire separately, and private S3 evidence retains its existing retention policy.

Incident closure records an operator decision; it does not certify that a workload is safe or restore missing telemetry. Monitoring health remains visible separately. Correlation version 1 treats a changed exported file snapshot as a new source revision; more semantic cross-event grouping is a future design decision.

Langfuse access retains the explicitly accepted read/write-key risk. Worker code uses bounded reads, but the credential itself can write. **TODO: create a way to read-only access LangFuse.**

When the review branch is retired, remove it from the deployment environment's allowed branches. Removing an Identity Center assignment prevents new sessions; immediate revocation also requires deleting the user's existing session records. The durable deployment and recovery contract is in [monitoring operations](../../src/monitoring/OPERATIONS.md).
