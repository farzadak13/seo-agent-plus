#!/usr/bin/env bash
#
# Replace the API key. Use it whenever the key has been on a screen, in a
# chat window, in a shell history, or in a screenshot.
#
#   sudo bash rotate-api-key.sh
#
# The new key is never printed. Nothing needs to read it by eye: every script
# here takes it from /etc/seoagent/env. If you genuinely need it for an
# external client, read it deliberately with
#     sudo grep '^SEO_AGENT_API_KEY=' /etc/seoagent/env
# rather than having it appear in output you might paste somewhere.
#
set -euo pipefail

# Every request below goes to the public name so that nginx and the
# certificate are genuinely exercised, but --resolve pins it to the loopback
# address. Reaching your own public IP from inside the machine depends on the
# network doing hairpin NAT, and on DNS answering — neither is guaranteed, and
# when it fails it fails as a hang rather than as an error. The first run of
# this check timed out for exactly that reason while the thing it was checking
# was fine.
RESOLVE=(--resolve "hoshyarseo.ir:443:127.0.0.1")

ENV_FILE="/etc/seoagent/env"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
[[ -f "$ENV_FILE" ]] || { echo "${ENV_FILE} is missing." >&2; exit 1; }

say "rotating"
NEW="$(openssl rand -hex 32)"
cp -p "$ENV_FILE" "${ENV_FILE}.bak"
sed -i "s|^SEO_AGENT_API_KEY=.*|SEO_AGENT_API_KEY=${NEW}|" "$ENV_FILE"
grep -q "^SEO_AGENT_API_KEY=${NEW}$" "$ENV_FILE" || {
  echo "The replacement did not take; restoring." >&2
  mv -f "${ENV_FILE}.bak" "$ENV_FILE"
  exit 1
}
rm -f "${ENV_FILE}.bak"
echo "written (not shown)"

say "restarting"
systemctl restart seoagent.service
sleep 3
curl -fsS --max-time 10 http://127.0.0.1:8000/readyz >/dev/null \
  || { echo "The service did not come back." >&2; journalctl -u seoagent -n 30 --no-pager >&2; exit 1; }
echo "service is ready"

say "checking the old key no longer works"
# Any wrong key must be refused; this proves the restart actually took effect
# rather than leaving the old value loaded in the running process.
CODE="$(curl "${RESOLVE[@]}" -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -H "authorization: bearer definitely-not-the-key" \
        https://hoshyarseo.ir/v1/sites -X POST \
        -H 'content-type: application/json' \
        -d '{"name":"x","base_url":"https://example.com"}')"
[[ "$CODE" == "401" || "$CODE" == "403" ]] || {
  echo "A bad key returned ${CODE}; authentication is not being enforced." >&2
  exit 1
}
echo "a wrong key is refused with ${CODE}"

say "done"
echo "The old key is dead. Scripts here read the new one from ${ENV_FILE}."
