"""Phase 6 Task 1 — architectural guard: no legacy create callers remain.

After cutover, only the strategy batch API creates new runs. The old
POST /backtests and GET /defaults handlers must not exist, and no
production code may call the legacy pipeline entry point directly.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _route_handler_names(source: str) -> set[str]:
    """Extract FastAPI route handler names from source code."""
    names: set[str] = set()
    for m in re.finditer(r"def (\w+)\(.*?\):", source):
        names.add(m.group(1))
    return names


def _function_names(source: str) -> set[str]:
    """Extract top-level function names."""
    names: set[str] = set()
    for m in re.finditer(r"^def (\w+)", source, re.MULTILINE):
        names.add(m.group(1))
    return names


class TestNoLegacyCreateEndpoints:
    def test_no_post_backtests_in_routes(self):
        """POST /stockpred/fund-rotation/backtests must be removed."""
        routes = PROJECT_ROOT / "agent/src/api/fund_rotation_routes.py"
        source = routes.read_text(encoding="utf-8")

        # The old create_backtest handler must not exist.
        assert 'def create_backtest(' not in source, (
            "POST /stockpred/fund-rotation/backtests handler still present"
        )
        # The old defaults handler must not exist.
        assert 'def get_defaults(' not in source, (
            "GET /stockpred/fund-rotation/defaults still present"
        )

    def test_no_submit_backtest_in_service(self):
        """The old write-side service method must be gone."""
        svc = PROJECT_ROOT / "agent/src/stockpred/fund_rotation/service.py"
        source = svc.read_text(encoding="utf-8")
        assert "def submit_backtest(" not in source, (
            "submit_backtest method still in FundRotationBacktestService"
        )
        assert "def get_defaults(" not in source, (
            "get_defaults method still in FundRotationBacktestService"
        )

    def test_no_structured_error_import_in_routes(self):
        """StructuredError was only used by the deleted create handler."""
        routes = PROJECT_ROOT / "agent/src/api/fund_rotation_routes.py"
        source = routes.read_text(encoding="utf-8")
        assert "StructuredError" not in source, (
            "StructuredError import still in routes after handler removal"
        )

    def test_read_only_backtest_endpoints_remain(self):
        """Legacy GET /backtests endpoints must be preserved for v1 read-only."""
        routes = PROJECT_ROOT / "agent/src/api/fund_rotation_routes.py"
        source = routes.read_text(encoding="utf-8")
        assert 'def list_backtests(' in source, "GET /backtests list endpoint removed"
        assert 'def get_backtest(' in source, "GET /backtests/{id} endpoint removed"
        assert 'def stream_events(' in source, "GET /backtests/{id}/events endpoint removed"
        assert 'def get_artifact(' in source, "GET /backtests/{id}/artifacts endpoint removed"
        assert 'def get_instrument_chart(' in source, "chart endpoint removed"

    def test_batch_create_endpoint_exists(self):
        """POST /strategy-batches must be the only create path."""
        routes = PROJECT_ROOT / "agent/src/api/fund_rotation_routes.py"
        source = routes.read_text(encoding="utf-8")
        assert 'def submit_strategy_batch(' in source, (
            "POST /strategy-batches endpoint missing"
        )
        assert '/strategy-batches' in source, (
            "strategy-batches route path missing"
        )


class TestNoLegacyPipelineInProduction:
    def test_run_signal_pipeline_not_called_in_src(self):
        """run_signal_pipeline must not be called from src/ (production layer)."""
        src_dir = PROJECT_ROOT / "agent/src"
        for py_file in src_dir.rglob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            source = py_file.read_text(encoding="utf-8")
            calls = re.findall(r"run_signal_pipeline\(", source)
            assert len(calls) == 0, (
                f"run_signal_pipeline called in {py_file.relative_to(PROJECT_ROOT)}"
            )

    def test_fund_rotation_config_not_imported_in_src(self):
        """FundRotationConfig must not be imported in src/ (production layer)."""
        src_dir = PROJECT_ROOT / "agent/src"
        for py_file in src_dir.rglob("*.py"):
            if py_file.name.startswith("test_"):
                continue
            source = py_file.read_text(encoding="utf-8")
            imports = re.findall(r"from backtest\.fund_rotation\.config import", source)
            assert len(imports) == 0, (
                f"FundRotationConfig imported in {py_file.relative_to(PROJECT_ROOT)}"
            )
