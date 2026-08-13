"""Strategy-neutral Runner tests — Phase 2 Task 3 (design §5/§6/§7/§24/§26).

Covers SET_TARGETS / HOLD_TARGETS / INVALID handling, cancellation, contract
violations, undeclared data access, evaluation/finalize failures, and the
warmup boundary. The fake strategies exercise the Runner through the public
contracts only; the Runner must stay strategy-agnostic.
"""

import inspect

import pandas as pd
import pytest
from pydantic import BaseModel

from backtest.fund_rotation import runner as runner_module
from backtest.fund_rotation.contracts import (
    DecisionKind,
    FundRotationStrategyDescriptor,
    QualityStatus,
    StrategyDataRequirements,
    StrategyDiagnostics,
    TargetWeightDecision,
)
from backtest.fund_rotation.evaluation import EvaluationContext
from backtest.fund_rotation.pit_universe import PITQueryMode
from backtest.fund_rotation.runner import (
    CancellationToken,
    ExecutionConfig,
    FundRotationBacktestRunner,
    FundRotationRunResult,
    SubRunStatus,
)
from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot
from tests.fund_rotation.conftest import make_test_market_rule_inputs


# ── synthetic market ──

MARKET_DATES = pd.bdate_range("2024-01-02", "2024-02-02").strftime("%Y%m%d").tolist()
# Weekly warmup boundary aligns with ISO week-endings: warmup=5 days means
# 5//5 + 1 = 2 week-endings are needed, so the first decision date is the
# second week-ending (Friday 20240112).
SIMULATION_START = "20240112"


def _market_frames():
    rows, adj = [], []
    for code in ("A", "B"):
        for date in MARKET_DATES:
            rows.append({
                "ts_code": code, "trade_date": date,
                "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9,
                "pre_close": 10.0, "vol": 1_000_000, "amount": 10_000_000.0,
            })
            adj.append({"ts_code": code, "trade_date": date, "adj_factor": 1.0})
    fund_daily = pd.DataFrame(rows)
    fund_adj = pd.DataFrame(adj)
    dim_fund = pd.DataFrame([
        {"ts_code": "A", "name": "ETF Alpha", "list_date": "20200101"},
        {"ts_code": "B", "name": "ETF Beta", "list_date": "20200101"},
    ])
    return fund_daily, fund_adj, dim_fund


def _snapshot():
    return PinnedFundDataSnapshot(
        fund_version=1, fund_adj_version=1, dim_version=1,
        universe_codes=("A", "B"), trading_dates=tuple(MARKET_DATES),
        fingerprint="test-fingerprint",
    )


def _evaluation():
    return EvaluationContext.from_range(MARKET_DATES, "20240115", "20240131")


def _execution():
    return ExecutionConfig(
        initial_capital=100_000, adv_min_observations=3, max_participation_rate=0.05,
    )


# ── fake strategy plumbing ──

class FakeConfig(BaseModel):
    pass


def _decision(signal_date, action, weights=None, cash=1.0, reason="", seq=0):
    return TargetWeightDecision(
        decision_id=f"{signal_date}-{seq}",
        signal_date=signal_date,
        action=action,
        target_weights=dict(weights or {}),
        cash_weight=cash,
        reason_code=reason,
        quality_status=QualityStatus.VALID,
    )


class FakeSession:
    """Scripted session: date -> decision, Exception instance, or callable."""

    def __init__(self, scripts, scheduled=None, finalize_error=None):
        self.scripts = dict(scripts)
        self._scheduled = scheduled
        self.finalize_error = finalize_error
        self.evaluate_calls: list[str] = []
        self.scheduled_arguments = None

    def scheduled_dates(self, calendar, simulation_start_date, evaluation_end_date):
        self.scheduled_arguments = (calendar, simulation_start_date, evaluation_end_date)
        if self._scheduled is not None:
            return tuple(self._scheduled)
        # Default: Fridays within [simulation_start, evaluation_end].
        return tuple(
            d for d in calendar
            if simulation_start_date <= d <= evaluation_end_date
            and pd.Timestamp(d).dayofweek == 4
        )

    def evaluate(self, context):
        self.evaluate_calls.append(context.signal_date)
        item = self.scripts.get(context.signal_date)
        if item is None:
            return _decision(context.signal_date, DecisionKind.HOLD_TARGETS, seq=99)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(context)
        return item

    def finalize(self):
        if self.finalize_error is not None:
            raise self.finalize_error
        return StrategyDiagnostics()


class FakeStrategy:
    def __init__(self, session, requirements=None):
        self._session = session
        self._requirements = requirements or StrategyDataRequirements(
            required_datasets=("fund", "fact_fund_adj", "dim_fund"),
            required_fields=(
                "ts_code", "trade_date", "name", "list_date", "close", "amount",
                "adj_factor",
            ),
            warmup_trade_days=5,
            frequency="weekly",
            needs_benchmark=False,
        )

    descriptor = FundRotationStrategyDescriptor(
        id="fake-strategy", name="Fake", description="test double",
        interface_version="1.0", supported_universe=("cn_etf",), deterministic=True,
    )
    config_model = FakeConfig

    def resolve_requirements(self, config):
        return self._requirements

    def create_session(self, initialization, config):
        self.initialization = initialization
        return self._session


def _run(session, *, requirements=None, token=None, runner_run_id=None,
         simulation_start_date=None, run_id=None, dim_fund=None):
    fund_daily, fund_adj, default_dim_fund = _market_frames()
    rule_resolver, rule_instruments = make_test_market_rule_inputs(("A", "B"))
    runner = FundRotationBacktestRunner(
        fund_daily, fund_adj, dim_fund if dim_fund is not None else default_dim_fund,
        market_rule_resolver=rule_resolver,
        market_rule_instruments=rule_instruments,
        market_rule_mode=PITQueryMode.AS_WAS_KNOWN,
        run_id=runner_run_id,
    )
    strategy = FakeStrategy(session, requirements)
    run_kwargs = {}
    if simulation_start_date is not None:
        run_kwargs["simulation_start_date"] = simulation_start_date
    if run_id is not None:
        run_kwargs["run_id"] = run_id
    result = runner.run(
        strategy=strategy,
        config=FakeConfig(),
        snapshot=_snapshot(),
        evaluation=_evaluation(),
        execution=_execution(),
        cancellation=token or CancellationToken(),
        **run_kwargs,
    )
    return result, strategy


# ── SET_TARGETS ──

def test_set_targets_drives_execution_and_succeeds():
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 0.5, "B": 0.5}, cash=0.0),
    })
    result, _ = _run(session)

    assert isinstance(result, FundRotationRunResult)
    assert result.status is SubRunStatus.SUCCEEDED
    assert result.weekly_targets == {"20240112": {"A": 0.5, "B": 0.5}}
    assert len(result.decisions) == 3  # 01-12, 01-19, 01-26 (02-02 > eval end)
    # Executed over the full evaluation calendar starting from initial NAV.
    assert result.executed_equity.index[0] == "20240115"
    assert result.executed_equity.index[-1] == "20240131"
    # Day-1 equity reflects the opening fill (fees/slippage), anchored near NAV.
    assert result.executed_equity.iloc[0] == pytest.approx(1.0, abs=0.05)
    assert any(e.get("filled", 0) > 0 for e in result.trade_events)
    assert result.orders and result.positions_history
    assert result.strategy_metrics  # metrics computed on the executed equity


# ── HOLD_TARGETS ──

def test_hold_before_any_set_keeps_cash_and_creates_no_events():
    session = FakeSession({})  # every date -> HOLD
    result, _ = _run(session)

    assert result.status is SubRunStatus.SUCCEEDED
    assert result.weekly_targets == {}
    assert result.trade_events == []
    # Cash hold over the whole evaluation interval at initial NAV.
    assert len(result.executed_equity) == 13  # 20240115..20240131 trading days
    assert (result.executed_equity == 1.0).all()


def test_hold_after_set_keeps_previous_targets_without_new_events():
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 1.0}, cash=0.0),
    })
    result, _ = _run(session)

    assert result.status is SubRunStatus.SUCCEEDED
    # One target event only; HOLDs do not add or recompute orders.
    assert list(result.weekly_targets) == ["20240112"]
    assert len(result.decisions) == 3


def test_hold_days_retry_residual_under_original_parent_order():
    # Capacity-constrained market: ADV permits only ~500 shares/day, so the
    # 10k-share target fills over several days. HOLD decisions must NOT create,
    # replace, cancel or recompute orders — the residual keeps its parent id.
    fund_daily, fund_adj, dim_fund = _market_frames()
    fund_daily = fund_daily.assign(amount=100.0)  # ADV = 100 * 1000 = 100k
    rule_resolver, rule_instruments = make_test_market_rule_inputs(("A", "B"))
    runner = FundRotationBacktestRunner(
        fund_daily,
        fund_adj,
        dim_fund,
        market_rule_resolver=rule_resolver,
        market_rule_instruments=rule_instruments,
        market_rule_mode=PITQueryMode.AS_WAS_KNOWN,
    )
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 1.0}, cash=0.0),
    })
    result = runner.run(
        strategy=FakeStrategy(session),
        config=FakeConfig(),
        snapshot=_snapshot(),
        evaluation=_evaluation(),
        execution=ExecutionConfig(
            initial_capital=100_000, adv_min_observations=3,
            max_participation_rate=0.05,
        ),
        cancellation=CancellationToken(),
    )

    assert result.status is SubRunStatus.SUCCEEDED
    buys = [
        e for e in result.trade_events
        if e["ts_code"] == "A" and e["action"] == "BUY" and e.get("filled", 0) > 0
    ]
    # Partial fills spread across several execution days...
    assert len({e["trade_date"] for e in buys}) >= 2
    # ...but every retry belongs to the single original parent order (§7.2).
    assert len({e["order_id"] for e in buys}) == 1
    assert list(result.weekly_targets) == ["20240112"]  # HOLDs added no target


# ── INVALID ──

def test_invalid_after_warmup_terminates_subrun():
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 1.0}, cash=0.0),
        "20240119": _decision("20240119", DecisionKind.INVALID,
                              reason="DATA_MISSING"),
    })
    result, _ = _run(session)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "DATA_MISSING"
    assert len(result.decisions) == 2
    assert result.executed_equity.empty  # terminated before execution


def test_invalid_before_evaluation_also_terminates():
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.INVALID,
                              reason="INSUFFICIENT_CAUSAL_HISTORY"),
    })
    result, _ = _run(session)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "INSUFFICIENT_CAUSAL_HISTORY"


# ── warmup boundary ──

def test_pure_warmup_period_never_calls_evaluate():
    session = FakeSession({})
    result, _ = _run(session)

    assert result.status is SubRunStatus.SUCCEEDED
    calendar, sim_start, eval_end = session.scheduled_arguments
    assert sim_start == SIMULATION_START
    assert eval_end == "20240131"
    assert session.evaluate_calls, "post-warmup decisions must be evaluated"
    assert all(d >= SIMULATION_START for d in session.evaluate_calls)


def test_scheduled_date_before_warmup_is_contract_violation():
    session = FakeSession({}, scheduled=("20240102",))  # inside pure warmup
    result, _ = _run(session)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "STRATEGY_CONTRACT_VIOLATION"


# ── cancellation ──

def test_cancellation_at_decision_checkpoint_cancels_subrun():
    token = CancellationToken()

    def cancel_after_first(context):
        decision = _decision(context.signal_date, DecisionKind.SET_TARGETS,
                             {"A": 1.0}, cash=0.0)
        token.cancel()
        return decision

    session = FakeSession({"20240112": cancel_after_first})
    result, _ = _run(session, token=token)

    assert result.status is SubRunStatus.CANCELED
    assert len(result.decisions) == 1


class DelayedToken:
    """Flips to cancelled after a fixed number of is_cancelled checks."""

    def __init__(self, flip_after: int):
        self._flip_after = flip_after
        self._checks = 0

    @property
    def is_cancelled(self):
        self._checks += 1
        return self._checks > self._flip_after

    def cancel(self):
        self._flip_after = 0


def test_cancellation_during_execution_preserves_partial_events():
    # Check order: 1 start + 3 decision days + 1 pre-execution, then daily.
    # flip_after=7 therefore stops the loop at the start of execution day 3.
    token = DelayedToken(flip_after=7)
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 1.0}, cash=0.0),
    })
    result, _ = _run(session, token=token)

    assert result.status is SubRunStatus.CANCELED
    assert result.error_code == "CANCELED"
    assert len(result.decisions) == 3
    assert result.trade_events  # partial evidence preserved
    assert len(result.executed_equity) == 2  # only completed execution days
    assert result.executed_equity.index[-1] == "20240116"


# ── contract violations and data-access enforcement ──

def test_weights_not_summing_to_one_is_contract_violation():
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 0.9}, cash=1.0),
    })
    result, _ = _run(session)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "STRATEGY_CONTRACT_VIOLATION"


def test_undeclared_data_access_fails_subrun():
    requirements = StrategyDataRequirements(
        required_datasets=("fund",),  # fact_fund_adj NOT declared
        required_fields=("ts_code", "trade_date", "close", "amount"),
        warmup_trade_days=5,
        frequency="weekly",
        needs_benchmark=False,
    )

    def read_undeclared(context):
        context.data_view.fund_adjustments()  # requires fact_fund_adj
        return _decision(context.signal_date, DecisionKind.HOLD_TARGETS)

    session = FakeSession({"20240112": read_undeclared})
    result, _ = _run(session, requirements=requirements)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "UNDECLARED_STRATEGY_DATA_ACCESS"


def test_evaluate_exception_preserves_prior_decisions_and_fails():
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"A": 1.0}, cash=0.0),
        "20240119": RuntimeError("boom"),
    })
    result, _ = _run(session)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "STRATEGY_EVALUATION_ERROR"
    assert len(result.decisions) == 1
    assert "20240112" in result.weekly_targets


def test_finalize_failure_preserves_events_and_does_not_fake_success():
    session = FakeSession(
        {"20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                               {"A": 1.0}, cash=0.0)},
        finalize_error=RuntimeError("finalize exploded"),
    )
    result, _ = _run(session)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "FINALIZE_FAILED"
    assert not result.executed_equity.empty
    assert result.trade_events  # prior events preserved


def test_runner_uses_planned_start_date_and_passes_external_run_id_to_session():
    session = FakeSession({}, scheduled=("20240119",))

    result, strategy = _run(
        session,
        simulation_start_date="20240119",
        run_id="external-variant-run",
    )

    assert result.status is SubRunStatus.SUCCEEDED
    assert session.scheduled_arguments[1] == "20240119"
    assert session.evaluate_calls == ["20240119"]
    assert strategy.initialization.run_id == "external-variant-run"


def test_runner_rejects_targets_not_eligible_on_signal_date():
    _, _, dim_fund = _market_frames()
    dim_fund.loc[dim_fund["ts_code"] == "B", "list_date"] = "20240120"
    session = FakeSession({
        "20240112": _decision("20240112", DecisionKind.SET_TARGETS,
                              {"B": 1.0}, cash=0.0),
    })

    result, _ = _run(session, dim_fund=dim_fund)

    assert result.status is SubRunStatus.FAILED
    assert result.error_code == "STRATEGY_CONTRACT_VIOLATION"


# ── strategy-agnostic guard ──

def test_runner_has_no_strategy_specific_branches():
    source = inspect.getsource(runner_module)
    assert "correlation" not in source.lower()
    assert "strategy_id" not in source
