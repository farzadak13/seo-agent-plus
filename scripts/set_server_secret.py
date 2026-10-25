"""Store one secret in the server's /etc/seoagent/env, without it ever being seen.

    python scripts/set_server_secret.py SERP_API_KEY

The value is asked for at a hidden prompt on this machine and travels to the
server on ssh's standard input, so it is never in a chat, a command line
(`ps`), a shell history or a log. On the server it replaces any earlier line
of the same name, keeping the file root:seoagent 0640 like app-setup.sh made
it. The service is not restarted: settings that use the secret are switched
on separately, once it is in place.

The ssh options are the ones this server's network path needs: the default
post-quantum key exchange stalls on its large packets.
"""
from __future__ import annotations

import base64
import getpass
import re
import subprocess
import sys

HOST = "seo-deploy"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "KexAlgorithms=curve25519-sha256", HOST]

# Runs as root on the server. Reads the value from stdin; never prints it.
REMOTE = r'''
import grp, os, re, sys
name = sys.argv[1]
value = sys.stdin.read().strip()
if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name):
    sys.exit("bad name")
if not value or any(c in value for c in "\r\n\0"):
    sys.exit("the value is empty or spans lines; nothing was written")
path = "/etc/seoagent/env"
with open(path, encoding="utf-8") as handle:
    lines = [line for line in handle.read().splitlines() if not line.startswith(name + "=")]
lines.append(f"{name}={value}")
temporary = path + ".new"
with open(temporary, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
os.chown(temporary, 0, grp.getgrnam("seoagent").gr_gid)
os.chmod(temporary, 0o640)
os.replace(temporary, path)
print(f"stored {name} ({len(value)} characters) in {path}")
'''


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", sys.argv[1]):
        sys.exit("Usage: python scripts/set_server_secret.py NAME   (NAME in CAPITALS, e.g. SERP_API_KEY)")
    name = sys.argv[1]
    value = getpass.getpass(f"Paste the value for {name} (hidden), then Enter: ").strip()
    if not value:
        sys.exit("Nothing entered; nothing was sent.")
    code = base64.b64encode(REMOTE.encode("utf-8")).decode("ascii")
    remote = f'sudo -n python3 -c "$(echo {code} | base64 -d)" {name}'
    result = subprocess.run([*SSH, remote], input=value + "\n", text=True, capture_output=True, timeout=60)
    if result.returncode != 0:
        sys.exit(f"Not stored: {result.stderr.strip() or result.stdout.strip()}")
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
