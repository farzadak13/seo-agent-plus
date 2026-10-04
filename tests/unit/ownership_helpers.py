"""Test helper: mark a site as proved, as POST /ownership/verify would."""
from datetime import datetime, timezone


def mark_verified(site_store, site_id: str) -> None:
    site = site_store.get(site_id)
    site_store.update(
        site.model_copy(
            update={"ownership_method": "meta_tag", "ownership_verified_at": datetime.now(timezone.utc)}
        )
    )
