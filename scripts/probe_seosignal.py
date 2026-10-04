"""Does the SEO Signal key work?

    python scripts/probe_seosignal.py                 key check only
    python scripts/probe_seosignal.py "کفش مردانه"    also one volume lookup (1 of the daily 50)

The key check sends an empty keyword list. SEO Signal checks the key before
the parameters, so "invalid parameters" means the key and plan are accepted;
it is not a keyword question and should not count against the daily cap.

Reads the same settings the application does (SEO_AGENT_SEOSIGNAL_API_KEY_REF
and the variable it names). The key is never printed. Goes through the same
adapter the service uses, so a pass here means the service will work.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.keyword_intel.contracts import KeywordProviderError  # noqa: E402
from app.keyword_intel.seosignal import SeoSignalClient, SeoSignalSearchVolume  # noqa: E402


def main() -> int:
    ref = (os.environ.get("SEO_AGENT_SEOSIGNAL_API_KEY_REF") or "").strip()
    if not ref:
        sys.exit("SEO_AGENT_SEOSIGNAL_API_KEY_REF is not set.")
    key = (os.environ.get(ref) or "").strip()
    if not key:
        sys.exit(f"{ref} is not set.")
    print(f"key: ${ref}, {len(key)} characters")
    volumes = SeoSignalSearchVolume(SeoSignalClient(api_key=key))

    print("\n--- key check (no keyword asked) ---")
    try:
        volumes.check_key()
    except KeywordProviderError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        if exc.detail:
            print(f"the service said: {exc.detail}")
        return 1
    print("ok: the key and plan are accepted")

    if len(sys.argv) > 1:
        print("\n--- search volume for one keyword (uses 1 request of the daily cap) ---")
        try:
            [volume] = volumes.search_volumes([sys.argv[1]])
        except KeywordProviderError as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            if exc.detail:
                print(f"the service said: {exc.detail}")
            return 1
        print(f"  {volume.keyword}: {volume.search_volume} / month, competition {volume.competition.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
