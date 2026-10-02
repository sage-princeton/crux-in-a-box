#!/bin/sh
set -eu
ssh-keygen -q -t ed25519 -N '' -f /tmp/inspection-key
{ printf 'restrict '; cat /tmp/inspection-key.pub; } > /etc/ssh/authorized_keys/crux-inspect
exec /usr/sbin/sshd -D -e
