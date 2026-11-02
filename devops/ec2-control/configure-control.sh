#!/usr/bin/env bash
set -euo pipefail
: "${CRUX_CONTROLLER_BUNDLE:?}" "${CRUX_CONTROLLER_SETTINGS:?}" "${DATA_VOLUME_ID:?}"
export DEBIAN_FRONTEND=noninteractive
for unit in agentrq caddy; do
  if [ "$(systemctl show "$unit" --property=LoadState --value)" = loaded ]; then systemctl stop "$unit"; fi
done
bash "$CRUX_CONTROLLER_BUNDLE/source/devops/apt/install.sh" controller
systemctl enable --now docker >/dev/null

DATA_DIR=/srv/agentrq
DEVICE=
while read -r device serial; do
  if [ "$serial" = "${DATA_VOLUME_ID//-/}" ]; then DEVICE="$device"; fi
done < <(lsblk --nodeps --noheadings --paths --output NAME,SERIAL)
if [ -z "$DEVICE" ] || [ ! -b "$DEVICE" ]; then
  echo 'Declared EBS data volume is not visible.' >&2
  exit 1
fi
if ! blkid "$DEVICE" >/dev/null; then
  [ "${INITIALIZE_DATA:-false}" = true ] || { echo 'Data volume has no recognized filesystem; initialization was not authorized.' >&2; exit 1; }
  # blkid returns 2 for a device without a recognized filesystem.
  code=0
  blkid -p "$DEVICE" >/dev/null || code=$?
  [ "$code" = 2 ] || { echo 'Cannot identify controller data filesystem.' >&2; exit 1; }
  mkfs.ext4 -q "$DEVICE"
fi
mkdir -p "$DATA_DIR"
CRUX_VOLUME_UUID="$(blkid -s UUID -o value "$DEVICE")"
export CRUX_VOLUME_UUID
if mountpoint -q "$DATA_DIR"; then
  [ "$(findmnt --noheadings --output UUID "$DATA_DIR")" = "$CRUX_VOLUME_UUID" ] \
    || { echo 'Another volume is mounted at the controller data path.' >&2; exit 1; }
else
  mount "$DEVICE" "$DATA_DIR"
fi
mkdir -p "$DATA_DIR/_storage" "$DATA_DIR/_certs"
chown -R 65534:65534 "$DATA_DIR/_storage" "$DATA_DIR/_certs"

if ! command -v caddy >/dev/null 2>&1; then
  bash "$CRUX_CONTROLLER_BUNDLE/source/devops/apt/install.sh" caddy
fi
/opt/crux-bootstrap/bin/python "$CRUX_CONTROLLER_BUNDLE/source/devops/ec2-control/controller_stage.py" \
  configure "$CRUX_CONTROLLER_BUNDLE" "$CRUX_CONTROLLER_SETTINGS"
systemctl daemon-reload
systemctl disable --now agentrq caddy >/dev/null
systemd-analyze verify /etc/systemd/system/agentrq.service
caddy validate --config /etc/caddy/Caddyfile
image="$(jq -r .services.agentrq.image "$DATA_DIR/compose.yaml")"
docker pull "$image"
echo 'Controller configured; use crux-controller start to serve requests.'
