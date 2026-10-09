#!/usr/bin/env bash
# Build the demo image and run it: docker/demo.sh [probe|check|run|bash|reset]
# Secrets pass through from this shell's environment by name, so their values never appear on a command line.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=crux-sdk-scaffold-demo
VOLUME=${CRUX_DEMO_VOLUME:-crux-sdk-scaffold-demo}
SECRETS=(OPENAI_API_KEY SLACK_BOT_TOKEN LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY)
SETTINGS=(LANGFUSE_BASE_URL CRUX_MODEL CRUX_REASONING_EFFORT RUN_SLUG SLACK_CHANNEL_ID)
command=${1:-run}

if [[ $command == reset ]]; then
  docker volume rm "$VOLUME"
  exit
fi

required=()
case $command in
  probe) required=(OPENAI_API_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY CRUX_MODEL) ;;
  run) required=("${SECRETS[@]}" CRUX_MODEL RUN_SLUG) ;;
esac
missing=()
for name in ${required[@]+"${required[@]}"}; do
  [[ -n ${!name:-} ]] || missing+=("$name")
done
if ((${#missing[@]})); then
  echo "export these first: ${missing[*]}" >&2
  exit 2
fi

if ! build_log=$(docker build --tag "$IMAGE" --file docker/Dockerfile . 2>&1); then
  echo "$build_log" >&2
  exit 1
fi

args=(--rm --init --interactive --volume "$VOLUME:/work")
[[ -t 0 && -t 1 ]] && args+=(--tty)
for name in "${SECRETS[@]}" "${SETTINGS[@]}"; do
  [[ -n ${!name:-} ]] && args+=(--env "$name")
done
exec docker run "${args[@]}" "$IMAGE" "$@"
