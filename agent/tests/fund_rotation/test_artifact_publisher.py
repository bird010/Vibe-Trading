"""Phase 2 Task 5 — common artifact publisher tests (design §12/§13.5).

The publisher owns safe file names, JSON/CSV serialization and the manifest
index. Common roles are fixed; strategy artifacts are namespaced and can never
override a common role. Path traversal, duplicate roles and unserializable
payloads must fail without ever leaving a success manifest.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backtest.fund_rotation.contracts import StrategyArtifact
from src.stockpred.fund_rotation.artifact_publisher import (
    COMMON_ROLES,
    ArtifactPublicationError,
    ArtifactPublisher,
)


def _publisher(tmp_path):
    return ArtifactPublisher(tmp_path / "run")


def test_common_roles_are_the_fixed_set():
    assert set(COMMON_ROLES) == {
        "manifest", "evaluation_calendar", "targets", "orders",
        "fills", "equity", "metrics", "events",
    }
    # fills keeps the legacy file name; events aligns with the append-based
    # run event log (§29/§30.1).
    assert COMMON_ROLES["fills"] == "trade_events.csv"
    assert COMMON_ROLES["events"] == "events.jsonl"


def test_events_role_indexes_external_jsonl_without_rewriting(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    content = '{"stage": "RUNNING"}\n{"stage": "SUCCEEDED"}\n'
    events_path.write_text(content, encoding="utf-8")

    pub = _publisher(tmp_path)
    path = pub.index_external("events")

    assert path == events_path
    assert events_path.read_text(encoding="utf-8") == content  # untouched
    entry = pub.artifact_index()["events"]
    assert entry["file"] == "events.jsonl"
    assert entry["media_type"] == "application/x-ndjson"
    assert entry["rows"] == 2

    manifest = pub.finalize()
    assert manifest["artifacts"]["events"]["file"] == "events.jsonl"


def test_index_external_requires_existing_file(tmp_path):
    pub = _publisher(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="does not exist"):
        pub.index_external("events")
    with pytest.raises(ArtifactPublicationError, match="not a common role"):
        pub.index_external("clusters")


def test_events_role_cannot_be_published_directly(tmp_path):
    """The append-persisted event log can only be indexed, never rewritten."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    content = '{"stage": "RUNNING"}\n'
    events_path.write_text(content, encoding="utf-8")

    pub = _publisher(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="index_external"):
        pub.publish(StrategyArtifact(
            role="events", media_type="application/json", payload=[{"x": 1}],
        ))
    assert events_path.read_text(encoding="utf-8") == content


def test_publish_common_roles_writes_files_and_index(tmp_path):
    pub = _publisher(tmp_path)
    pub.publish(StrategyArtifact(
        role="targets", media_type="text/csv",
        payload=[{"week_ending": "20240105", "ts_code": "A", "weight": 0.5}],
    ))
    pub.publish(StrategyArtifact(
        role="equity", media_type="text/csv",
        payload=pd.Series([1.0, 1.01], index=["20240108", "20240109"], name="strategy"),
    ))
    pub.publish(StrategyArtifact(
        role="metrics", media_type="application/json", payload={"sharpe": 1.5},
    ))
    pub.publish(StrategyArtifact(
        role="evaluation_calendar", media_type="application/json",
        payload=["20240108", "20240109"],
    ))

    run_dir = tmp_path / "run"
    targets = pd.read_csv(run_dir / "targets.csv", index_col=0)
    assert targets.iloc[0]["ts_code"] == "A"
    equity = pd.read_csv(run_dir / "equity.csv", index_col=0)
    assert list(equity.columns) == ["strategy"]
    assert json.loads((run_dir / "metrics.json").read_text(encoding="utf-8")) == {"sharpe": 1.5}

    index = pub.artifact_index()
    assert set(index) == {"targets", "equity", "metrics", "evaluation_calendar"}
    entry = index["targets"]
    assert entry["file"] == "targets.csv"
    assert entry["media_type"] == "text/csv"
    assert entry["rows"] == 1
    assert entry["checksum"]
    assert "ts_code" in entry["columns"]


def test_strategy_artifact_is_namespaced_and_format_compatible(tmp_path):
    pub = _publisher(tmp_path)
    rows = [
        {"week": "20240105", "ts_code": "A", "cluster_id": 0},
        {"week": "20240105", "ts_code": "B", "cluster_id": 1},
    ]
    pub.publish(StrategyArtifact(
        role="cluster_history", media_type="text/csv", payload=rows,
    ), producer="correlation_all_members")

    path = tmp_path / "run" / "strategy_cluster_history.csv"
    df = pd.read_csv(path, index_col=0)
    assert list(df.columns) == ["week", "ts_code", "cluster_id"]
    entry = pub.artifact_index()["cluster_history"]
    assert entry["file"] == "strategy_cluster_history.csv"
    assert entry["producer"] == "correlation_all_members"
    assert entry["rows"] == 2


def test_strategy_role_cannot_override_common_role(tmp_path):
    pub = _publisher(tmp_path)
    pub.publish(StrategyArtifact(
        role="equity", media_type="text/csv",
        payload=[{"date": "20240108", "strategy": 1.0}],
    ))
    with pytest.raises(ArtifactPublicationError, match="common role"):
        pub.publish(StrategyArtifact(
            role="equity", media_type="text/csv", payload=[{"x": 1}],
        ), producer="some_strategy")


def test_duplicate_common_role_fails(tmp_path):
    pub = _publisher(tmp_path)
    pub.publish(StrategyArtifact(role="metrics", media_type="application/json", payload={}))
    with pytest.raises(ArtifactPublicationError, match="already published"):
        pub.publish(StrategyArtifact(
            role="metrics", media_type="application/json", payload={"x": 1},
        ))


def test_manifest_role_cannot_be_published_directly(tmp_path):
    pub = _publisher(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="manifest"):
        pub.publish(StrategyArtifact(
            role="manifest", media_type="application/json", payload={},
        ))


def test_path_traversal_role_fails_and_writes_nothing(tmp_path):
    pub = _publisher(tmp_path)
    for evil in ("../evil", "a/b", "..", "a..b", ""):
        with pytest.raises(ArtifactPublicationError):
            pub.publish(StrategyArtifact(
                role=evil, media_type="application/json", payload={"x": 1},
            ), producer="s")
    run_dir = tmp_path / "run"
    leaked = list(run_dir.glob("*")) if run_dir.exists() else []
    assert leaked == []
    assert not (tmp_path / "evil").exists()


def test_unserializable_payload_fails_and_poisons_publication(tmp_path):
    pub = _publisher(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="serializ"):
        pub.publish(StrategyArtifact(
            role="metrics", media_type="application/json",
            payload={"bad": object()},
        ))
    # No success manifest can ever be produced now.
    with pytest.raises(ArtifactPublicationError):
        pub.finalize()
    assert not (tmp_path / "run" / "manifest.json").exists()


def test_unknown_media_type_fails(tmp_path):
    pub = _publisher(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="media_type"):
        pub.publish(StrategyArtifact(
            role="targets", media_type="application/x-parquet", payload=b"xx",
        ))


def test_finalize_writes_manifest_index_and_blocks_reuse(tmp_path):
    pub = _publisher(tmp_path)
    pub.publish(StrategyArtifact(
        role="orders", media_type="text/csv",
        payload=[{"order_id": "SIG-1-A", "filled": 100}],
    ))
    manifest = pub.finalize(status="SUCCEEDED", identity={"run_id": "r1"})

    path = tmp_path / "run" / "manifest.json"
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "SUCCEEDED"
    assert on_disk["run_id"] == "r1"
    assert "orders" in on_disk["artifacts"]
    assert manifest["artifacts"]["orders"]["file"] == "orders.csv"
    # The manifest itself is indexed under the reserved role.
    assert on_disk["artifacts"]["manifest"]["file"] == "manifest.json"

    # Publishing after finalize is forbidden (publication is closed).
    with pytest.raises(ArtifactPublicationError):
        pub.publish(StrategyArtifact(
            role="equity", media_type="text/csv", payload=[{"x": 1}],
        ))
    with pytest.raises(ArtifactPublicationError):
        pub.finalize()


def test_finalize_identity_cannot_override_reserved_keys(tmp_path):
    pub = _publisher(tmp_path)
    pub.publish(StrategyArtifact(
        role="metrics", media_type="application/json", payload={},
    ))
    with pytest.raises(ArtifactPublicationError, match="reserved"):
        pub.finalize(identity={"status": "FAILED"})
    assert not (tmp_path / "run" / "manifest.json").exists()
