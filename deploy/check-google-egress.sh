#!/usr/bin/env bash
#
# Can this machine reach the Google API at all?
#
#   bash check-google-egress.sh [source-ip]
#
# With a source address it tests that address specifically. That matters while
# a server holds two addresses: inbound reachability can be checked against
# either, but outbound uses whichever the default route picks, so without
# pinning the source you learn nothing about the candidate.
#
# Run it on any candidate server before trusting it. The test needs no
# credentials, because the distinction is visible without one:
#
#   401  reachable — Google answered the API and asked who we are
#   403  blocked   — Google's edge refused the client before the API saw it
#                    (an HTML page, not a JSON error)
#
# This exists because an IP can satisfy one requirement and fail the other.
# hoshyarseo's first address was reachable from Google and blocked inside Iran;
# its replacement is reachable from Iran and blocked by Google. Both were
# "working servers".
#
set -euo pipefail

# The status goes to stdout and the human-readable line to stderr, so a caller
# can capture one without the other. An earlier version printed both to stdout
# and captured the label along with the code.
SOURCE="${1:-}"
BIND=()
[[ -n "$SOURCE" ]] && BIND=(--interface "$SOURCE")

probe() {
  local label="$1" url="$2" out status type
  out="$(curl "${BIND[@]}" -sS -o /dev/null -m 20 -w '%{http_code} %{content_type}' "$url" 2>/dev/null || true)"
  [[ -z "$out" ]] && out="000 (no answer)"
  status="${out%% *}"
  type="${out#* }"
  printf "  %-34s %s  %s\n" "$label" "$status" "${type:-}" >&2
  printf "%s" "$status"
}

if [[ -n "$SOURCE" ]]; then
  echo "testing source address: ${SOURCE}"
  if ! ip -4 addr show | grep -q "inet ${SOURCE}/"; then
    echo "  WARNING: ${SOURCE} is not configured on any interface here." >&2
    echo "  Add it in the panel and restart, or the test below is meaningless." >&2
  fi
fi
echo "outbound address: $(curl "${BIND[@]}" -sS -m 10 https://api.ipify.org 2>/dev/null || echo unknown)"
echo

echo "Google endpoints:"
TOKEN_STATUS="$(probe "oauth2.googleapis.com/token" "https://oauth2.googleapis.com/token")"
API_STATUS="$(probe "www.googleapis.com webmasters" "https://www.googleapis.com/webmasters/v3/sites")"
echo

case "$API_STATUS" in
  401)
    echo "REACHABLE. Google answered the API and asked for credentials."
    echo
    echo "This address passes the Google half. The other half — whether it is"
    echo "reachable from inside Iran — has to be tested from there, not here."
    ;;
  403)
    echo "BLOCKED. Google's edge refused this client before the API saw the"
    echo "request — the body is an HTML error page, not a JSON API error."
    echo "Minting a token can still succeed (it did: ${TOKEN_STATUS}); the two"
    echo "endpoints are blocked independently, so a working token proves"
    echo "nothing about whether the data call will work."
    echo
    echo "Fixes, in order of preference:"
    echo "  * a different outbound address"
    echo "  * SEO_AGENT_GOOGLE_PROXY_URL pointing at a host that is not blocked"
    exit 1
    ;;
  000)
    echo "NO ANSWER. Not a Google decision — nothing got through at all."
    exit 1
    ;;
  *)
    echo "Unexpected status ${API_STATUS}. Run diagnose-gsc.sh for the body."
    exit 1
    ;;
esac
