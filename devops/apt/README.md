# Box operating-system packages

These package lists are consumed by production Box installers and the Ubuntu
audit image. Ubuntu supplies current security revisions; Dependabot does not
manage apt packages. Requires Docker for the isolated audit image.

From the repository root:

```sh
docker build --pull -t crux-apt:audit -f devops/apt/Dockerfile .
```

The daily and pull-request Ubuntu audit exports package/vulnerability reports.
Runtime, CI and application dependency policy is described in [design.md](../design.md).

## Development

Run `shellcheck devops/apt/install.sh` from the repository root. Exercise changed
package lists in the audit image above and a fresh ciab-devenv Box; installers and
the audit must consume the same lists.
