#!/usr/bin/env bash
#
# Ship the committed code to the server, doing everything that needs no sudo
# and stopping where it does. Runs on the development machine (Git Bash).
#
#   bash deploy/ship.sh upload      build release.tgz from HEAD, copy, unpack the scripts
#   bash deploy/ship.sh check       after the sudo step: is the new release live and ready?
#
# The server keeps sudo behind a password on purpose, so nothing here runs
# sudo. Each step that needs it ends by printing the exact command to paste
# into a terminal on the server (`ssh seo-deploy`).
#
set -euo pipefail

HOST="seo-deploy"
STEP="${1:-}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
fail() { echo "FAILED: $1" >&2; exit 1; }

cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short=10 HEAD)"

case "$STEP" in
  upload)
    [[ -z "$(git status --porcelain --untracked-files=no)" ]] \
      || fail "uncommitted changes; a release is exactly HEAD, so commit first"

    say "building release.tgz from ${SHA}"
    # git archive ships tracked files only: never .env, secrets/ or .venv.
    git archive --format=tar.gz -o release.tgz HEAD
    LOCAL_SUM="$(sha256sum release.tgz | cut -d' ' -f1)"
    echo "sha256 ${LOCAL_SUM}"

    say "copying to ${HOST}"
    scp release.tgz "${HOST}:/tmp/release-${SHA}.tgz"

    say "unpacking the deploy scripts on the server (no sudo)"
    ssh "$HOST" "set -e
      test \"\$(sha256sum /tmp/release-${SHA}.tgz | cut -d' ' -f1)\" = '${LOCAL_SUM}' || { echo 'checksum mismatch'; exit 1; }
      rm -rf ~/release-${SHA} && mkdir -p ~/release-${SHA}
      tar -xzf /tmp/release-${SHA}.tgz -C ~/release-${SHA} deploy
      echo unpacked to ~/release-${SHA}"
    rm -f release.tgz

    say "your turn, on the server"
    cat <<EOF
Paste this into a terminal on the server (ssh ${HOST}):

    sudo bash ~/release-${SHA}/deploy/app-release.sh /tmp/release-${SHA}.tgz ${SHA}

It migrates the database, switches to the new release, and rolls back by
itself if /readyz does not answer. Then, here:

    bash deploy/ship.sh check
EOF
    ;;

  check)
    say "what is live on ${HOST}"
    ssh "$HOST" "set -e
      echo current: \$(readlink /opt/seoagent/current)
      curl -fsS --max-time 10 http://127.0.0.1:8000/readyz && echo"
    LIVE="$(ssh "$HOST" "readlink /opt/seoagent/current")"
    if [[ "$LIVE" == *"-${SHA}" ]]; then
      echo "live release is ${SHA} (HEAD)"
    else
      echo "live release is NOT HEAD (${SHA}); the sudo step has not run, or it rolled back."
      exit 1
    fi
    ;;

  *)
    echo "usage: bash deploy/ship.sh upload|check" >&2
    exit 2
    ;;
esac
