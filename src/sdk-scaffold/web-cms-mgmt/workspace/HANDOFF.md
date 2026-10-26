# Maintenance handoff

The operator fills this from the immutable S3 archive before launch; the agent
then appends actual verification evidence. Do not launch with this template alone.

- Archive receipt, URI, object version and SHA-256:
- Restored manifest and original run root under handoff/<archive-id>/:
- Editable project path and Git revision (copy into site/ if useful):
- Source/deployment mapping and active release:
- Resource inventory, accounts/regions, endpoints, access instructions:
- Secret identifiers/versions and successor retrieval method (no values):
- Build/install, deterministic/editorial gate and rollback commands:
- Existing logs, completion report and visual snapshot indexes:
- Isolated preview URL/deployment/data procedure; live publication procedure:
- Database/media/config/TLS backup identifiers, restore evidence and retention:
- Successor rebuild, secret retrieval and CMS/SSH/database/media/TLS access evidence:
- Open migration issues and baseline accessibility/performance/security results:
