"""Does the Stage 35 daily reconciliation hold on real data?

The first probe proved the connection works. It also showed that one URL had
2300 impressions on a day when its best query showed 192 — Google withholds
low-volume queries, so query rows never add up to URL totals.

Stage 35 already allows that ("query below URL is partial coverage") but the
size of the gap decides whether query-level CTR detection means anything at
all. If 90% of a page's impressions are invisible at query level, a CTR
detector reading query rows is measuring a sliver and calling it the page.

This probe answers, per day, with full pagination so nothing is truncated:

  * how many distinct queries a URL actually exposes
  * what fraction of the URL's clicks and impressions those queries cover
  * whether URL totals exist for every day queries exist, which is the exact
    precondition `validate_daily_totals` enforces
  * what the real query strings look like, codepoint by codepoint, unfiltered
    — the question the first probe's equals-filter could not answer
  * how far back "final" data actually goes

Dependency-free, same as probe_gsc.py.

    python3 probe_reconciliation.py service-account.json "https://pama.shop/" \\
        "https://pama.shop/search/men-shoes"
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
TIMEOUT = 60
PAGE_SIZE = 25000
DAYS_TO_CHECK = 7
DATA_LAG_DAYS = 4


def fail(message: str, detail: object = None) -> None:
    print(f"\n  FAILED: {message}")
    if detail is not None:
        print(f"  detail: {detail}")
    sys.exit(1)


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_rs256(signing_input: bytes, private_key_pem: str) -> bytes:
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
    segments = [
        b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode()),
        b64url(
            json.dumps(
                {
                    "iss": key["client_email"],
                    "scope": SCOPE,
                    "aud": key["token_uri"],
                    "iat": now,
                    "exp": now + 3600,
                },
                separators=(",", ":"),
            ).encode()
        ),
    ]
    assertion = ".".join(
        segments + [b64url(sign_rs256(".".join(segments).encode("ascii"), key["private_key"]))]
    )
    body = urllib.parse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
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
        fail("token minting refused", f"HTTP {exc.code} — {exc.read().decode(errors='replace')[:400]}")
    except Exception as exc:
        fail("could not reach the token endpoint", exc)
    return payload["access_token"]


def query(token: str, site: str, payload: dict) -> dict:
    encoded = urllib.parse.quote(site, safe="")
    request = urllib.request.Request(
        f"{API}/sites/{encoded}/searchAnalytics/query",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        fail(f"searchAnalytics.query returned HTTP {exc.code}", exc.read().decode(errors="replace")[:500])
    except Exception as exc:
        fail("searchAnalytics.query failed", exc)


def fetch_all(token: str, site: str, payload: dict) -> list[dict]:
    """Page until Google stops returning rows. Truncated data would fake the answer."""
    rows: list[dict] = []
    start_row = 0
    for _ in range(40):
        page = query(token, site, {**payload, "rowLimit": PAGE_SIZE, "startRow": start_row})
        batch = page.get("rows", [])
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start_row += len(batch)
    print("  WARNING: stopped after 40 pages; results may be incomplete")
    return rows


def day_string(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400))


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)

    key_path, site_url, target_url = sys.argv[1:4]
    with open(key_path, encoding="utf-8") as stream:
        key = json.load(stream)
    token = mint_token(key)
    print(f"token OK for {key['client_email']}")

    # --- how fresh is final data, really -----------------------------------
    section("A. how far back does 'final' data actually go")
    latest = None
    for days_ago in range(1, 10):
        day = day_string(days_ago)
        rows = query(
            token,
            site_url,
            {
                "startDate": day,
                "endDate": day,
                "dimensions": ["date"],
                "rowLimit": 1,
                "dataState": "final",
            },
        ).get("rows", [])
        state = "has data" if rows else "empty"
        print(f"  {day}  ({days_ago} days ago)  {state}")
        if rows and latest is None:
            latest = day
    print(f"\n  latest final day: {latest}")

    # --- the reconciliation, day by day ------------------------------------
    section(f"B. query coverage of {target_url}")
    print("  For each day: every query row for this URL, fully paged, against")
    print("  the URL's own totals for the same day.\n")
    print(
        f"  {'date':<12} {'queries':>8} {'q_clicks':>9} {'url_clicks':>11} "
        f"{'q_impr':>9} {'url_impr':>10} {'impr_cov':>9}"
    )
    print(f"  {'-' * 74}")

    coverage_samples = []
    for days_ago in range(DATA_LAG_DAYS, DATA_LAG_DAYS + DAYS_TO_CHECK):
        day = day_string(days_ago)
        page_filter = [{"dimension": "page", "operator": "equals", "expression": target_url}]

        query_rows = fetch_all(
            token,
            site_url,
            {
                "startDate": day,
                "endDate": day,
                "dimensions": ["query"],
                "dimensionFilterGroups": [{"filters": page_filter}],
                "dataState": "final",
            },
        )
        url_rows = fetch_all(
            token,
            site_url,
            {
                "startDate": day,
                "endDate": day,
                "dimensions": ["page"],
                "dimensionFilterGroups": [{"filters": page_filter}],
                "dataState": "final",
            },
        )

        q_clicks = sum(row["clicks"] for row in query_rows)
        q_impr = sum(row["impressions"] for row in query_rows)
        if url_rows:
            u_clicks = url_rows[0]["clicks"]
            u_impr = url_rows[0]["impressions"]
            coverage = f"{(q_impr / u_impr * 100):.1f}%" if u_impr else "n/a"
            coverage_samples.append((q_clicks, u_clicks, q_impr, u_impr))
        else:
            u_clicks = u_impr = 0
            coverage = "NO URL ROW"

        flag = ""
        if q_clicks > u_clicks or q_impr > u_impr:
            flag = "  <-- QUERIES EXCEED URL TOTALS"
        if query_rows and not url_rows:
            flag = "  <-- QUERIES BUT NO URL TOTALS (stage 35 stops here)"

        print(
            f"  {day:<12} {len(query_rows):>8} {q_clicks:>9} {u_clicks:>11} "
            f"{q_impr:>9} {u_impr:>10} {coverage:>9}{flag}"
        )

    if coverage_samples:
        total_q_impr = sum(item[2] for item in coverage_samples)
        total_u_impr = sum(item[3] for item in coverage_samples)
        total_q_clicks = sum(item[0] for item in coverage_samples)
        total_u_clicks = sum(item[1] for item in coverage_samples)
        print(f"\n  across these days:")
        print(
            f"    clicks      visible at query level: {total_q_clicks} of {total_u_clicks}"
            f"  ({total_q_clicks / total_u_clicks * 100:.1f}%)" if total_u_clicks else ""
        )
        print(
            f"    impressions visible at query level: {total_q_impr} of {total_u_impr}"
            f"  ({total_q_impr / total_u_impr * 100:.1f}%)" if total_u_impr else ""
        )

    # --- what real query strings look like, unfiltered ----------------------
    section("C. real query strings, unfiltered — the normalization question")
    print("  The first probe filtered by an exact string, so of course it came")
    print("  back identical. These are whatever Google chose to return.\n")
    rows = query(
        token,
        site_url,
        {
            "startDate": day_string(DATA_LAG_DAYS + 7),
            "endDate": day_string(DATA_LAG_DAYS),
            "dimensions": ["query"],
            "rowLimit": 15,
            "dataState": "final",
        },
    ).get("rows", [])

    for row in rows:
        text = row["keys"][0]
        points = " ".join(hex(ord(character)) for character in text)
        print(f"  {text!r}")
        print(f"      clicks={row['clicks']} impressions={row['impressions']}")
        print(f"      codepoints: {points}")
        suspicious = [
            (character, hex(ord(character)))
            for character in text
            if ord(character) in {0x64A, 0x643, 0x200C, 0x200F, 0x200E, 0xFEFF}
        ]
        if suspicious:
            print(f"      NOTE: contains {suspicious} — arabic yeh/kaf or a bidi mark")

    section("done")
    print(
        "  Read section B first. Low coverage does not break Stage 35, but it\n"
        "  decides whether a query-level CTR signal describes the page or a\n"
        "  sliver of it. A 'NO URL ROW' line is the case that stops the\n"
        "  pipeline outright."
    )


if __name__ == "__main__":
    main()
