# Utilities

Support tools used independently of Run deployment. The Google Workspace
bootstrap requires Bash, curl and the supported host package manager. Supply a
Google OAuth client file and `GOG_KEYRING_PASSWORD` through your private secret
store; credentials and generated bundles stay outside Git.

## Usage

From this directory, authorize and package the selected Gmail account:

```sh
./bootstrap/bootstrap-gog.sh /private/client-secret.json user@example.com /private/gog-home.tar.gz
```

Read the script's prerequisites before authorizing access.

## Development

From this directory, check shell changes without authenticating:

```sh
bash -n bootstrap/bootstrap-gog.sh
shellcheck bootstrap/bootstrap-gog.sh
```

Secret scanning is owned by the TruffleHog CI workflow.

The [secret scanner](security/scan-secrets.py) checks derived run exports against credential shapes and an optional on-box literal blacklist. It reports counts without printing matches.
