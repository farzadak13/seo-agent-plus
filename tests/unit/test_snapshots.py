from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.snapshots import SnapshotMetadata


def test_valid_snapshot_metadata():
    snapshot = SnapshotMetadata(
        snapshot_id="snapshot-001",
        data_snapshot_id="data-001",
        rule_version="rules-v1",
        config_version="config-v1",
        generated_at=datetime(
            2026,
            9,
            5,
            10,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert snapshot.snapshot_id == "snapshot-001"
    assert snapshot.data_snapshot_id == "data-001"
    assert snapshot.rule_version == "rules-v1"
    assert snapshot.config_version == "config-v1"


def test_empty_snapshot_id_is_rejected():
    with pytest.raises(ValidationError):
        SnapshotMetadata(
            snapshot_id="",
            data_snapshot_id="data-001",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime.now(timezone.utc),
        )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        SnapshotMetadata(
            snapshot_id="snapshot-001",
            data_snapshot_id="data-001",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime.now(timezone.utc),
            unexpected_field="bad",
        )