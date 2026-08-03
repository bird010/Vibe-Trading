"""Sequential batch orchestration and failure-isolation tests.

Covers separate data-loading and decision-start boundaries, bounded
single-worker execution, immutable per-variant requirements, partial evidence,
snapshot failure, cancellation and restart recovery.
"""

from __future__ import annotations

import json
import threading

import pytest
from pydantic import BaseModel

from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    StrategyDataRequirements,
    StrategyDiagnostics,
    TargetWeightDecision,
)
from src.stockpred.fund_rotation.batch_models import StrategyBatchRequest
from src.stockpred.fund_rotation.batch_service import (
    BatchPlanningError,
    BatchService,
)

import pandas as _pd

CALENDAR = _pd.bdate_range(
    "2024-01-01",
    periods=300,
).strftime("%Y%m%d").tolist()


class FakeBatchConfig(BaseModel):
    lookback_days: int = 30
    fail_on_signal: int = -1


FAKE_DESCRIPTOR = FundRotationStrategyDescriptor(
    id="fake_batch",
    name="Fake Batch",
    description="orchestration test double",
    interface_version="1.0",
    supported_universe=("etf",),
    deterministic=True,
)


class FakeBatchSession:
    def __init__(self, config, execution_log=None, session_record=None):
        self.config = config
        self.execution_log = execution_log
        self.session_record = session_record
        self.evaluate_count = 0

    def scheduled_dates(
        self,
        calendar,
        decision_start_date,
        evaluation_end_date,
    ):
        if self.session_record is not None:
            self.session_record["decision_start_date"] = decision_start_date
        window = [
            date
            for date in calendar
            if decision_start_date <= date <= evaluation_end_date
        ]
        return tuple(window[::5])

    def evaluate(self, context):
        self.evaluate_count += 1
        if self.evaluate_count == self.config.fail_on_signal:
            raise RuntimeError("invariant broken")
        if self.execution_log is not None:
            self.execution_log.append(context.signal_date)
        return TargetWeightDecision(
            decision_id=f"{context.signal_date}-fake",
            signal_date=context.signal_date,
            action=DecisionKind.SET_TARGETS,
            target_weights={},
            cash_weight=1.0,
        )

    def finalize(self):
        return StrategyDiagnostics()


class FakeBatchStrategy:
    descriptor = FAKE_DESCRIPTOR
    config_model = FakeBatchConfig
    session_log = []

    def __init__(self, execution_log=None):
        self.execution_log = execution_log

    def resolve_requirements(self, config):
        return StrategyDataRequirements(
            required_datasets=("fund",),
            required_fields=("close",),
            warmup_trade_days=config.lookback_days,
            frequency="weekly",
            needs_benchmark=False,
        )

    def create_session(self, initialization, config):
        record = {
            "run_id": initialization.run_id,
            "lookback_days": config.lookback_days,
        }
        self.session_log.append(record)
        return FakeBatchSession(config, self.execution_log, record)


def _calendar_metadata():
    from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot

    return PinnedFundDataSnapshot(
        fund_version=17,
        fund_adj_version=23,
        dim_version=31,
        universe_codes=("E1",),
        trading_dates=tuple(CALENDAR),
        fingerprint="fp-test",
    )


def _frames_loader(snapshot, data_start, data_end):
    import pandas as pd

    dates = [
        date
        for date in CALENDAR
        if data_start <= date <= data_end
    ]
    fund_daily = pd.DataFrame(
        [
            {
                "ts_code": "E1",
                "trade_date": date,
                "close": 1.0,
                "vol": 1,
                "amount": 1.0,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "pre_close": 1.0,
            }
            for date in dates
        ]
    )
    fund_adj = pd.DataFrame(
        [
            {
                "ts_code": "E1",
                "trade_date": date,
                "adj_factor": 1.0,
            }
            for date in dates
        ]
    )
    dim_fund = pd.DataFrame(
        [
            {
                "ts_code": "E1",
                "name": "测试ETF",
                "list_date": "20200101",
            }
        ]
    )
    return fund_daily, fund_adj, dim_fund


def _service(
    tmp_path,
    *,
    frames_loader=None,
    fail_frames=False,
    auto_start=False,
):
    FakeBatchStrategy.session_log = []

    def frames(snapshot, data_start, data_end):
        if fail_frames:
            raise RuntimeError("snapshot read failed")
        if frames_loader is not None:
            return frames_loader(snapshot, data_start, data_end)
        return _frames_loader(snapshot, data_start, data_end)

    from backtest.fund_rotation.catalog import FundRotationStrategyCatalog

    catalog = FundRotationStrategyCatalog([FakeBatchStrategy])
    return BatchService(
        tmp_path,
        catalog=catalog,
        metadata_loader=_calendar_metadata,
        frames_loader=frames,
        auto_start=auto_start,
    )


def _request(
    variants,
    *,
    start=None,
    end=None,
    key="key-1",
    schema_version="1",
):
    return StrategyBatchRequest(
        **{
            "schema_version": schema_version,
            "idempotency_key": key,
            "mode": "RESEARCH_ONLY",
            "evaluation_start_date": start or CALENDAR[100],
            "evaluation_end_date": end or CALENDAR[200],
            "execution": {"initial_capital": 100_000.0},
            "variants": variants,
        }
    )


def _submit_and_run(service, request):
    outcome = service.submit_batch(request)
    service.run_batch_sync(outcome["batch_id"])
    return outcome


def _resolved(service, batch_id):
    batch_dir = service.persistence.batch_dir(batch_id)
    return json.loads(
        (batch_dir / "resolved_batch.json").read_text(encoding="utf-8")
    )


def _state(service, batch_id):
    batch_dir = service.persistence.batch_dir(batch_id)
    return json.loads(
        (batch_dir / "state.json").read_text(encoding="utf-8")
    )


class TestPlanning:
    def test_data_start_and_decision_start_are_distinct(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ],
            start=CALENDAR[100],
            end=CALENDAR[200],
        )
        outcome = _submit_and_run(service, request)
        plan = _resolved(service, outcome["batch_id"])["plan"]
        variant_plan = plan["variants"][0]
        assert variant_plan["anchor_decision_date"] == CALENDAR[95]
        assert variant_plan["decision_start_date"] == CALENDAR[95]
        assert variant_plan["data_start"] == CALENDAR[65]
        assert plan["data_start"] == CALENDAR[65]
        assert plan["earliest_decision_start_date"] == CALENDAR[95]

    def test_planning_scans_no_market_data_and_loads_once(self, tmp_path):
        scanned = []

        def tracking_frames(snapshot, data_start, data_end):
            scanned.append((data_start, data_end))
            return _frames_loader(snapshot, data_start, data_end)

        service = _service(tmp_path, frames_loader=tracking_frames)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        outcome = _submit_and_run(service, request)
        assert scanned == [(CALENDAR[55], CALENDAR[200])]
        assert _state(service, outcome["batch_id"])["stage"] == "SUCCEEDED"

    def test_all_variants_share_one_snapshot_fingerprint(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        outcome = _submit_and_run(service, request)
        resolved = _resolved(service, outcome["batch_id"])
        fingerprints = {
            variant["snapshot_fingerprint"]
            for variant in resolved["variants"]
        }
        assert fingerprints == {"fp-test"}
        assert resolved["plan"]["data_start"] == CALENDAR[55]
        assert {
            variant["decision_start_date"]
            for variant in resolved["plan"]["variants"]
        } == {CALENDAR[95]}

    def test_no_pre_evaluation_decision_reports_insufficient(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ],
            start=CALENDAR[30],
            end=CALENDAR[200],
        )
        with pytest.raises(BatchPlanningError) as exc_info:
            service.submit_batch(request)
        assert exc_info.value.code == "FUND_ROTATION_INSUFFICIENT_HISTORY"

    def test_duplicate_variants_fail_structurally_and_keep_idempotent(
        self,
        tmp_path,
    ):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
            ]
        )
        with pytest.raises(BatchPlanningError) as exc_info:
            service.submit_batch(request)
        assert exc_info.value.code == "FUND_ROTATION_BATCH_INVALID"

        from src.stockpred.fund_rotation.batch_models import canonical_payload_hash

        record, created = service.persistence.submit(
            request.idempotency_key,
            canonical_payload_hash(request),
        )
        assert created is False
        assert _state(service, record["batch_id"])["stage"] == "FAILED"

    def test_no_scheduled_decision_reports_insufficient_history(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 400},
                }
            ]
        )
        with pytest.raises(BatchPlanningError) as exc_info:
            service.submit_batch(request)
        assert exc_info.value.code == "FUND_ROTATION_INSUFFICIENT_HISTORY"

    def test_early_evaluation_start_traces_back_from_anchor(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ],
            start=CALENDAR[50],
            end=CALENDAR[200],
        )
        outcome = _submit_and_run(service, request)
        variant_plan = _resolved(service, outcome["batch_id"])["plan"][
            "variants"
        ][0]
        assert variant_plan["anchor_decision_date"] == CALENDAR[45]
        assert variant_plan["decision_start_date"] == CALENDAR[45]
        assert variant_plan["data_start"] == CALENDAR[15]


class TestExecution:
    def test_each_session_receives_anchor_not_data_start(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        outcome = _submit_and_run(service, request)
        resolved = _resolved(service, outcome["batch_id"])
        child_run_ids = {
            variant["run_id"] for variant in resolved["variants"]
        }
        execution_sessions = [
            record
            for record in FakeBatchStrategy.session_log
            if record["run_id"] != "planning"
        ]
        assert len(execution_sessions) == 2
        assert {
            record["run_id"] for record in execution_sessions
        } == child_run_ids
        assert {
            record["lookback_days"]: record["decision_start_date"]
            for record in execution_sessions
        } == {
            30: CALENDAR[95],
            40: CALENDAR[95],
        }

    def test_execution_ordered_by_variant_key_not_request_order(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "label": "second",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "label": "first",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        outcome = _submit_and_run(service, request)
        resolved = _resolved(service, outcome["batch_id"])
        keys = [variant["variant_key"] for variant in resolved["variants"]]
        executed = [
            variant["variant_key"]
            for variant in resolved["executed_order"]
        ]
        assert executed == sorted(keys)

    def test_single_worker_executor(self, tmp_path):
        service = _service(tmp_path)
        assert service.executor._max_workers == 1

    def test_variant_failure_isolates_and_publishes_partial_evidence(
        self,
        tmp_path,
    ):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {
                        "lookback_days": 30,
                        "fail_on_signal": 2,
                    },
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        outcome = _submit_and_run(service, request)
        resolved = _resolved(service, outcome["batch_id"])
        statuses = {
            variant["variant_key"]: variant["status"]
            for variant in resolved["variants"]
        }
        assert "FAILED" in statuses.values()
        assert "SUCCEEDED" in statuses.values()
        assert _state(service, outcome["batch_id"])["stage"] == "PARTIAL_SUCCEEDED"

        failed_variant = next(
            variant
            for variant in resolved["variants"]
            if variant["status"] == "FAILED"
        )
        failed_dir = service.runs_root / failed_variant["run_id"]
        manifest = json.loads(
            (failed_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "FAILED"
        assert manifest["partial"] is True
        assert manifest["publishable_for_comparison"] is False
        assert (failed_dir / "target_decisions.csv").exists()

    def test_snapshot_failure_fails_batch_without_child_runs(self, tmp_path):
        service = _service(tmp_path, fail_frames=True)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ]
        )
        outcome = _submit_and_run(service, request)
        assert _state(service, outcome["batch_id"])["stage"] == "FAILED"
        assert not service.runs_root.exists() or not any(
            service.runs_root.iterdir()
        )

    def test_all_variants_failed_means_batch_failed(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {
                        "lookback_days": 30,
                        "fail_on_signal": 1,
                    },
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {
                        "lookback_days": 40,
                        "fail_on_signal": 1,
                    },
                },
            ]
        )
        outcome = _submit_and_run(service, request)
        assert _state(service, outcome["batch_id"])["stage"] == "FAILED"

    def test_each_variant_has_independent_run_directory(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        _submit_and_run(service, request)
        children = sorted(path.name for path in service.runs_root.iterdir())
        assert len(children) == 2
        for child in service.runs_root.iterdir():
            child_state = json.loads(
                (child / "state.json").read_text(encoding="utf-8")
            )
            assert child_state["stage"] == "SUCCEEDED"
            assert child_state["schema_version"] == "2"


class TestCancellation:
    def test_cancel_before_start_marks_canceled_without_comparison(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ]
        )
        outcome = service.submit_batch(request)
        batch_id = outcome["batch_id"]
        assert service.cancel_batch(batch_id) is True
        service.run_batch_sync(batch_id)
        assert _state(service, batch_id)["stage"] == "CANCELED"
        assert not (
            service.persistence.batch_dir(batch_id) / "manifest.json"
        ).exists()

    def test_cancel_midway_cancels_unstarted_variants(self, tmp_path):
        started = threading.Event()
        release = threading.Event()

        def blocking_frames(snapshot, data_start, data_end):
            started.set()
            release.wait(timeout=5)
            return _frames_loader(snapshot, data_start, data_end)

        service = _service(
            tmp_path,
            frames_loader=blocking_frames,
            auto_start=True,
        )
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                },
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 40},
                },
            ]
        )
        outcome = service.submit_batch(request)
        batch_id = outcome["batch_id"]
        assert started.wait(timeout=5)
        assert service.cancel_batch(batch_id) is True
        release.set()
        service.wait_until_idle(timeout=10)

        resolved = _resolved(service, batch_id)
        statuses = [variant["status"] for variant in resolved["variants"]]
        assert "CANCELED" in statuses
        assert _state(service, batch_id)["stage"] == "CANCELED"
        assert not (
            service.persistence.batch_dir(batch_id) / "manifest.json"
        ).exists()

    def test_cancel_unknown_batch_returns_false(self, tmp_path):
        service = _service(tmp_path)
        assert service.cancel_batch("nope") is False


class TestRecovery:
    def test_startup_marks_non_terminal_batches_interrupted(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ]
        )
        outcome = _submit_and_run(service, request)
        batch_dir = service.persistence.batch_dir(outcome["batch_id"])
        (batch_dir / "manifest.json").unlink()
        state_path = batch_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "RUNNING_STRATEGIES"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        fresh = _service(tmp_path)
        recovered = fresh.recover_interrupted()
        assert outcome["batch_id"] in recovered
        new_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert new_state["stage"] == "FAILED_INTERRUPTED"

    def test_terminal_batches_left_alone(self, tmp_path):
        service = _service(tmp_path)
        request = _request(
            [
                {
                    "strategy_id": "fake_batch",
                    "params": {"lookback_days": 30},
                }
            ]
        )
        outcome = _submit_and_run(service, request)
        fresh = _service(tmp_path)
        recovered = fresh.recover_interrupted()
        assert outcome["batch_id"] not in recovered
        assert _state(service, outcome["batch_id"])["stage"] == "SUCCEEDED"
