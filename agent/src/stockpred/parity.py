"""Layered parity reports for frozen StockPred Graph golden bundles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class ParityReport:
    passed: bool
    missing_keys: list[tuple[str, ...]] = field(default_factory=list)
    extra_keys: list[tuple[str, ...]] = field(default_factory=list)
    max_abs_diff: dict[str, float] = field(default_factory=dict)
    mismatched_columns: list[str] = field(default_factory=list)
    layers: dict[str, "ParityReport"] = field(default_factory=dict)
    summary: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


class BacktestComparable(Protocol):
    signals: pd.DataFrame
    selected: pd.DataFrame
    trades: pd.DataFrame
    equity: pd.DataFrame
    metrics: Mapping[str, float]


_SIGNAL_COLUMN_ATOL: Mapping[str, float] = {
    "score": 0.40,
    "base_score": 0.40,
    "confidence": 0.04,
    "f_rel_str": 0.25,
    "f_reversal": 0.041,
    "position_weight": 2e-6,
}

_SELECTED_COLUMN_ATOL: Mapping[str, float] = {
    "f_reversal": 0.021,
    "position_weight": 2e-6,
}


def _key_frame(frame: pd.DataFrame, keys: tuple[str, ...]) -> pd.DataFrame:
    result = frame.copy()
    for key in keys:
        if key not in result.columns:
            raise KeyError(key)
        result[key] = result[key].astype(str)
    return result


def _key_tuples(frame: pd.DataFrame, keys: tuple[str, ...]) -> list[tuple[str, ...]]:
    return [tuple(row) for row in frame.loc[:, list(keys)].itertuples(index=False, name=None)]


def compare_signal_frames(
    expected: pd.DataFrame,
    actual: pd.DataFrame,
    *,
    keys: tuple[str, ...],
    numeric_columns: tuple[str, ...],
    exact_columns: tuple[str, ...] = (),
    column_atol: Mapping[str, float] | None = None,
    rtol: float = 1e-8,
    atol: float = 1e-10,
) -> ParityReport:
    """Compare keyed frames with exact keys and tolerant numeric values."""
    try:
        expected_frame = _key_frame(expected, keys)
        actual_frame = _key_frame(actual, keys)
    except KeyError as exc:
        return ParityReport(False, summary=f"missing key column: {exc.args[0]}")
    expected_keys = set(_key_tuples(expected_frame, keys))
    actual_keys = set(_key_tuples(actual_frame, keys))
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    mismatched: list[str] = []
    max_diff: dict[str, float] = {}
    duplicate_keys = expected_frame.duplicated(list(keys)).any() or actual_frame.duplicated(list(keys)).any()
    if duplicate_keys:
        mismatched.append("duplicate_keys")

    common = sorted(expected_keys & actual_keys)
    common_index: list[object] = (
        [key[0] for key in common] if len(keys) == 1 else list(common)
    )
    expected_indexed = expected_frame.set_index(list(keys))
    actual_indexed = actual_frame.set_index(list(keys))
    for column in numeric_columns:
        if column not in expected_indexed.columns or column not in actual_indexed.columns:
            mismatched.append(column)
            max_diff[column] = float("inf")
            continue
        expected_values = pd.to_numeric(
            expected_indexed.loc[common_index, column], errors="coerce"
        ).to_numpy(dtype=float)
        actual_values = pd.to_numeric(
            actual_indexed.loc[common_index, column], errors="coerce"
        ).to_numpy(dtype=float)
        differences = np.abs(expected_values - actual_values)
        max_diff[column] = float(np.nanmax(differences)) if len(differences) else 0.0
        effective_atol = (column_atol or {}).get(column, atol)
        if not np.allclose(
            expected_values,
            actual_values,
            rtol=rtol,
            atol=effective_atol,
            equal_nan=True,
        ):
            mismatched.append(column)
    for column in exact_columns:
        if column not in expected_indexed.columns or column not in actual_indexed.columns:
            mismatched.append(column)
            continue
        expected_values = (
            expected_indexed.loc[common_index, column].fillna("").astype(str)
        )
        actual_values = actual_indexed.loc[common_index, column].fillna("").astype(str)
        if expected_values.tolist() != actual_values.tolist():
            mismatched.append(column)
    passed = not missing and not extra and not mismatched
    return ParityReport(
        passed=passed,
        missing_keys=missing,
        extra_keys=extra,
        max_abs_diff=max_diff,
        mismatched_columns=sorted(set(mismatched)),
        summary="passed" if passed else "frame parity failed",
    )


def _missing_layer(path: Path) -> ParityReport:
    return ParityReport(False, summary=f"missing golden file: {path.name}")


def _numeric_intersection(
    expected: pd.DataFrame,
    actual: pd.DataFrame,
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    candidates = (
        set(expected.columns)
        & set(actual.select_dtypes(include=[np.number]).columns)
        - set(keys)
    )
    numeric: list[str] = []
    for column in sorted(candidates):
        converted = pd.to_numeric(expected[column], errors="coerce")
        if converted.notna().sum() == expected[column].notna().sum():
            numeric.append(column)
    return tuple(numeric)


def _compare_metrics(
    expected: Mapping[str, object],
    actual: Mapping[str, float],
    *,
    atol: float = 1e-6,
) -> ParityReport:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    differences: dict[str, float] = {}
    mismatched: list[str] = []
    for key in sorted(set(expected) & set(actual)):
        try:
            expected_value = float(expected[key])
            actual_value = float(actual[key])
        except (TypeError, ValueError):
            if expected[key] != actual[key]:
                mismatched.append(key)
            continue
        difference = abs(expected_value - actual_value)
        differences[key] = difference
        if not np.isclose(expected_value, actual_value, rtol=1e-8, atol=atol):
            mismatched.append(key)
    passed = not missing and not extra and not mismatched
    return ParityReport(
        passed,
        missing_keys=[(key,) for key in missing],
        extra_keys=[(key,) for key in extra],
        max_abs_diff=differences,
        mismatched_columns=mismatched,
        summary="passed" if passed else "metrics parity failed",
    )


def _frame_for(result: BacktestComparable, name: str) -> pd.DataFrame:
    explicit = getattr(result, f"parity_{name}", None)
    if isinstance(explicit, pd.DataFrame):
        return explicit
    return getattr(result, name)


def _metrics_for(result: BacktestComparable) -> Mapping[str, float]:
    explicit = getattr(result, "parity_metrics", None)
    if explicit is not None:
        return explicit
    return result.metrics


def compare_backtest_bundle(
    golden_dir: Path,
    result: BacktestComparable,
) -> ParityReport:
    """Compare the five executable layers of a golden backtest bundle."""
    root = Path(golden_dir)
    paths = {
        "signals": root / "details.parquet",
        "selected": root / "selected.csv",
        "trades": root / "trades.csv",
        "equity": root / "equity.csv",
        "metrics": root / "metrics.json",
    }
    layers: dict[str, ParityReport] = {}
    if paths["signals"].is_file():
        expected = pd.read_parquet(paths["signals"])
        actual = _frame_for(result, "signals")
        keys = ("trade_date", "ts_code")
        layers["signals"] = compare_signal_frames(
            expected,
            actual,
            keys=keys,
            numeric_columns=_numeric_intersection(expected, actual, keys),
            column_atol=_SIGNAL_COLUMN_ATOL,
        )
    else:
        layers["signals"] = _missing_layer(paths["signals"])
    if paths["selected"].is_file():
        expected = pd.read_csv(paths["selected"], dtype=str)
        actual = _frame_for(result, "selected").copy()
        numeric = _numeric_intersection(
            expected,
            actual,
            ("trade_date", "ts_code"),
        )
        layers["selected"] = compare_signal_frames(
            expected,
            actual,
            keys=("trade_date", "ts_code"),
            numeric_columns=numeric,
            column_atol=_SELECTED_COLUMN_ATOL,
        )
    else:
        layers["selected"] = _missing_layer(paths["selected"])
    if paths["trades"].is_file():
        expected = pd.read_csv(paths["trades"], dtype=str)
        actual = _frame_for(result, "trades").copy()
        if "price" in expected.columns:
            expected["price"] = pd.to_numeric(expected["price"], errors="coerce").round(2)
        if "price" in actual.columns:
            actual["price"] = pd.to_numeric(actual["price"], errors="coerce").round(2)
        trade_keys = tuple(
            key
            for key in ("timestamp", "code", "side", "signal_date")
            if key in expected.columns and key in actual.columns
        )
        numeric = tuple(
            column for column in ("price", "exit_delay_days") if column in expected.columns and column in actual.columns
        )
        exact = tuple(
            column for column in ("status", "reason") if column in expected.columns and column in actual.columns
        )
        layers["trades"] = compare_signal_frames(
            expected,
            actual,
            keys=trade_keys,
            numeric_columns=numeric,
            exact_columns=exact,
        )
    else:
        layers["trades"] = _missing_layer(paths["trades"])
    if paths["equity"].is_file():
        expected = pd.read_csv(paths["equity"])
        actual = _frame_for(result, "equity")
        layers["equity"] = compare_signal_frames(
            expected,
            actual,
            keys=("time",),
            numeric_columns=("equity",),
        )
    else:
        layers["equity"] = _missing_layer(paths["equity"])
    if paths["metrics"].is_file():
        expected_metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
        layers["metrics"] = _compare_metrics(expected_metrics, _metrics_for(result))
    else:
        layers["metrics"] = _missing_layer(paths["metrics"])
    passed = all(layer.passed for layer in layers.values())
    return ParityReport(
        passed=passed,
        layers=layers,
        summary="passed" if passed else "one or more parity layers failed",
    )
