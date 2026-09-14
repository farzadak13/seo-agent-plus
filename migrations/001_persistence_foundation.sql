CREATE TABLE IF NOT EXISTS persistence_records (
    record_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,

    snapshot_id TEXT NULL,
    data_snapshot_id TEXT NULL,
    rule_version TEXT NULL,
    config_version TEXT NULL,

    PRIMARY KEY (
        aggregate_type,
        aggregate_id,
        version
    ),

    CONSTRAINT persistence_record_id_unique
        UNIQUE (record_id)
);

CREATE INDEX IF NOT EXISTS idx_persistence_records_type_created
    ON persistence_records (
        aggregate_type,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_persistence_records_snapshot
    ON persistence_records (
        snapshot_id
    )
    WHERE snapshot_id IS NOT NULL;
