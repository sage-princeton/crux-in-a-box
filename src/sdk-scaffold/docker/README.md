# docker/

A local image for running the web-cms-product-change demo end to end on a laptop: the scaffold, the demo drop-in and the Slack MCP server in one container. It is for local development and testing only; nothing here is pushed to a registry, and run boxes install the scaffold directly rather than using this image.

How to run the demo, including which secrets to export, is in the scaffold [README](../README.md#run-the-demo-in-docker).

## Notes for development

- `demo.sh` is the only entry point. It rebuilds the image on every call, so code changes are picked up without a separate build step, then passes secrets through by name, so their values never appear on a command line.
- `entrypoint.sh` runs inside the container. It copies the demo drop-in into the `/work` volume once, fills in `{{SLACK_CHANNEL_ID}}`, and runs the scaffold with its state in `/work/web-cms-product-change/.state`, so a rerun resumes the run. `demo.sh reset` deletes the volume to start over.
- Codex's sandbox cannot run in an unprivileged container, so the entrypoint switches the staged copy to `sandbox = "full-access"` and the container is the boundary.
- CI (`.github/workflows/sdk-scaffold-checks.yml`) shellchecks these scripts, builds the image and runs `check` in it without model calls. Run `shellcheck docker/*.sh` before pushing a change here.
