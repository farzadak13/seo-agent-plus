"""Diagnostic probe for a real Search Console connection.

Not part of the application. Deliberately dependency-free: it uses only the
Python standard library plus the `openssl` binary, so it runs on a server
without installing anything. The real application will use google-auth; this
exists to answer, in one run, the questions Stage 34 rests on and that no
amount of local testing can settle:

  1. Can this machine mint a Google access token at all? (Network reachability
     is not the same thing: quota and sanctions checks happen at token and
     project level.)
  2. What is the exact siteUrl string Search Console knows this property by?
     A single character difference produces a 403 that reads like "no access".
  3. Does the target URL come back in the form the pipeline assumes, or
     percent-encoded, or with a trailing slash it does not expect?
  4. What does Google return for a Persian query — the same string that was
     sent, or a normalized variant?
  5. How far behind is "final" data, and how many rows actually come back?

Usage:

    python3 probe_gsc.py service-account.json "https://pama.shop/" \\
        "https://pama.shop/search/men-shoes" "کفش پاما مردانه"

Nothing is written anywhere except a short-lived private key file with mode
600, removed before the script exits. Every Google call is read-only.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://www.googleapis.com/webmasters/v3"
TIMEOUT = 45

DATA_LAG_DAYS = 3
WINDOW_DAYS = 28


def fail(message: str, detail: object = None) -> None:
    print(f"\n  FAILED: {message}")
    if detail is not None:
        print(f"  detail: {detail}")
    sys.exit(1)


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_rs256(signing_input: bytes, private_key_pem: str) -> bytes:
    """Sign with openssl so no crypto library has to be installed."""
    handle, path = tempfile.mkstemp(suffix=".pem")
    try:
        os.close(handle)
        os.chmod(path, 0o600)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(private_key_pem)
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", path],
            input=signing_input,
            capture_output=True,
        )
        if result.returncode != 0:
            fail("openssl could not sign the assertion", result.stderr.decode(errors="replace"))
        return result.stdout
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def mint_token(key: dict) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": key["client_email"],
        "scope": SCOPE,
        "aud": key["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }
    segments = [
        b64url(json.dumps(header, separators=(",", ":")).encode()),
        b64url(json.dumps(claims, separators=(",", ":")).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    assertion = ".".join(segments + [b64url(sign_rs256(signing_input, key["private_key"]))])

    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode()
    request = urllib.request.Request(
        key["token_uri"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        fail(
            "token minting was refused. Network reachability is not enough: "
            "this is where a blocked project or region shows up.",
            f"HTTP {exc.code} — {exc.read().decode(errors='replace')[:600]}",
        )
    except Exception as exc:
        fail("could not reach the token endpoint", exc)

    token = payload.get("access_token")
    if not token:
        fail("the token endpoint answered without an access_token", payload)
    print(f"  OK — token minted, valid for {payload.get('expires_in')} seconds")
    return token


def call(token: str, path: str, payload: dict | None = None) -> tuple[int, dict | str]:
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:
        return 0, str(exc)


def window() -> tuple[str, str]:
    day = 86400
    end = time.gmtime(time.time() - DATA_LAG_DAYS * day)
    start = time.gmtime(time.time() - (DATA_LAG_DAYS + WINDOW_DAYS - 1) * day)
    return time.strftime("%Y-%m-%d", start), time.strftime("%Y-%m-%d", end)


def query_payload(start: str, end: str, dimensions: list[str], filters: list[dict] | None = None) -> dict:
    payload = {
        "startDate": start,
        "endDate": end,
        "dimensions": dimensions,
        "rowLimit": 25,
        "dataState": "final",
    }
    if filters:
        payload["dimensionFilterGroups"] = [{"filters": filters}]
    return payload


def main() -> None:
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)

    key_path, site_url, target_url, target_query = sys.argv[1:5]

    # --- 1. token ----------------------------------------------------------
    section("1. minting an access token")
    try:
        with open(key_path, encoding="utf-8") as stream:
            key = json.load(stream)
    except Exception as exc:
        fail("could not read the service account key file", exc)

    for field in ("client_email", "private_key", "token_uri"):
        if field not in key:
            fail(f"the key file has no '{field}' — is this a service account key?")

    print(f"  service account: {key['client_email']}")
    print(f"  token endpoint:  {key['token_uri']}")
    token = mint_token(key)

    start, end = window()

    # --- 2. which properties are visible -----------------------------------
    section("2. properties this service account can see")
    status, body = call(token, "/sites")
    print(f"  HTTP {status}")
    if status != 200:
        fail(
            "sites.list refused. A 403 here usually means the service account "
            "email is not added as a user on the property in Search Console.",
            str(body)[:600],
        )

    entries = body.get("siteEntry", []) if isinstance(body, dict) else []
    if not entries:
        fail(
            "the token works but no property is visible. Add the service "
            "account email as a user in Search Console, wait a minute, re-run."
        )
    for entry in entries:
        marker = "   <-- the one we asked for" if entry.get("siteUrl") == site_url else ""
        print(f"  {str(entry.get('permissionLevel')):<22} {entry.get('siteUrl')}{marker}")

    if site_url not in {entry.get("siteUrl") for entry in entries}:
        print(
            f"\n  NOTE: '{site_url}' is not one of the strings above. Use the "
            "exact string Search Console reports — a trailing slash or a scheme "
            "difference is enough to produce a 403."
        )

    encoded_site = urllib.parse.quote(site_url, safe="")

    # --- 3. what the property actually returns -----------------------------
    section(f"3. search analytics for the whole property, {start} .. {end}")
    status, body = call(
        token,
        f"/sites/{encoded_site}/searchAnalytics/query",
        query_payload(start, end, ["page", "query", "date"]),
    )
    print(f"  HTTP {status}")
    if status != 200:
        fail("searchAnalytics.query refused", str(body)[:600])

    rows = body.get("rows", [])
    print(f"  rows returned: {len(rows)}")
    print(f"  responseAggregationType: {body.get('responseAggregationType')}")
    for extra in body:
        if extra not in {"rows", "responseAggregationType"}:
            print(f"  extra key in response: {extra} = {body[extra]!r}")

    if rows:
        print("\n  first rows, exactly as Google returns them:")
        for row in rows[:5]:
            page, query, day = row["keys"]
            print(f"    date={day}  clicks={row['clicks']}  impressions={row['impressions']}")
            print(f"      page  = {page!r}")
            print(f"      query = {query!r}")

    # --- 4. the specific URL the pipeline will target -----------------------
    section(f"4. the target URL: {target_url}")
    status, body = call(
        token,
        f"/sites/{encoded_site}/searchAnalytics/query",
        query_payload(
            start, end, ["page", "query", "date"],
            [{"dimension": "page", "operator": "equals", "expression": target_url}],
        ),
    )
    print(f"  HTTP {status}")
    if status == 200:
        rows = body.get("rows", [])
        print(f"  rows: {len(rows)}")
        if not rows:
            print(
                "  no rows for that exact string. The page dimension matches "
                "byte for byte, so compare with the forms printed in section 3."
            )
        for row in rows[:5]:
            page, query, day = row["keys"]
            print(f"    {day}  clicks={row['clicks']}  impressions={row['impressions']}  query={query!r}")
    else:
        print(f"  {str(body)[:400]}")

    # --- 5. the Persian query, round-tripped -------------------------------
    section(f"5. the Persian query: {target_query!r}")
    status, body = call(
        token,
        f"/sites/{encoded_site}/searchAnalytics/query",
        query_payload(
            start, end, ["query", "page", "date"],
            [{"dimension": "query", "operator": "equals", "expression": target_query}],
        ),
    )
    print(f"  HTTP {status}")
    if status == 200:
        rows = body.get("rows", [])
        print(f"  rows: {len(rows)}")
        print(f"  codepoints sent:     {[hex(ord(c)) for c in target_query]}")
        for row in rows[:1]:
            query = row["keys"][0]
            same = "identical" if query == target_query else "DIFFERENT from what we sent"
            print(f"  codepoints returned: {[hex(ord(c)) for c in query]}  ({same})")
        for row in rows[:5]:
            query, page, day = row["keys"]
            print(f"    {day}  clicks={row['clicks']}  impressions={row['impressions']}")
            print(f"      query={query!r}")
            print(f"      page={page!r}")
        if not rows:
            print(
                "  no exact match. Section 3 shows which query strings this "
                "property actually has; compare their codepoints with ours."
            )
    else:
        print(f"  {str(body)[:400]}")

    # --- 6. URL-level totals, which stage 35 reconciles against ------------
    section("6. URL-level totals (no query dimension)")
    status, body = call(
        token,
        f"/sites/{encoded_site}/searchAnalytics/query",
        query_payload(
            start, end, ["page", "date"],
            [{"dimension": "page", "operator": "equals", "expression": target_url}],
        ),
    )
    print(f"  HTTP {status}")
    if status == 200:
        rows = body.get("rows", [])
        print(f"  rows: {len(rows)}")
        for row in rows[:5]:
            page, day = row["keys"]
            print(
                f"    {day}  clicks={row['clicks']}  impressions={row['impressions']}"
                f"  position={row.get('position')}"
            )
    else:
        print(f"  {str(body)[:400]}")

    section("done")
    print(
        "  What matters from this run:\n"
        "   - section 2: the exact siteUrl string to store for this site\n"
        "   - section 3: the real shape of page and query values\n"
        "   - section 4/5: whether our assumed URL and query forms match\n"
        "   - section 6: whether URL totals exist for the days queries exist,\n"
        "     which is what the Stage 35 daily reconciliation requires"
    )


if __name__ == "__main__":
    main()
