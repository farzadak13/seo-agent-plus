#!/usr/bin/env bash
#
# Does the configured Arvan AI model answer, and answer in a usable shape?
#
#   sudo bash check-llm.sh
#
# Reads /etc/seoagent/env, the same settings the service uses, so the key is
# never typed into a terminal or a chat window. Run it before setting
# SEO_AGENT_TITLE_WORKFLOW_ENABLED=true: with the workflow on, a model that
# does not answer stops the service from starting at all.
#
set -euo pipefail

ENV_FILE="/etc/seoagent/env"
VENV="/opt/seoagent/venv"
SRC="/opt/seoagent/current"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (the key lives in a root-readable file)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$SRC"
"${VENV}/bin/python" scripts/probe_llm.py
# The basic plan's texts are written by another model, if one is set: it must answer too.
if [[ -n "${SEO_AGENT_ARVAN_BASIC_MODEL:-}" ]]; then
  echo
  echo "=== the basic plan's model ==="
  SEO_AGENT_ARVAN_MODEL="$SEO_AGENT_ARVAN_BASIC_MODEL" "${VENV}/bin/python" scripts/probe_llm.py
fi
