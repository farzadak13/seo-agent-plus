#!/usr/bin/env bash
#
# Make a second address usable long enough to test it.
#
#   sudo bash try-second-ip.sh up   185.204.169.52
#   sudo bash try-second-ip.sh down 185.204.169.52
#
# A server given a second address by DHCP ends up with two default routes at
# the same metric. Every packet then leaves by whichever one the kernel picks
# first, regardless of its source address — so a reply to a connection that
# arrived on the second interface goes out the first, carrying a source the
# provider will not accept, and is dropped as spoofed. Inbound times out and
# outbound gets nowhere, which looks exactly like a blocked or unconfigured
# address.
#
# The fix is a routing table of its own for that address, selected by a rule
# matching its source. This is deliberately runtime-only: it disappears on
# reboot, which is what you want for something you are still evaluating.
#
# The end state is not this. Once an address is chosen, the other one is
# removed in the panel and a single interface needs none of it.
#
set -euo pipefail

TABLE=100
ACTION="${1:-}"
ADDRESS="${2:-}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
if [[ "$ACTION" != "up" && "$ACTION" != "down" ]] || [[ -z "$ADDRESS" ]]; then
  echo "Usage: sudo bash try-second-ip.sh up|down <address>" >&2
  exit 1
fi

# Derive the interface and gateway rather than asking for them: a mistyped
# gateway would produce a silent black hole that looks like the problem we are
# trying to diagnose.
DEV="$(ip -4 -o addr show | awk -v a="$ADDRESS" '$4 ~ "^"a"/" {print $2}' | head -1)"
if [[ -z "$DEV" ]]; then
  echo "${ADDRESS} is not configured on any interface." >&2
  echo "Add it in the panel and restart the server first." >&2
  exit 1
fi
GATEWAY="$(ip -4 route show default dev "$DEV" | awk '{print $3}' | head -1)"
SUBNET="$(ip -4 -o route show scope link dev "$DEV" proto kernel | awk '{print $1}' | head -1)"
echo "address:   ${ADDRESS}"
echo "interface: ${DEV}"
echo "gateway:   ${GATEWAY:-(none found)}"
echo "subnet:    ${SUBNET:-(none found)}"
[[ -n "$GATEWAY" && -n "$SUBNET" ]] || { echo "Cannot determine routing for ${DEV}." >&2; exit 1; }

if [[ "$ACTION" == "down" ]]; then
  say "removing the rule and table"
  while ip rule show | grep -q "from ${ADDRESS} lookup ${TABLE}"; do
    ip rule del from "$ADDRESS" lookup "$TABLE"
  done
  ip route flush table "$TABLE" 2>/dev/null || true
  echo "removed; the address is back to being unusable as a source"
  exit 0
fi

say "giving ${ADDRESS} a routing table of its own"
ip route flush table "$TABLE" 2>/dev/null || true
ip route add "$SUBNET" dev "$DEV" scope link src "$ADDRESS" table "$TABLE"
ip route add default via "$GATEWAY" dev "$DEV" table "$TABLE"
while ip rule show | grep -q "from ${ADDRESS} lookup ${TABLE}"; do
  ip rule del from "$ADDRESS" lookup "$TABLE"
done
ip rule add from "$ADDRESS" lookup "$TABLE"
ip route show table "$TABLE" | sed 's/^/  /'

say "does anything leave from it now"
SEEN="$(curl --interface "$ADDRESS" -sS -m 15 https://api.ipify.org 2>/dev/null || true)"
if [[ -z "$SEEN" ]]; then
  echo "Still nothing. The address is configured and routed but the network" >&2
  echo "is not accepting traffic from it — check its security group in the" >&2
  echo "panel; an address without one is not usable." >&2
  exit 1
fi
echo "the internet sees: ${SEEN}"
if [[ "$SEEN" != "$ADDRESS" ]]; then
  echo "WARNING: that is not the address we bound to. Traffic is still" >&2
  echo "leaving by the other interface." >&2
  exit 1
fi

say "next"
cat <<EOF
Outbound works from this address. Two things still have to be true before
it is worth keeping:

  1. Google accepts it:
         bash check-google-egress.sh ${ADDRESS}
     401 is what you want. 403 with an HTML body means blocked.

  2. It is reachable from Iran — test that from there, not from here:
         ssh deploy@${ADDRESS} "hostname"

Only if both pass: remove the old address in the panel, restart, update the
DNS records and the ssh config. Do not remove the old one first.
EOF
