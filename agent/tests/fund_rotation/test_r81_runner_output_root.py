from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


_RUNNER_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "run_r81_combination_batch.py"
)
_SPEC = importlib.util.spec_from_file_location("run_r81_combination_batch", _RUNNER_PATH)
_RUNNER = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_RUNNER)


def test_r81_batch_runner_accepts_output_root():
    args = _RUNNER._parser().parse_args(
        [
            "--idempotency-key",
            "test-key",
            "--champion",
            "champion",
            "--challenger",
            "challenger",
            "--output-root",
            r"C:\custom\r81runs",
        ]
    )

    assert args.output_root == r"C:\custom\r81runs"


def test_r88_only_runner_request_resolves_role_universe_codes():
    from src.stockpred.fund_rotation.batch_models import (
        RESEARCH_ONLY,
        BatchVariantRequest,
        StrategyBatchRequest,
    )
    from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot

    request = StrategyBatchRequest(
        schema_version="1",
        idempotency_key="runner-role-routing-test",
        mode=RESEARCH_ONLY,
        evaluation_start_date="20200103",
        evaluation_end_date="20200110",
        variants=[
            BatchVariantRequest(
                strategy_id="ai_rotation_r88_r81_role_r60_gate", params={}
            ),
        ],
    )
    snapshot = PinnedFundDataSnapshot(
        fund_version=1,
        fund_adj_version=1,
        dim_version=1,
        universe_codes=("E1",),
        role_universe_codes=("E1", "513100.SH"),
        trading_dates=("20200103", "20200110"),
        fingerprint="runner-role-routing",
    )
    dim_fund = pd.DataFrame(
        [
            {"ts_code": "E1", "name": "测试ETF", "instrument_type": "domestic_equity_etf"},
            {"ts_code": "513100.SH", "name": "纳斯达克100ETF(QDII)", "instrument_type": "cross_border_etf"},
        ]
    )

    context = _RUNNER._execution_rule_loader(
        request=request,
        snapshot=snapshot,
        dim_fund=dim_fund,
    )

    assert set(context.instruments) == {"E1", "513100.SH"}


def test_r91_only_runner_request_resolves_role_universe_codes():
    from src.stockpred.fund_rotation.batch_models import RESEARCH_ONLY, BatchVariantRequest, StrategyBatchRequest
    from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot

    request = StrategyBatchRequest(
        schema_version="1", idempotency_key="runner-r91-role-routing-test",
        mode=RESEARCH_ONLY, evaluation_start_date="20200103", evaluation_end_date="20200110",
        variants=[BatchVariantRequest(strategy_id="ai_rotation_r91_r81_role_r73_multi_horizon", params={})],
    )
    snapshot = PinnedFundDataSnapshot(
        fund_version=1, fund_adj_version=1, dim_version=1,
        universe_codes=("E1",), role_universe_codes=("E1", "513100.SH"),
        trading_dates=("20200103", "20200110"), fingerprint="runner-r91-role-routing",
    )
    dim_fund = pd.DataFrame([
        {"ts_code": "E1", "name": "测试ETF", "instrument_type": "domestic_equity_etf"},
        {"ts_code": "513100.SH", "name": "纳斯达克100ETF(QDII)", "instrument_type": "cross_border_etf"},
    ])
    context = _RUNNER._execution_rule_loader(request=request, snapshot=snapshot, dim_fund=dim_fund)
    assert set(context.instruments) == {"E1", "513100.SH"}


def test_r100_only_runner_request_resolves_role_universe_codes():
    from src.stockpred.fund_rotation.batch_models import RESEARCH_ONLY, BatchVariantRequest, StrategyBatchRequest
    from src.stockpred.fund_rotation.data_snapshot import PinnedFundDataSnapshot

    request = StrategyBatchRequest(
        schema_version="1", idempotency_key="runner-r100-role-routing-test",
        mode=RESEARCH_ONLY, evaluation_start_date="20200103", evaluation_end_date="20200110",
        variants=[BatchVariantRequest(strategy_id="ai_rotation_r100_r81_r88_invvol_slots", params={})],
    )
    snapshot = PinnedFundDataSnapshot(
        fund_version=1, fund_adj_version=1, dim_version=1,
        universe_codes=("E1",), role_universe_codes=("E1", "513100.SH"),
        trading_dates=("20200103", "20200110"), fingerprint="runner-r100-role-routing",
    )
    dim_fund = pd.DataFrame([
        {"ts_code": "E1", "name": "测试ETF", "instrument_type": "domestic_equity_etf"},
        {"ts_code": "513100.SH", "name": "纳斯达克100ETF(QDII)", "instrument_type": "cross_border_etf"},
    ])
    context = _RUNNER._execution_rule_loader(request=request, snapshot=snapshot, dim_fund=dim_fund)
    assert set(context.instruments) == {"E1", "513100.SH"}
