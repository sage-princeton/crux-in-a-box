# Langfuse cost collector

Deploy a Lambda/API endpoint that reports Langfuse costs by Run. Requires AWS
CLI v2, Python 3, and an authenticated explicit AWS profile with deployment access.
Load `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` and
`COST_TRACKER_TOKEN` from your private secret store.

## Usage

From this directory:

```sh
./deploy.sh --profile crux-2-acct-admin-access
```

The deployment prints its endpoint. Call `POST /cost` with bearer authentication
and a Run/time-window JSON request; [design.md](design.md) specifies the contract
and accounting limits. This service uses no model-provider keys.

## Development

From this directory, run the collector tests and deployment-script checks:

```sh
python3 -m unittest discover -s tests
shellcheck deploy.sh
```
