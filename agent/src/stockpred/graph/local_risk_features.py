"""Point-in-time local evidence used to diagnose graph model risk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

COVERAGE_FRAME_KEY = "__coverage__"


@dataclass(frozen=True)
class RiskTableSpec:
    table_name: str
    layer: str
    available_date_column: str
    secondary_actual_date_column: str | None = None

    def __post_init__(self) -> None:
        if not self.available_date_column.strip():
            raise ValueError("available date column must not be empty")


TABLE_SPECS = {
    spec.table_name: spec
    for spec in (
        RiskTableSpec("fact_margin_detail", "market_core", "trade_date"),
        RiskTableSpec("fact_holdernumber", "market_core", "ann_date"),
        RiskTableSpec("fact_fina_indicator", "market_core", "ann_date"),
        RiskTableSpec("fact_stock_daily_basic", "market_core", "trade_date"),
        RiskTableSpec("raw_holdertrade", "source_raw", "ann_date"),
        RiskTableSpec("raw_repurchase", "source_raw", "ann_date"),
        RiskTableSpec("raw_pledge_detail", "source_raw", "ann_date"),
        RiskTableSpec("raw_block_trade", "source_raw", "trade_date"),
        RiskTableSpec("raw_top_list", "source_raw", "trade_date"),
        RiskTableSpec("raw_top_inst", "source_raw", "trade_date"),
        RiskTableSpec("raw_income", "source_raw", "ann_date", "f_ann_date"),
        RiskTableSpec("raw_balancesheet", "source_raw", "ann_date", "f_ann_date"),
        RiskTableSpec("raw_cashflow", "source_raw", "ann_date", "f_ann_date"),
    )
}


def has_complete_coverage(
    coverage: pd.DataFrame,
    table_name: str,
    eval_date: str,
    window_days: int,
) -> bool:
    """Return whether verified metadata fully covers the requested window."""
    rows = _prepare_coverage(coverage)
    return _has_prepared_coverage(rows, table_name, eval_date, window_days)


def _prepare_coverage(coverage: object) -> pd.DataFrame:
    required = {"table_name", "start_date", "end_date", "complete"}
    if not isinstance(coverage, pd.DataFrame) or not required.issubset(coverage.columns):
        return pd.DataFrame(columns=list(required))
    rows = coverage.copy(deep=True)
    rows["table_name"] = rows["table_name"].astype("string")
    rows["start_date"] = pd.to_datetime(
        rows["start_date"].astype("string"), format="mixed", errors="coerce"
    )
    rows["end_date"] = pd.to_datetime(
        rows["end_date"].astype("string"), format="mixed", errors="coerce"
    )
    return rows


def _has_prepared_coverage(
    rows: pd.DataFrame,
    table_name: str,
    eval_date: str,
    window_days: int,
) -> bool:
    if rows.empty:
        return False
    evaluation = pd.to_datetime(str(eval_date), errors="coerce")
    if pd.isna(evaluation):
        return False
    window_start = evaluation - pd.Timedelta(days=window_days)
    table_rows = rows[rows["table_name"].eq(table_name)]
    if (
        table_rows.empty
        or table_rows[["start_date", "end_date"]].isna().any().any()
        or table_rows["start_date"].gt(table_rows["end_date"]).any()
    ):
        return False
    matching = table_rows[
        table_rows["start_date"].le(evaluation)
        & table_rows["end_date"].ge(window_start)
    ]
    if matching.empty:
        return False
    fully_covers = matching["start_date"].le(window_start) & matching[
        "end_date"
    ].ge(evaluation)
    explicit_complete = matching["complete"].map(
        lambda value: isinstance(value, (bool, np.bool_)) and bool(value)
    )
    return bool((fully_covers & explicit_complete).all())


def _normalized_dates(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values.astype("string"), format="mixed", errors="coerce")
    if parsed.isna().any():
        raise ValueError("date values must be valid and non-missing")
    return parsed.dt.strftime("%Y%m%d")


def attach_available_date(rows: pd.DataFrame, spec: RiskTableSpec) -> pd.DataFrame:
    """Copy rows and attach the table's normalized evidence availability date."""
    if spec.available_date_column not in rows.columns:
        raise KeyError(spec.available_date_column)
    result = rows.copy(deep=True)
    primary = pd.to_datetime(
        _normalized_dates(result[spec.available_date_column]), format="%Y%m%d"
    )
    available = primary
    if spec.secondary_actual_date_column is not None:
        if spec.secondary_actual_date_column not in result.columns:
            raise KeyError(spec.secondary_actual_date_column)
        secondary = pd.to_datetime(
            result[spec.secondary_actual_date_column].astype("string"),
            format="mixed",
            errors="raise",
        )
        available = pd.concat([primary, secondary], axis=1).max(axis=1)
    result["available_date"] = available.dt.strftime("%Y%m%d")
    return result


def assert_point_in_time(rows: pd.DataFrame) -> None:
    """Reject rows whose evidence became available after their evaluation date."""
    required = {"trade_date", "available_date"}
    missing = required.difference(rows.columns)
    if missing:
        raise KeyError(f"missing point-in-time columns: {sorted(missing)}")
    trade_dates = _normalized_dates(rows["trade_date"])
    available_dates = _normalized_dates(rows["available_date"])
    if (available_dates > trade_dates).any():
        raise ValueError("future evidence is not permitted")


def _event_output_columns(events: pd.DataFrame, eval_rows: pd.DataFrame) -> list[str]:
    columns = list(eval_rows.columns)
    for column in events.columns:
        if column == "ts_code":
            continue
        output = f"event_{column}" if column in eval_rows.columns else column
        if output not in columns:
            columns.append(output)
    return columns


def _combine_eval_and_event(eval_row: pd.Series, event: pd.Series) -> dict[str, object]:
    combined = eval_row.to_dict()
    for column, value in event.items():
        if column == "ts_code":
            continue
        output = f"event_{column}" if column in combined else column
        combined[output] = value
    return combined


def _group_events_by_stock_and_date(
    events: pd.DataFrame,
) -> dict[object, tuple[pd.DatetimeIndex, dict[pd.Timestamp, pd.DataFrame]]]:
    if events.empty:
        return {}
    prepared = events.copy(deep=True)
    prepared["_available_dt"] = pd.to_datetime(_normalized_dates(prepared["available_date"]))
    prepared["_event_pos"] = np.arange(len(prepared))
    grouped: dict[object, tuple[pd.DatetimeIndex, dict[pd.Timestamp, pd.DataFrame]]] = {}
    for ts_code, stock_rows in prepared.groupby("ts_code", sort=False):
        by_date = {
            available_dt: rows.copy()
            for available_dt, rows in stock_rows.groupby("_available_dt", sort=False)
        }
        grouped[ts_code] = (pd.DatetimeIndex(sorted(by_date)), by_date)
    return grouped


def trailing_window(
    events: pd.DataFrame,
    eval_rows: pd.DataFrame,
    window_days: int,
) -> pd.DataFrame:
    """Match events to evaluations within an inclusive calendar-day window."""
    event_copy = events.copy(deep=True)
    eval_copy = eval_rows.copy(deep=True)
    for frame, required in (
        (event_copy, {"ts_code", "available_date"}),
        (eval_copy, {"ts_code", "trade_date"}),
    ):
        missing = required.difference(frame.columns)
        if missing:
            raise KeyError(f"missing columns: {sorted(missing)}")

    grouped_events = _group_events_by_stock_and_date(event_copy)
    records: list[dict[str, object]] = []
    for _, eval_row in eval_copy.iterrows():
        eval_date = pd.to_datetime(str(eval_row["trade_date"]))
        stock_group = grouped_events.get(eval_row["ts_code"])
        if stock_group is None:
            continue
        stock_dates, by_date = stock_group
        start_date = eval_date - pd.Timedelta(days=window_days)
        left = stock_dates.searchsorted(start_date, side="left")
        right = stock_dates.searchsorted(eval_date, side="right")
        if left >= right:
            continue
        matched = pd.concat(
            [by_date[stock_dates[position]] for position in range(left, right)],
            axis=0,
        ).sort_values("_event_pos", kind="stable")
        records.extend(
            _combine_eval_and_event(eval_row, event[event_copy.columns])
            for _, event in matched.iterrows()
        )

    result = pd.DataFrame(records, columns=_event_output_columns(event_copy, eval_copy))
    if not result.empty:
        assert_point_in_time(result)
    return result


def latest_asof(
    events: pd.DataFrame,
    eval_rows: pd.DataFrame,
    tie_break_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Attach the latest available event while retaining unmatched evaluations."""
    event_copy = events.copy(deep=True)
    eval_copy = eval_rows.copy(deep=True)
    for frame, required in (
        (event_copy, {"ts_code", "available_date"}),
        (eval_copy, {"ts_code", "trade_date"}),
    ):
        missing = required.difference(frame.columns)
        if missing:
            raise KeyError(f"missing columns: {sorted(missing)}")

    grouped_events = _group_events_by_stock_and_date(event_copy)
    records: list[dict[str, object]] = []
    for _, eval_row in eval_copy.iterrows():
        eval_date = pd.to_datetime(str(eval_row["trade_date"]))
        stock_group = grouped_events.get(eval_row["ts_code"])
        if stock_group is None:
            records.append(eval_row.to_dict())
            continue
        stock_dates, by_date = stock_group
        right = stock_dates.searchsorted(eval_date, side="right")
        if right == 0:
            records.append(eval_row.to_dict())
            continue
        latest = by_date[stock_dates[right - 1]]
        if len(latest) > 1:
            if not tie_break_columns or not set(tie_break_columns).issubset(latest.columns):
                raise ValueError("latest asof tie requires sufficient tie_break_columns")
            ordered = latest.sort_values(tie_break_columns, na_position="first")
            winner = ordered.iloc[-1]
            tied = ordered
            for column in tie_break_columns:
                value = winner[column]
                tied = tied[tied[column].isna()] if pd.isna(value) else tied[tied[column] == value]
            if len(tied) > 1:
                raise ValueError("latest asof tie remains after tie_break_columns")
        else:
            winner = latest.iloc[-1]
        records.append(_combine_eval_and_event(eval_row, winner[event_copy.columns]))

    result = pd.DataFrame(records, columns=_event_output_columns(event_copy, eval_copy))
    matched = result.dropna(subset=["available_date"])
    if not matched.empty:
        assert_point_in_time(matched)
    return result


FEATURE_NAMES = (
    "margin_balance_change_5d",
    "margin_net_buy_ratio_5d",
    "holder_num_change",
    "holder_sell_ratio_90d",
    "holder_buy_ratio_90d",
    "repurchase_amount_180d",
    "pledge_amount_180d",
    "pledge_release_amount_180d",
    "block_trade_amount_20d",
    "block_trade_discount_20d",
    "top_list_net_amount_20d",
    "top_inst_net_buy_20d",
    "financial_revenue_growth",
    "financial_profit_growth",
    "financial_cash_to_profit",
    "financial_debt_to_assets",
)


def _numeric(value: object) -> float:
    return float(pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0])


def _safe_ratio(numerator: object, denominator: object) -> float:
    numerator_value = _numeric(numerator)
    denominator_value = _numeric(denominator)
    if (
        not np.isfinite(numerator_value)
        or not np.isfinite(denominator_value)
        or denominator_value == 0
    ):
        return np.nan
    return numerator_value / denominator_value


def _complete_numeric_values(values: pd.Series) -> pd.Series | None:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    return None if numeric.isna().any() else numeric


def _usable_frame(frames: Mapping[str, pd.DataFrame], name: str) -> pd.DataFrame:
    frame = frames.get(name)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["available_date"])
    if "available_date" not in frame.columns:
        raise KeyError(f"{name} missing available_date")
    result = frame.copy(deep=True)
    result["available_date"] = _normalized_dates(result["available_date"])
    result["_available_dt"] = pd.to_datetime(
        result["available_date"], format="%Y%m%d"
    )
    if name == "raw_block_trade" and "trade_date" not in result.columns:
        raise KeyError("raw_block_trade missing trade_date")
    if "trade_date" in result.columns:
        result["trade_date"] = _normalized_dates(result["trade_date"])
    if "ts_code" in result.columns:
        result["ts_code"] = result["ts_code"].astype("string")
    return result


def _available_rows(
    frame: pd.DataFrame,
    eval_date: str,
    ts_code: object | None = None,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    selected = frame[frame["available_date"] <= eval_date]
    if ts_code is not None:
        if "ts_code" not in selected.columns:
            return selected.iloc[0:0].copy()
        selected = selected[selected["ts_code"] == ts_code]
    return selected.copy()


def _window_rows(frame: pd.DataFrame, eval_date: str, window_days: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    end = pd.Timestamp(eval_date)
    start = end - pd.Timedelta(days=window_days)
    dates = (
        frame["_available_dt"]
        if "_available_dt" in frame.columns
        else pd.to_datetime(frame["available_date"], format="%Y%m%d")
    )
    return frame[dates.between(start, end, inclusive="both")].copy()


def _event_sum(
    frame: pd.DataFrame,
    eval_date: str,
    ts_code: object,
    window_days: int,
    value_column: str,
    mask_column: str | None = None,
    mask_values: set[str] | None = None,
    *,
    allowed_mask_values: set[str] | None = None,
    normalize_integer_categories: bool = False,
    exclude_mask_values: bool = False,
    absolute_values: bool = False,
    complete_coverage: bool | None = None,
) -> float:
    if complete_coverage is not True:
        return np.nan
    covered = _window_rows(frame, eval_date, window_days)
    if covered.empty:
        return 0.0 if complete_coverage else np.nan
    if value_column not in covered.columns:
        return np.nan
    selected = covered[covered.get("ts_code", pd.Series(index=covered.index)) == ts_code]
    if mask_column is not None:
        if mask_column not in selected.columns:
            return np.nan
        def normalized_category(value: object) -> str | None:
            if pd.isna(value) or isinstance(value, (bool, np.bool_)):
                return None
            if normalize_integer_categories and isinstance(value, (int, float, np.number)):
                number = float(value)
                if not np.isfinite(number) or not number.is_integer():
                    return None
                return str(int(number))
            normalized = str(value).strip()
            return normalized or None

        categories = selected[mask_column].map(normalized_category)
        if categories.isna().any():
            return np.nan
        if (
            allowed_mask_values is None
            or not categories.isin(allowed_mask_values).all()
        ):
            return np.nan
        mask = categories.isin(mask_values or set())
        selected = selected[~mask if exclude_mask_values else mask]
    if selected.empty:
        return 0.0 if complete_coverage else np.nan
    values = _complete_numeric_values(selected[value_column])
    if values is None:
        return np.nan
    if absolute_values:
        values = values.abs()
    return float(values.sum())


_NEGATIVE_REPURCHASE_STATES = {
    "stopped", "failed", "expired", "停止", "未通过", "失效"
}
_ACTIVE_REPURCHASE_STATES = {
    "active", "提议", "预案", "股东大会通过", "实施", "完成"
}


def _finite_number(value: object) -> float | None:
    number = _numeric(value)
    return number if np.isfinite(number) else None


def _normalized_repurchase_proc(value: object) -> str | None:
    if pd.isna(value):
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _repurchase_plan_key(row: pd.Series) -> tuple[object, ...]:
    exp_date = pd.to_datetime(str(row.get("exp_date")), errors="coerce")
    if not pd.isna(exp_date):
        return ("exp_date", exp_date.strftime("%Y%m%d"))
    high = _finite_number(row.get("high_limit"))
    low = _finite_number(row.get("low_limit"))
    if high is not None or low is not None:
        return ("limits", high, low)
    return (
        "economic",
        _finite_number(row.get("vol")),
        _finite_number(row.get("amount")),
    )


def _latest_repurchase_snapshot(plan_rows: pd.DataFrame) -> pd.Series | None:
    latest_date = plan_rows["available_date"].max()
    latest = plan_rows[plan_rows["available_date"] == latest_date]
    content_columns = [
        column
        for column in ("proc", "amount", "vol", "high_limit", "low_limit", "exp_date")
        if column in latest.columns
    ]
    latest = latest.drop_duplicates(subset=content_columns)
    if len(latest) != 1:
        return None
    return latest.iloc[0]


def _repurchase_amount(
    rows: pd.DataFrame, complete_coverage: bool | None
) -> float:
    if complete_coverage is not True:
        return np.nan
    if rows.empty:
        return 0.0 if complete_coverage else np.nan
    required = {"ts_code", "amount", "proc", "available_date"}
    if not required.issubset(rows.columns):
        return np.nan
    snapshots = rows.copy()
    snapshots["_plan_key"] = [
        _repurchase_plan_key(row) for _, row in snapshots.iterrows()
    ]
    snapshots["_proc"] = snapshots["proc"].map(_normalized_repurchase_proc)
    active: list[pd.Series] = []
    for _, plan_rows in snapshots.groupby("_plan_key", sort=False):
        latest = _latest_repurchase_snapshot(plan_rows)
        if latest is None or latest["_proc"] not in (
            _ACTIVE_REPURCHASE_STATES | _NEGATIVE_REPURCHASE_STATES
        ):
            return np.nan
        if latest["_proc"] in _ACTIVE_REPURCHASE_STATES:
            active.append(latest)
    if not active:
        return 0.0 if complete_coverage else np.nan
    amounts = _complete_numeric_values(pd.Series([row["amount"] for row in active]))
    return np.nan if amounts is None else float(amounts.sum())


def _unique_latest_evidence(
    rows: pd.DataFrame,
    order_columns: list[str],
    evidence_columns: list[str],
) -> pd.Series | None:
    if rows.empty or not set([*order_columns, *evidence_columns]).issubset(rows.columns):
        return None
    candidates = rows
    for column in order_columns:
        candidates = candidates[candidates[column] == candidates[column].max()]
    candidates = candidates.drop_duplicates(subset=evidence_columns)
    return candidates.iloc[0] if len(candidates) == 1 else None


def _latest_report(
    frame: pd.DataFrame,
    eval_date: str,
    ts_code: object,
    evidence_columns: list[str],
) -> pd.Series | None:
    rows = _available_rows(frame, eval_date, ts_code)
    if rows.empty or "end_date" not in rows.columns:
        return None
    rows = rows.assign(_end_date=_normalized_dates(rows["end_date"]))
    return _unique_latest_evidence(
        rows, ["_end_date", "available_date"], evidence_columns
    )


def _set_feature(result: pd.DataFrame, index: int, name: str, value: float) -> None:
    if not pd.isna(value) and not np.isfinite(float(value)):
        value = np.nan
    result.at[index, name] = value
    result.at[index, f"{name}_missing"] = bool(pd.isna(value))


def _set_margin_features(
    result: pd.DataFrame,
    index: int,
    rows: pd.DataFrame,
) -> None:
    groups = list(rows.groupby("available_date", sort=True))
    balance_observations = [
        _unique_latest_evidence(group, [], ["rzrqye"])
        for _, group in groups
    ]
    balance_change = np.nan
    latest_six = balance_observations[-6:]
    if len(latest_six) == 6 and all(row is not None for row in latest_six):
        ratio = _safe_ratio(latest_six[-1]["rzrqye"], latest_six[0]["rzrqye"])
        balance_change = ratio - 1 if not pd.isna(ratio) else np.nan
    _set_feature(result, index, "margin_balance_change_5d", balance_change)

    net_buy_ratio = np.nan
    net_buy_observations = [
        _unique_latest_evidence(group, [], ["rzmre", "rzche", "rzrqye"])
        for _, group in groups
    ]
    latest_five = net_buy_observations[-5:]
    if len(latest_five) == 5 and all(row is not None for row in latest_five):
        latest_five_frame = pd.DataFrame(latest_five)
        net_buy = (
            pd.to_numeric(latest_five_frame["rzmre"], errors="coerce")
            - pd.to_numeric(latest_five_frame["rzche"], errors="coerce")
        ).sum(min_count=5)
        net_buy_ratio = _safe_ratio(
            net_buy, abs(_numeric(latest_five[-1]["rzrqye"]))
        )
    _set_feature(result, index, "margin_net_buy_ratio_5d", net_buy_ratio)


def _set_financial_features(
    result: pd.DataFrame,
    index: int,
    eval_date: str,
    ts_code: object,
    frames: Mapping[str, pd.DataFrame],
) -> None:
    income = _available_rows(frames["raw_income"], eval_date, ts_code)
    if not income.empty and "end_date" in income.columns:
        income = income.assign(_end_date=pd.to_datetime(_normalized_dates(income["end_date"])))
    def growth(column: str) -> float:
        latest = _unique_latest_evidence(
            income, ["_end_date", "available_date"], [column]
        )
        if latest is None:
            return np.nan
        prior = income[
            (income["_end_date"].dt.year == latest["_end_date"].year - 1)
            & (income["_end_date"].dt.quarter == latest["_end_date"].quarter)
        ]
        prior_row = _unique_latest_evidence(
            prior, ["_end_date", "available_date"], [column]
        )
        if prior_row is None:
            return np.nan
        ratio = _safe_ratio(latest[column], prior_row[column])
        return ratio - 1 if not pd.isna(ratio) else np.nan

    revenue_growth = growth("revenue")
    profit_growth = growth("n_income_attr_p")
    _set_feature(result, index, "financial_revenue_growth", revenue_growth)
    _set_feature(result, index, "financial_profit_growth", profit_growth)

    cash = _latest_report(
        frames["raw_cashflow"],
        eval_date,
        ts_code,
        ["n_cashflow_act", "net_profit"],
    )
    cash_to_profit = np.nan
    if cash is not None and {"n_cashflow_act", "net_profit"}.issubset(cash.index):
        cash_to_profit = _safe_ratio(cash["n_cashflow_act"], cash["net_profit"])
    _set_feature(result, index, "financial_cash_to_profit", cash_to_profit)

    balance = _latest_report(
        frames["raw_balancesheet"],
        eval_date,
        ts_code,
        ["total_liab", "total_assets"],
    )
    debt_to_assets = np.nan
    if balance is not None and {"total_liab", "total_assets"}.issubset(balance.index):
        debt_to_assets = _safe_ratio(balance["total_liab"], balance["total_assets"])
    _set_feature(result, index, "financial_debt_to_assets", debt_to_assets)


def build_local_risk_features(
    eval_rows: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build conservative point-in-time risk evidence for each evaluation row."""
    required = {"ts_code", "trade_date"}
    missing = required.difference(eval_rows.columns)
    if missing:
        raise KeyError(f"missing evaluation columns: {sorted(missing)}")
    result = eval_rows.copy(deep=True).reset_index(drop=True)
    result["trade_date"] = _normalized_dates(result["trade_date"])
    result["ts_code"] = result["ts_code"].astype("string")
    if result.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("eval_rows must be unique by ts_code and trade_date")
    for feature in FEATURE_NAMES:
        result[feature] = np.nan
        result[f"{feature}_missing"] = True

    prepared = {
        name: _usable_frame(frames, name)
        for name in TABLE_SPECS
    }
    grouped = {
        name: {
            str(ts_code): rows
            for ts_code, rows in frame.groupby("ts_code", sort=False)
        }
        if not frame.empty and "ts_code" in frame.columns
        else {}
        for name, frame in prepared.items()
    }
    coverage = _prepare_coverage(frames.get(COVERAGE_FRAME_KEY))
    for index, eval_row in result.iterrows():
        eval_date = eval_row["trade_date"]
        ts_code = str(eval_row["ts_code"])
        stock_frames = {
            name: grouped[name].get(ts_code, prepared[name].iloc[0:0])
            for name in prepared
        }

        def covered(table_name: str, window_days: int) -> bool:
            return _has_prepared_coverage(
                coverage, table_name, eval_date, window_days
            )

        margin = _available_rows(stock_frames["fact_margin_detail"], eval_date, ts_code)
        _set_margin_features(result, index, margin)

        holders = _available_rows(stock_frames["fact_holdernumber"], eval_date, ts_code)
        holder_order: list[str] = []
        if "end_date" in holders.columns:
            holders["end_date"] = _normalized_dates(holders["end_date"])
            holder_order.append("end_date")
        holder_observations = [
            _unique_latest_evidence(group, holder_order, ["holder_num"])
            for _, group in holders.groupby("available_date", sort=True)
        ]
        holder_change = np.nan
        latest_holders = holder_observations[-2:]
        if len(latest_holders) == 2 and all(
            row is not None for row in latest_holders
        ):
            holder_ratio = _safe_ratio(
                latest_holders[-1]["holder_num"], latest_holders[0]["holder_num"]
            )
            holder_change = holder_ratio - 1 if not pd.isna(holder_ratio) else np.nan
        _set_feature(result, index, "holder_num_change", holder_change)

        holder_trade = stock_frames["raw_holdertrade"]
        _set_feature(
            result,
            index,
            "holder_sell_ratio_90d",
            _event_sum(
                holder_trade, eval_date, ts_code, 90, "change_ratio", "in_de", {"DE"},
                allowed_mask_values={"DE", "IN"},
                absolute_values=True,
                complete_coverage=covered("raw_holdertrade", 90),
            ),
        )
        _set_feature(
            result,
            index,
            "holder_buy_ratio_90d",
            _event_sum(
                holder_trade, eval_date, ts_code, 90, "change_ratio", "in_de", {"IN"},
                allowed_mask_values={"DE", "IN"},
                absolute_values=True,
                complete_coverage=covered("raw_holdertrade", 90),
            ),
        )

        repurchase = _window_rows(stock_frames["raw_repurchase"], eval_date, 180)
        repurchase_covered = covered("raw_repurchase", 180)
        selected_repurchase = repurchase[repurchase.get("ts_code").eq(ts_code)] if "ts_code" in repurchase.columns else repurchase.iloc[0:0]
        repurchase_amount = _repurchase_amount(
            selected_repurchase, repurchase_covered
        )
        _set_feature(result, index, "repurchase_amount_180d", repurchase_amount)

        pledge = stock_frames["raw_pledge_detail"]
        _set_feature(
            result, index, "pledge_amount_180d",
            _event_sum(
                pledge, eval_date, ts_code, 180, "pledge_amount", "is_release", {"1"},
                allowed_mask_values={"0", "1", "2"},
                normalize_integer_categories=True,
                exclude_mask_values=True,
                complete_coverage=covered("raw_pledge_detail", 180),
            ),
        )
        _set_feature(
            result, index, "pledge_release_amount_180d",
            _event_sum(
                pledge, eval_date, ts_code, 180, "pledge_amount", "is_release", {"1"},
                allowed_mask_values={"0", "1", "2"},
                normalize_integer_categories=True,
                complete_coverage=covered("raw_pledge_detail", 180),
            ),
        )

        block = _window_rows(stock_frames["raw_block_trade"], eval_date, 20)
        block_amount = np.nan
        block_discount = np.nan
        block_covered = covered("raw_block_trade", 20)
        if block_covered is not True:
            pass
        elif block.empty:
            block_amount = 0.0 if block_covered else np.nan
        elif {"ts_code", "amount"}.issubset(block.columns):
            selected = block[block["ts_code"] == ts_code].copy()
            if selected.empty:
                block_amount = 0.0 if block_covered else np.nan
            else:
                amounts = _complete_numeric_values(selected["amount"])
                if amounts is not None:
                    block_amount = float(amounts.sum())
            closes = _available_rows(
                stock_frames["fact_stock_daily_basic"], eval_date, ts_code
            )
            discount_columns = {"price", "trade_date"}
            close_columns = ["ts_code", "trade_date", "close"]
            if (
                not selected.empty
                and discount_columns.issubset(selected.columns)
                and set(close_columns).issubset(closes.columns)
            ):
                participating_dates = selected["trade_date"].drop_duplicates()
                closes = closes[closes["trade_date"].isin(participating_dates)]
                closes = closes[close_columns].drop_duplicates()
                conflicting_closes = closes.duplicated(
                    ["ts_code", "trade_date"], keep=False
                ).any()
                if conflicting_closes:
                    closes = closes.iloc[0:0]
                selected = selected.merge(
                    closes[["ts_code", "trade_date", "close"]],
                    on=["ts_code", "trade_date"],
                    how="left",
                )
                amounts = _complete_numeric_values(selected["amount"])
                prices = _complete_numeric_values(selected["price"])
                close_values = _complete_numeric_values(selected["close"])
                if amounts is not None and prices is not None and close_values is not None:
                    valid = amounts.gt(0)
                    has_zero_close = close_values[valid].eq(0).any()
                    denominator = amounts[valid].sum() if not has_zero_close else 0.0
                else:
                    valid = pd.Series(False, index=selected.index)
                    denominator = 0.0
                if valid.any() and denominator != 0:
                    block_discount = float(
                        ((prices[valid] / close_values[valid] - 1) * amounts[valid]).sum()
                        / denominator
                    )
        _set_feature(result, index, "block_trade_amount_20d", block_amount)
        _set_feature(result, index, "block_trade_discount_20d", block_discount)

        _set_feature(
            result, index, "top_list_net_amount_20d",
            _event_sum(
                stock_frames["raw_top_list"], eval_date, ts_code, 20, "net_amount",
                complete_coverage=covered("raw_top_list", 20),
            ),
        )
        _set_feature(
            result, index, "top_inst_net_buy_20d",
            _event_sum(
                stock_frames["raw_top_inst"], eval_date, ts_code, 20, "net_buy",
                complete_coverage=covered("raw_top_inst", 20),
            ),
        )

        _set_financial_features(result, index, eval_date, ts_code, stock_frames)

    return result
