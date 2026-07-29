"""Outcome-independent exposure cohorts for Graph failure patterns."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd


PREDICTABLE_REASON_CONFIDENCE = {
    "crowded_reversal": 0.8,
    "flow_price_divergence": 0.8,
    "margin_unwind": 0.8,
    "insider_selling": 0.7,
    "pledge_pressure": 0.7,
    "valuation_fundamental": 0.7,
    "graph_propagation_error": 0.8,
    "retail_distribution_weak_momentum": 0.8,
    "data_quality": 0.7,
}

PREDICTABLE_REASON_REQUIRED_FIELDS = {
    "crowded_reversal": ("crowding_score",),
    "flow_price_divergence": ("net_big_inflow_5d", "f_rel_str"),
    "margin_unwind": ("margin_balance_change_5d",),
    "insider_selling": ("holder_sell_ratio_90d",),
    "pledge_pressure": ("pledge_amount_180d",),
    "valuation_fundamental": ("financial_profit_growth", "pe_ttm_percentile"),
    "graph_propagation_error": ("f_neighbor", "f_rel_str", "f_moneyflow"),
    "retail_distribution_weak_momentum": ("f_short_mom", "holder_num_change"),
    "data_quality": ("missing_feature_count",),
}

EXPOSURE_MATCH_COLUMNS = [
    "exposed_ts_code",
    "control_ts_code",
    "trade_date",
    "reason_code",
    "same_industry",
    "distance",
]

EXPOSURE_COLUMNS = [
    "reason_code",
    "exposure_support",
    "exposure_dates",
    "exposure_control_coverage",
    "paired_return_delta",
    "paired_loss_rate_delta",
    "underperforming_folds",
    "exposure_eligible",
]

MIN_EXPOSURE_SUPPORT = 30
MIN_EXPOSURE_DATES = 8
MIN_EXPOSURE_CONTROL_COVERAGE = 0.80
MIN_UNDERPERFORMING_FOLDS = 2


def _require(frame: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{name} missing required columns: {missing}")


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _number(row: Mapping[str, object], name: str, default: float) -> float:
    value = _finite(row.get(name))
    return default if value is None else value


def _normalized_date(value: object) -> str | None:
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (int, float, np.number)):
        number = float(value)
        if not np.isfinite(number) or not number.is_integer():
            return None
        value = str(int(number))
    parsed = pd.to_datetime(str(value), errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y%m%d")


def predictable_reason_codes(row: Mapping[str, object]) -> tuple[str, ...]:
    """Return deterministic pretrade reason codes without reading outcomes."""
    reasons: list[str] = []
    if _number(row, "crowding_score", 0.0) >= 0.8:
        reasons.append("crowded_reversal")
    if _number(row, "net_big_inflow_5d", 0.0) < 0.0 and _number(
        row, "f_rel_str", 10.0
    ) >= 15.0:
        reasons.append("flow_price_divergence")
    if _number(row, "margin_balance_change_5d", 0.0) <= -0.1:
        reasons.append("margin_unwind")
    if _number(row, "holder_sell_ratio_90d", 0.0) > 0.0:
        reasons.append("insider_selling")
    if _number(row, "pledge_amount_180d", 0.0) > 0.0:
        reasons.append("pledge_pressure")
    if _number(row, "financial_profit_growth", 0.0) < 0.0 and _number(
        row, "pe_ttm_percentile", 0.5
    ) >= 0.8:
        reasons.append("valuation_fundamental")
    if (
        _number(row, "f_neighbor", 10.0) >= 15.0
        and _number(row, "f_rel_str", 10.0) <= 10.0
        and _number(row, "f_moneyflow", 10.0) <= 10.0
    ):
        reasons.append("graph_propagation_error")
    if _number(row, "f_short_mom", 10.0) <= 10.0 and _number(
        row, "holder_num_change", 0.0
    ) > 0.0:
        reasons.append("retail_distribution_weak_momentum")
    if _number(row, "missing_feature_count", 0.0) >= 3.0:
        reasons.append("data_quality")
    return tuple(reasons)


def reason_exposure_mask(
    cases: pd.DataFrame,
    reason_code: str,
) -> pd.Series:
    """Identify all Top cases exposed to one pretrade reason."""
    if reason_code not in PREDICTABLE_REASON_CONFIDENCE:
        return pd.Series(False, index=cases.index, dtype=bool)
    matched = cases.apply(
        lambda row: reason_code in predictable_reason_codes(row),
        axis=1,
    ).astype(bool)
    return matched & reason_signal_available_mask(cases, reason_code)


def reason_signal_available_mask(
    cases: pd.DataFrame,
    reason_code: str,
) -> pd.Series:
    required = PREDICTABLE_REASON_REQUIRED_FIELDS.get(reason_code)
    if required is None:
        return pd.Series(False, index=cases.index, dtype=bool)
    available = pd.Series(True, index=cases.index, dtype=bool)
    for column in required:
        if column not in cases.columns:
            return pd.Series(False, index=cases.index, dtype=bool)
        numeric = pd.to_numeric(cases[column], errors="coerce")
        available &= numeric.notna() & np.isfinite(numeric)
        missing_column = f"{column}_missing"
        if missing_column in cases.columns:
            available &= ~cases[missing_column].eq(True)
    return available


def match_exposure_controls(
    cases: pd.DataFrame,
    reason_code: str,
    *,
    controls_per_case: int = 3,
) -> pd.DataFrame:
    """Match exposed cases to same-day unexposed controls without outcomes."""
    if (
        isinstance(controls_per_case, bool)
        or not isinstance(controls_per_case, int)
        or controls_per_case <= 0
    ):
        raise ValueError("controls_per_case must be a positive integer")
    _require(
        cases,
        {"ts_code", "trade_date", "industry", "circ_mv", "score"},
        "cases",
    )
    prepared = cases.copy(deep=True)
    prepared["_trade_date"] = prepared["trade_date"].map(_normalized_date)
    prepared["_available"] = reason_signal_available_mask(
        prepared, reason_code
    )
    prepared["_exposed"] = reason_exposure_mask(prepared, reason_code)
    rows: list[dict[str, object]] = []
    for _, exposed in prepared[prepared["_exposed"]].iterrows():
        exposed_date = exposed["_trade_date"]
        exposed_size = _finite(exposed["circ_mv"])
        exposed_score = _finite(exposed["score"])
        if exposed_date is None or exposed_size is None or exposed_score is None:
            continue
        controls = prepared[
            prepared["_trade_date"].eq(exposed_date)
            & prepared["_available"]
            & ~prepared["_exposed"]
            & prepared["ts_code"].astype(str).ne(str(exposed["ts_code"]))
        ]
        ranked: list[tuple[tuple[object, ...], pd.Series, float, bool]] = []
        for _, control in controls.iterrows():
            control_size = _finite(control["circ_mv"])
            control_score = _finite(control["score"])
            if control_size is None or control_score is None:
                continue
            same_industry = bool(
                pd.notna(exposed["industry"])
                and pd.notna(control["industry"])
                and exposed["industry"] == control["industry"]
            )
            size_distance = abs(control_size - exposed_size) / max(
                abs(exposed_size), 1.0
            )
            score_distance = abs(control_score - exposed_score) / max(
                abs(exposed_score), 1.0
            )
            liquidity_distance = 0.0
            if "f_liquidity" in prepared.columns:
                exposed_liquidity = _finite(exposed.get("f_liquidity"))
                control_liquidity = _finite(control.get("f_liquidity"))
                if (
                    exposed_liquidity is not None
                    and control_liquidity is not None
                ):
                    liquidity_distance = abs(
                        control_liquidity - exposed_liquidity
                    ) / max(abs(exposed_liquidity), 1.0)
            distance = (
                (0.0 if same_industry else 10.0)
                + size_distance
                + score_distance
                + liquidity_distance
            )
            sort_key = (
                not same_industry,
                size_distance,
                score_distance,
                liquidity_distance,
                str(control["ts_code"]),
            )
            ranked.append((sort_key, control, distance, same_industry))
        for _, control, distance, same_industry in sorted(
            ranked, key=lambda item: item[0]
        )[:controls_per_case]:
            rows.append(
                {
                    "exposed_ts_code": str(exposed["ts_code"]),
                    "control_ts_code": str(control["ts_code"]),
                    "trade_date": exposed_date,
                    "reason_code": reason_code,
                    "same_industry": same_industry,
                    "distance": float(distance),
                }
            )
    return pd.DataFrame(rows, columns=EXPOSURE_MATCH_COLUMNS)


def summarize_reason_exposures(
    cases: pd.DataFrame,
    reason_codes: Iterable[str],
    *,
    return_col: str,
) -> pd.DataFrame:
    """Apply fixed cohort gates to outcome-independent reason exposures."""
    _require(
        cases,
        {"ts_code", "trade_date", "time_fold", return_col},
        "cases",
    )
    prepared = cases.copy(deep=True)
    prepared["trade_date"] = prepared["trade_date"].map(_normalized_date)
    if prepared["trade_date"].isna().any():
        raise ValueError("cases contain invalid trade_date")
    if prepared.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("cases must be unique by ts_code and trade_date")
    prepared["_ts_code"] = prepared["ts_code"].astype(str)
    indexed = prepared.set_index(["_ts_code", "trade_date"])

    rows: list[dict[str, object]] = []
    for reason_code in sorted({str(reason) for reason in reason_codes}):
        exposed = prepared[reason_exposure_mask(prepared, reason_code)]
        total_exposed = len(exposed)
        matches = match_exposure_controls(prepared, reason_code)
        paired_rows: list[dict[str, object]] = []
        for (exposed_code, trade_date), group in matches.groupby(
            ["exposed_ts_code", "trade_date"], sort=True
        ):
            exposed_row = indexed.loc[(str(exposed_code), trade_date)]
            exposed_return = _finite(exposed_row[return_col])
            control_returns = [
                _finite(indexed.loc[(str(code), trade_date)][return_col])
                for code in group["control_ts_code"]
            ]
            if exposed_return is None or any(
                value is None for value in control_returns
            ):
                continue
            control_array = np.asarray(control_returns, dtype=float)
            paired_rows.append(
                {
                    "trade_date": trade_date,
                    "time_fold": exposed_row["time_fold"],
                    "return_delta": exposed_return
                    - float(control_array.mean()),
                    "loss_rate_delta": float(exposed_return < 0.0)
                    - float((control_array < 0.0).mean()),
                }
            )
        paired = pd.DataFrame(
            paired_rows,
            columns=[
                "trade_date",
                "time_fold",
                "return_delta",
                "loss_rate_delta",
            ],
        )
        support = len(paired)
        exposure_dates = int(paired["trade_date"].nunique())
        coverage = support / total_exposed if total_exposed else 0.0
        paired_return_delta = (
            float(paired["return_delta"].mean()) if support else np.nan
        )
        paired_loss_rate_delta = (
            float(paired["loss_rate_delta"].mean()) if support else np.nan
        )
        underperforming_folds = (
            int(
                (
                    paired.groupby("time_fold")["return_delta"].mean()
                    < 0.0
                ).sum()
            )
            if support and paired["time_fold"].notna().all()
            else 0
        )
        eligible = bool(
            support >= MIN_EXPOSURE_SUPPORT
            and exposure_dates >= MIN_EXPOSURE_DATES
            and coverage >= MIN_EXPOSURE_CONTROL_COVERAGE
            and paired_return_delta < 0.0
            and paired_loss_rate_delta > 0.0
            and underperforming_folds >= MIN_UNDERPERFORMING_FOLDS
        )
        rows.append(
            {
                "reason_code": reason_code,
                "exposure_support": support,
                "exposure_dates": exposure_dates,
                "exposure_control_coverage": coverage,
                "paired_return_delta": paired_return_delta,
                "paired_loss_rate_delta": paired_loss_rate_delta,
                "underperforming_folds": underperforming_folds,
                "exposure_eligible": eligible,
            }
        )
    return pd.DataFrame(rows, columns=EXPOSURE_COLUMNS)
