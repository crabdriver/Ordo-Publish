import csv
from pathlib import Path
from unittest.mock import patch

from ordo_engine.results import publish_records


def test_old_csv_migration_adds_empty_run_id(tmp_path):
    path = tmp_path / "records.csv"
    old_fields = [field for field in publish_records.PUBLISH_RECORD_FIELDNAMES if field != "run_id"]
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({"article": "/tmp/a.md", "platform": "zhihu", "mode": "publish"})
    rows = publish_records.maybe_migrate_publish_records_csv(path)
    assert rows[0]["run_id"] == ""
    with path.open("r", encoding="utf-8", newline="") as fp:
        assert "run_id" in csv.DictReader(fp).fieldnames


def test_append_holds_independent_flock_across_read_and_replace(tmp_path):
    path = tmp_path / "records.csv"
    with patch.object(publish_records.fcntl, "flock", wraps=publish_records.fcntl.flock) as flock:
        publish_records.append_publish_record_at_path(path, {
            "article": "/tmp/a.md", "platform": "zhihu", "mode": "publish",
            "status": "published", "returncode": 0, "run_id": "run-1",
        })
    assert flock.call_args_list[0].args[1] == publish_records.fcntl.LOCK_EX
    assert flock.call_args_list[-1].args[1] == publish_records.fcntl.LOCK_UN
