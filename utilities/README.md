# Utilities

Support tools used independently of Run deployment. `bootstrap/bootstrap-gog.sh`
prepares Google Workspace CLI credentials; read its usage before running it.
It requires Bash, curl and the supported host package manager. Credentials remain
private and outside Git. Secret scanning is owned by the TruffleHog CI workflow.
