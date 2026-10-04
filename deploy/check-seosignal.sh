#!/usr/bin/env bash
#
# Does the SEO Signal key work? Run before turning SEO_AGENT_KEYWORD_PROVIDER on.
#
#   sudo bash check-seosignal.sh                  projects only, costs nothing
#   sudo bash check-seosignal.sh @/path/word.txt  plus one volume lookup
#
# A Persian keyword is read from a file ('@path'), never typed on the command
# line: a mangled keyword is not an error, it is a silent "no data".
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
VENV="/opt/seoagent/venv"
SRC="/opt/seoagent/current"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (the key lives in a root-readable file)." >&2
  exit 1
fi

ARGS=()
if [[ -n "${1:-}" ]]; then
  [[ "$1" == @* ]] || { echo "Give the keyword as @/path/to/file.txt" >&2; exit 1; }
  ARGS+=("$(python3 -c 'import sys; print(open(sys.argv[1], encoding="utf-8").read().strip())' "${1#@}")")
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$SRC"
exec "${VENV}/bin/python" scripts/probe_seosignal.py "${ARGS[@]}"
