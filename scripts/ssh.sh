#!/bin/bash
# SSH into a running RhubarbTart clone with host-key checking that survives
# DHCP address reuse: keys are pinned per VM *name* (HostKeyAlias), trusted on
# first use, and any later change for that name is refused.
#
#   ./scripts/ssh.sh <vm> [ssh args...]
#
# Forget a deleted clone's key: ssh-keygen -R <vm> -f ~/.ssh/known_hosts_rhubarbtart

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

VM="${1:?vm name}"; shift
IP="$(tart ip --wait 60 "$VM")"
exec ssh \
  -o HostKeyAlias="$VM" \
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts_rhubarbtart" \
  -o StrictHostKeyChecking=accept-new \
  -o ForwardAgent=no \
  -o ForwardX11=no \
  "${RHUBARB_USER:-admin}@$IP" "$@"
