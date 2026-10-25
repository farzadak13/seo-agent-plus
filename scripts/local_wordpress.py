"""A throwaway WordPress with the HoshyarSEO Connector, for the live Stage 37/38 test.

    python scripts/local_wordpress.py up     # start, install, write .env.wordpress
    python scripts/local_wordpress.py down   # stop and delete it, data included

`up` can be run again at any time: it installs only what is missing and
issues a fresh application password, revoking the previous ones.

What it sets up (docker/wordpress/compose.yml, on 127.0.0.1:58080):

  * WordPress, pretty permalinks, and the connector plugin from this checkout;
  * an Editor account, the role a customer would connect with, and an
    application password for it;
  * one published page for the test to retitle and restore.

The settings land in .env.wordpress (git-ignored), where
tests/integration/test_wordpress_live.py reads them. Nothing secret is
printed. The page title is ASCII on purpose: Persian on a Windows command
line can arrive mangled without an error. The test writes Persian over HTTP.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = ["docker", "compose", "-f", os.path.join(ROOT, "docker", "wordpress", "compose.yml")]
BASE_URL = "http://localhost:58080"
EDITOR = "hoshyarseo-editor"
PAGE_SLUG = "hoshyarseo-test"
ENV_FILE = os.path.join(ROOT, ".env.wordpress")


def compose(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        [*COMPOSE, *args],
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE if capture else None,
    )
    return (result.stdout or "").strip() if capture else ""


def wp(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        [*COMPOSE, "run", "--rm", "--no-deps", "cli", "wp", *args],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        sys.exit(f"wp {args[0]} failed:\n{result.stderr.strip()}")
    return result.stdout.strip() if result.returncode == 0 else ""


def wait_for_http(timeout_seconds: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE_URL + "/wp-login.php", timeout=5):
                return
        except urllib.error.HTTPError:
            return  # it answered
        except OSError:
            time.sleep(2)
    sys.exit(f"WordPress did not answer on {BASE_URL} within {timeout_seconds:.0f}s.")


def up() -> None:
    compose("up", "-d", "--wait", "db", "wordpress")
    wait_for_http()

    if not _installed():
        wp(
            "core", "install",
            f"--url={BASE_URL}",
            "--title=HoshyarSEO Test",
            "--admin_user=admin",
            # Never used: the test works as the Editor. Random so nothing guessable exists.
            f"--admin_password={secrets.token_urlsafe(24)}",
            "--admin_email=admin@example.test",
            "--skip-email",
        )
    wp("rewrite", "structure", "/%postname%/", "--hard")
    wp("plugin", "activate", "hoshyarseo-connector")

    if not wp("user", "get", EDITOR, "--field=ID", check=False):
        wp(
            "user", "create", EDITOR, "editor@example.test", "--role=editor",
            f"--user_pass={secrets.token_urlsafe(24)}",
        )
    wp("user", "application-password", "delete", EDITOR, "--all", check=False)
    password = wp("user", "application-password", "create", EDITOR, "seoagent-tests", "--porcelain")

    if not wp("post", "list", "--post_type=page", f"--name={PAGE_SLUG}", "--field=ID"):
        wp(
            "post", "create", "--post_type=page", "--post_status=publish",
            "--post_title=Mens shoes", f"--post_name={PAGE_SLUG}",
        )

    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "# Written by scripts/local_wordpress.py. Local test values only.\n"
            f"SEO_AGENT_TEST_WP_URL={BASE_URL}\n"
            f"SEO_AGENT_TEST_WP_USER={EDITOR}\n"
            f"SEO_AGENT_TEST_WP_PASSWORD={password}\n"
            f"SEO_AGENT_TEST_WP_PAGE_URL={BASE_URL}/{PAGE_SLUG}/\n"
        )
    print(f"WordPress is up on {BASE_URL}; test settings written to .env.wordpress (password not shown).")
    print('Run the test:  pytest -q -m integration tests/integration/test_wordpress_live.py')


def _installed() -> bool:
    # `wp core is-installed` answers only through its exit code.
    result = subprocess.run(
        [*COMPOSE, "run", "--rm", "--no-deps", "cli", "wp", "core", "is-installed"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def down() -> None:
    compose("down", "-v")
    if os.path.exists(ENV_FILE):
        os.remove(ENV_FILE)
    print("WordPress and its data are gone.")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "up":
        up()
    elif command == "down":
        down()
    else:
        sys.exit("Usage: python scripts/local_wordpress.py up|down")


if __name__ == "__main__":
    main()
