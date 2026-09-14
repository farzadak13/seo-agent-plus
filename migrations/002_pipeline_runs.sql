CREATE INDEX IF NOT EXISTS idx_persistence_records_aggregate
    ON persistence_records (
        aggregate_type,
        aggregate_id,
        version DESC
    );

CREATE INDEX IF NOT EXISTS idx_persistence_records_site_snapshot
    ON persistence_records (
        snapshot_id,
        aggregate_type
    )
    WHERE snapshot_id IS NOT NULL;