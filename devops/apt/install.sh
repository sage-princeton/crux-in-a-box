#!/usr/bin/env bash
set -euo pipefail
component="${1:?package component required}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$component" in base|sdk|controller|node|caddy) ;; *) echo 'Unknown apt component' >&2; exit 2 ;; esac
export DEBIAN_FRONTEND=noninteractive
if [ "$component" = node ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
elif [ "$component" = caddy ]; then
  curl -1fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
fi
apt-get update -qq
xargs -r apt-get install -y -qq < "$root/$component.txt" >/dev/null
