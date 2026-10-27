# Maintenance handoff

The operator fills this from the immutable S3 archive before launch; the agent
then appends actual verification evidence. Do not launch with this template alone.

- Archive receipt, URI, object version and SHA-256:
- Restored manifest and original run root under handoff/<archive-id>/:
- Editable project path and Git revision (copy into site/ if useful):
- Source/deployment mapping and active release:
- Resource inventory, accounts/regions, endpoints, access instructions:
- Credential environment-variable names supplied through runtime-secrets.json (no values):
- Existing build/install and deterministic/editorial gate commands:
- Existing logs, completion report and visual snapshot indexes:
- Known native Payload preview URL and deployment/data mapping (agent defines publication/rollback procedures):
- Database/media/config/TLS backup identifiers, restore evidence and retention:
- Successor rebuild, runtime credential availability and CMS/SSH/database/media/TLS access evidence:
- Open migration issues and baseline accessibility/performance/security results:

Use native Payload admin.preview, versions/drafts and authenticated frontend draft mode. Devise, test, document and execute publication and rollback yourself; preserve the currently published content while preparing drafts. Use wave_check for credit-accounted WAVE audits and budget_status for the $100 Langfuse inference allowance. There is no token or turn cap.
