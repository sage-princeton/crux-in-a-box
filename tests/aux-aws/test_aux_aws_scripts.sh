#!/usr/bin/env bash
# Offline tests for the aux-AWS provisioning/teardown scripts. `aws` is
# replaced by a stub on PATH that logs every call and answers from env vars,
# so nothing here touches a real account.
#
# Usage: tests/aux-aws/test_aux_aws_scripts.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/src/ec2-workspaces"
POLICY_DIR="$SRC/aux-aws-policies"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aux-aws-tests.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1"; }
check() { local desc="$1"; shift; if "$@"; then pass "$desc"; else fail "$desc"; fi; }

check_exit() {
  if [ "$1" -eq 0 ]; then pass "exits 0"; else fail "exits 0 (got $1)"; sed 's/^/       | /' "$OUT" | tail -15; fi
}

logged()     { grep -qF -- "$1" "$LOG"; }
not_logged() { ! grep -qF -- "$1" "$LOG"; }
line_of()    { grep -nF -- "$1" "$LOG" | head -1 | cut -d: -f1; }

new_case() {
  CASE_DIR="$TMP_ROOT/$1"
  mkdir -p "$CASE_DIR/bin" "$CASE_DIR/home/.ssh" "$CASE_DIR/scripts"
  LOG="$CASE_DIR/aws.log"; : > "$LOG"
  OUT="$CASE_DIR/out.txt"
  cat > "$CASE_DIR/bin/aws" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$AWS_STUB_LOG"
profile=""; rest=()
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    --region) shift 2 ;;
    *) rest+=("$1"); shift ;;
  esac
done
svc="${rest[0]:-}"; op="${rest[1]:-}"
name=""
for ((i = 0; i < ${#rest[@]}; i++)); do
  case "${rest[i]}" in
    --role-name|--instance-profile-name) name="${rest[i+1]:-}" ;;
  esac
done
have() { [[ " ${STUB_ROLES:-} " == *" $1 "* ]]; }
case "$svc $op" in
  "sts get-caller-identity") if [ "$profile" = aux ]; then echo 222222222222; else echo 111111111111; fi ;;
  "iam get-role")
    if [[ " ${rest[*]} " == *"Principal.AWS"* ]]; then echo "${STUB_AUX_TRUST:-}"; exit 0; fi
    have "$name" || exit 254 ;;
  "iam get-instance-profile") have "$name" || exit 254 ;;
  "ec2 describe-instances") echo "${STUB_INSTANCES:-}" ;;
esac
exit 0
STUB
  chmod +x "$CASE_DIR/bin/aws"
  # Some macOS setups ignore TMPDIR for a bare `mktemp`; pin it to the case dir.
  cat > "$CASE_DIR/bin/mktemp" <<SHIM
#!/usr/bin/env bash
[ \$# -eq 0 ] && exec $(command -v mktemp) "$CASE_DIR/tmp.XXXXXX"
exec $(command -v mktemp) "\$@"
SHIM
  chmod +x "$CASE_DIR/bin/mktemp"
  export PATH="$CASE_DIR/bin:$ORIG_PATH"
  export AWS_STUB_LOG="$LOG" HOME="$CASE_DIR/home"
  export STUB_ROLES="" STUB_AUX_TRUST="" STUB_INSTANCES=""
}
ORIG_PATH="$PATH"

write_config() {
  local slug="$1"; shift
  CFG_FILE="$CASE_DIR/placeholders-$slug.txt"
  {
    printf 'AWS_REGION=us-east-1\nAWS_PROFILE=main\nRUN_SLUG=%s\nAUX_RESOURCE_PROFILE=aux\n' "$slug"
    printf '%s\n' "$@"
  } > "$CFG_FILE"
}

install_workspace_teardown() {
  cp "$SRC/teardown-workspace-aws-resources.sh" "$CASE_DIR/scripts/"
  cat > "$CASE_DIR/scripts/teardown-aux-aws-resources.sh" <<'STUB'
#!/usr/bin/env bash
touch "$AUX_MARKER"
STUB
  chmod +x "$CASE_DIR/scripts/"*.sh
  export AUX_MARKER="$CASE_DIR/aux-teardown-called"
}

echo "policy templates"
new_case policies
check "aux-aws-policies directory exists" test -d "$POLICY_DIR"
shopt -s nullglob
policy_files=("$POLICY_DIR"/*.json)
shopt -u nullglob
check "at least one policy template present" test "${#policy_files[@]}" -gt 0
for f in "${policy_files[@]}"; do
  check "valid JSON: $(basename "$f")" jq -e . "$f" >/dev/null
  check "pretty-printed (not minified): $(basename "$f")" test "$(wc -l < "$f")" -gt 3
done

echo "workspace teardown: default sweeps aux"
new_case ws-default
install_workspace_teardown
write_config old PROVISION_EC2=1
STUB_ROLES="crux-run-old" bash "$CASE_DIR/scripts/teardown-workspace-aws-resources.sh" "$CFG_FILE" --yes >"$OUT" 2>&1
check_exit $?
check "calls aux teardown" test -e "$AUX_MARKER"

echo "workspace teardown: --keep-aux"
new_case ws-keep
install_workspace_teardown
write_config old PROVISION_EC2=1
STUB_ROLES="crux-run-old" bash "$CASE_DIR/scripts/teardown-workspace-aws-resources.sh" "$CFG_FILE" --keep-aux --yes >"$OUT" 2>&1
check_exit $?
check "does not call aux teardown" test ! -e "$AUX_MARKER"
check "banner says aux resources are kept" grep -qi "keep.*aux" "$OUT"
check "warns that kept resources keep billing" grep -qi "billing" "$OUT"
check "still terminates / cleans the box (ssm legacy param step reached)" logged "ssm get-parameter"

echo "workspace teardown: --keep-aux with aux never provisioned"
new_case ws-keep-noaux
install_workspace_teardown
write_config plain
bash "$CASE_DIR/scripts/teardown-workspace-aws-resources.sh" "$CFG_FILE" --keep-aux --yes >"$OUT" 2>&1
check_exit $?
check "does not call aux teardown" test ! -e "$AUX_MARKER"

echo "aux teardown: account owned by another run"
new_case aux-guard
write_config old PROVISION_EC2=1
STUB_ROLES="crux-run-old crux-agent-devops" \
STUB_AUX_TRUST="arn:aws:iam::111111111111:role/crux-run-new" \
  bash "$SRC/teardown-aux-aws-resources.sh" "$CFG_FILE" --yes >"$OUT" 2>&1
check_exit $?
check "does not sweep RDS" not_logged "rds describe-db-instances"
check "does not delete the shared aux role" not_logged "delete-role --role-name crux-agent-devops"
check "still removes its own run role" logged "delete-role --role-name crux-run-old"
check "explains why the sweep was skipped" grep -qi "another run" "$OUT"

echo "aux teardown: account owned by this run"
new_case aux-own
write_config old PROVISION_EC2=1
STUB_ROLES="crux-run-old crux-agent-devops" \
STUB_AUX_TRUST="arn:aws:iam::111111111111:role/crux-run-old" \
  bash "$SRC/teardown-aux-aws-resources.sh" "$CFG_FILE" --yes >"$OUT" 2>&1
check_exit $?
check "sweeps RDS" logged "rds describe-db-instances"
check "deletes the aux role" logged "delete-role --role-name crux-agent-devops"
check "deletes its run role" logged "delete-role --role-name crux-run-old"

echo "provision: handoff from a previous run"
new_case prov-handoff
write_config new PROVISION_EC2=1 PROVISION_S3=1 PROVISION_POSTGRES=1
STUB_ROLES="crux-agent-devops crux-run-old crux-run-new" \
STUB_AUX_TRUST="arn:aws:iam::111111111111:role/crux-run-old" \
  bash "$SRC/provision-aux-aws-resources.sh" "$CFG_FILE" >"$OUT" 2>&1
check_exit $?
check "re-trusts the new run role" logged "update-assume-role-policy --role-name crux-agent-devops"
check "deletes the previous owner's run role" logged "delete-role --role-name crux-run-old"
check "never touches resources (no RDS/S3 deletes)" not_logged "delete-db-instance"
trust_line="$(line_of "update-assume-role-policy --role-name crux-agent-devops")"
delete_line="$(line_of "delete-role --role-name crux-run-old")"
check "re-trusts before deleting the previous role" test "${trust_line:-999}" -lt "${delete_line:-0}"
check "does not delete the new run role" not_logged "delete-role --role-name crux-run-new"
check "does not delete the shared aux role" not_logged "delete-role --role-name crux-agent-devops"

echo "provision: previous run's box still running"
new_case prov-handoff-live
write_config new PROVISION_EC2=1 PROVISION_S3=1
STUB_ROLES="crux-agent-devops crux-run-old crux-run-new" \
STUB_AUX_TRUST="arn:aws:iam::111111111111:role/crux-run-old" \
STUB_INSTANCES="i-0abc" \
  bash "$SRC/provision-aux-aws-resources.sh" "$CFG_FILE" >"$OUT" 2>&1
check_exit $?
check "keeps the previous role while its instance is live" not_logged "delete-role --role-name crux-run-old"
check "warns about it" grep -qi "still in use\|still running\|live instance" "$OUT"

echo "provision: rendered policy documents"
new_case prov-docs
write_config new PROVISION_EC2=1 PROVISION_S3=1
STUB_ROLES="crux-agent-devops crux-run-new" \
  bash "$SRC/provision-aux-aws-resources.sh" "$CFG_FILE" >"$OUT" 2>&1
check_exit $?
docs_ok=1; doc_count=0
while IFS= read -r doc; do
  doc_count=$((doc_count + 1))
  printf '%s' "$doc" | jq -e . >/dev/null 2>&1 || docs_ok=0
  case "$doc" in *'{{'*) docs_ok=0 ;; esac
done < <(tr ' ' '\n' < "$LOG" | grep -A1 -x -- '--policy-document' | grep -v -x -- '--policy-document' | grep -v '^--$')
check "policy documents were sent" test "$doc_count" -gt 0
check "every rendered policy document is valid JSON with no unresolved tokens" test "$docs_ok" -eq 1
check "boundary allows SSM agent traffic" grep -q "ssmmessages:" "$LOG"
check "agent role gets EC2 Instance Connect" grep -q "ec2-instance-connect:SendSSHPublicKey" "$LOG"
check "agent role gets SSM sessions" grep -q "ssm:StartSession" "$LOG"

echo "provision: instance access reconciled away without PROVISION_EC2"
new_case prov-noec2
write_config new PROVISION_S3=1
STUB_ROLES="crux-agent-devops crux-run-new" \
  bash "$SRC/provision-aux-aws-resources.sh" "$CFG_FILE" >"$OUT" 2>&1
check_exit $?
check "does not grant instance access" not_logged "ec2-instance-connect:SendSSHPublicKey"
check "removes any existing instance-access policy" logged "delete-role-policy --role-name crux-agent-devops --policy-name instance-access"

echo "make-new-workspace: flag detection covers every PROVISION_* flag"
new_case flags
pattern="$(sed -n "s/^AUX_FLAG_PATTERN='\(.*\)'$/\1/p" "$SRC/make-new-workspace.sh" | head -1)"
check "AUX_FLAG_PATTERN defined" test -n "$pattern"
for flag in POSTGRES S3 DNS EC2 CLOUDFRONT ACM; do
  # shellcheck disable=SC2016 # expansion happens in the inner bash
  check "detects PROVISION_$flag=1" bash -c 'printf "PROVISION_%s=1\n" "$1" | grep -qE "$2"' _ "$flag" "$pattern"
done
# shellcheck disable=SC2016 # expansion happens in the inner bash
check "ignores an unset flag" bash -c '! printf "PROVISION_ACM=\n" | grep -qE "$1"' _ "$pattern"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
