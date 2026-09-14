-- Tenant scoping for the append-only record store.
--
-- tenant_id and site_id are denormalized out of the payload for the same
-- reason snapshot_id already is: they are how records are found. Without an
-- indexed owner column every "records for this customer" query degrades into
-- a scan of the whole append-only table.

ALTER TABLE persistence_records
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NULL;

ALTER TABLE persistence_records
    ADD COLUMN IF NOT EXISTS site_id TEXT NULL;

-- Backfill. Until per-tenant identity exists, one API key means one account,
-- so the stored principal IS that account's tenant. Rows with no owner in
-- their payload stay NULL and are never returned by a tenant-scoped query.
UPDATE persistence_records
   SET tenant_id = payload ->> 'principal_id'
 WHERE tenant_id IS NULL
   AND payload ? 'principal_id'
   AND payload ->> 'principal_id' IS NOT NULL;

UPDATE persistence_records
   SET site_id = payload ->> 'site_id'
 WHERE site_id IS NULL
   AND payload ? 'site_id'
   AND payload ->> 'site_id' IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_persistence_records_tenant
    ON persistence_records (
        tenant_id,
        aggregate_type,
        created_at DESC,
        aggregate_id DESC
    )
    WHERE tenant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_persistence_records_tenant_site
    ON persistence_records (
        tenant_id,
        site_id,
        aggregate_type,
        created_at DESC,
        aggregate_id DESC
    )
    WHERE tenant_id IS NOT NULL
      AND site_id IS NOT NULL;
