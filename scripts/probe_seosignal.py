"""Does the SEO Signal key work, and what does the account offer?

    python scripts/probe_seosignal.py                 rank-tracker projects only (free)
    python scripts/probe_seosignal.py "کفش مردانه"    also one volume lookup (1 of the daily 50)

Reads the same settings the application does (SEO_AGENT_SEOSIGNAL_API_KEY_REF
and the variable it names). The key is never printed. Goes through the same
adapters the service uses, so a pass here means the service will work.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.keyword_intel.contracts import KeywordProviderError  # noqa: E402
from app.keyword_intel.seosignal import (  # noqa: E402
    SeoSignalClient,
    SeoSignalRankTracker,
    SeoSignalSearchVolume,
)


def main() -> int:
    ref = (os.environ.get("SEO_AGENT_SEOSIGNAL_API_KEY_REF") or "").strip()
    if not ref:
        sys.exit("SEO_AGENT_SEOSIGNAL_API_KEY_REF is not set.")
    key = (os.environ.get(ref) or "").strip()
    if not key:
        sys.exit(f"{ref} is not set.")
    print(f"key: ${ref}, {len(key)} characters")
    client = SeoSignalClient(api_key=key)

    print("\n--- rank tracker projects (no daily cap) ---")
    try:
        projects = SeoSignalRankTracker(client).projects()
    except KeywordProviderError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    if not projects:
        print("no projects yet; create one in the SEO Signal panel for each site to track")
    for project in projects:
        print(f"  {project.project_id}  {project.domain}  {project.name}  {'active' if project.active else 'inactive'}")

    if len(sys.argv) > 1:
        keyword = sys.argv[1]
        print(f"\n--- search volume for one keyword (uses 1 request of the daily cap) ---")
        try:
            [volume] = SeoSignalSearchVolume(client).search_volumes([keyword])
        except KeywordProviderError as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            return 1
        print(f"  {volume.keyword}: {volume.search_volume} / month, competition {volume.competition.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
