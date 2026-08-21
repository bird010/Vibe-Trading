from __future__ import annotations

from pathlib import Path

from backtest.fund_rotation.champion_validation.contracts import ValidationContract
from backtest.fund_rotation.champion_validation.historical_handlers import (
    build_historical_stage_handlers,
    historical_identity,
)


def test_historical_handlers_cover_every_non_final_stage():
    contract = ValidationContract()
    handlers = build_historical_stage_handlers()

    assert set(handlers) == {
        "preflight", "universe", "benchmarks", "ablation", "stability",
        "stress", "attribution", "statistics",
    }
    context = {"contract": contract, "identity": historical_identity()}
    results = {stage: handler({"stage": stage, **context}) for stage, handler in handlers.items()}

    assert results["preflight"]["status"] == "PASS"
    assert results["universe"]["status"] == "INCONCLUSIVE"
    assert all("NO_STAGE_HANDLER" not in result.get("reason_codes", ()) for result in results.values())
    assert Path(results["preflight"]["payload"]["source_result"]).exists()


def test_historical_identity_is_complete_and_binds_r11():
    identity = historical_identity()
    assert identity["strategy_hash"]
    assert identity["data_hash"]
    assert identity["identity_hash"]
