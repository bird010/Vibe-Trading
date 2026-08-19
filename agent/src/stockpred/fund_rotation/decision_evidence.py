"""Build causal, display-oriented evidence from one fund-rotation run."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from backtest.fund_rotation.contracts import DecisionKind, TargetWeightDecision
from backtest.fund_rotation.runner import FundRotationRunResult


def _date_key(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(char for char in text if char.isdigit())
    return digits[:8] if len(digits) >= 8 else text


def _finite(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if math.isfinite(number) else default


def _weights_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    holdings = snapshot.get("holdings", ())
    if isinstance(holdings, Sequence) and not isinstance(holdings, (str, bytes)):
        for raw in holdings:
            if not isinstance(raw, Mapping):
                continue
            code = str(raw.get("ts_code") or raw.get("code") or "").strip()
            weight = _finite(raw.get("actual_weight"), 0.0)
            if code and code != "_CASH" and weight > 0:
                weights[code] = weight
    equity = _finite(snapshot.get("equity"), 0.0)
    cash = _finite(snapshot.get("cash"), 0.0)
    if equity > 0 and cash > 0:
        weights["_CASH"] = cash / equity
    return weights


def _target_weights(decision: TargetWeightDecision) -> dict[str, float]:
    return {
        str(code): _finite(weight)
        for code, weight in decision.target_weights.items()
        if _finite(weight) > 0
    }


def _changed_positions(before: Mapping[str, float], after: Mapping[str, float]) -> int:
    """Count account-state position changes; turnover separately excludes CASH."""
    codes = set(before) | set(after)
    return sum(
        1
        for code in codes
        if not math.isclose(
            _finite(before.get(code)),
            _finite(after.get(code)),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )


def _turnover(before: Mapping[str, float], after: Mapping[str, float]) -> float:
    codes = (set(before) | set(after)) - {"_CASH"}
    return sum(abs(_finite(after.get(code)) - _finite(before.get(code))) for code in codes) / 2.0


def _interval_row(
    code: str,
    dates: Sequence[str],
    states: Sequence[Mapping[str, Any]],
    start: int,
    end: int,
) -> dict[str, Any]:
    weights = [_finite(states[index].get("actual_weight")) for index in range(start, end + 1)]
    targets = [states[index].get("target_weight") for index in range(start, end + 1)]
    market_values = [_finite(states[index].get("market_value")) for index in range(start, end + 1)]
    numeric_targets = [
        _finite(value) for value in targets if value is not None and not isinstance(value, bool)
    ]
    return {
        "ts_code": code,
        "start_date": dates[start],
        "end_date": dates[end],
        "actual_weight": sum(weights) / len(weights) if weights else 0.0,
        "target_weight": (sum(numeric_targets) / len(numeric_targets)) if numeric_targets else None,
        "market_value": (sum(market_values) / len(market_values)) if market_values else None,
    }


def build_holdings_timeline(
    positions_history: Sequence[Mapping[str, Any]],
    decisions: Sequence[TargetWeightDecision],
    evaluation_dates: Sequence[str],
    trade_events: Sequence[Mapping[str, Any]] | None = None,
    initial_capital: float | None = None,
) -> dict[str, Any]:
    """Compress actual daily holding snapshots into causal intervals."""
    dates = sorted({_date_key(date) for date in evaluation_dates if _date_key(date)})
    snapshot_by_date = {
        _date_key(snapshot.get("trade_date")): snapshot
        for snapshot in positions_history
        if _date_key(snapshot.get("trade_date"))
    }
    rows_by_code: dict[str, dict[str, Mapping[str, Any]]] = {}
    for date in dates:
        snapshot = snapshot_by_date.get(date, {})
        holding_by_code: dict[str, Mapping[str, Any]] = {}
        raw_holdings = snapshot.get("holdings", ())
        if isinstance(raw_holdings, Sequence) and not isinstance(raw_holdings, (str, bytes)):
            for raw in raw_holdings:
                if not isinstance(raw, Mapping):
                    continue
                code = str(raw.get("ts_code") or raw.get("code") or "").strip()
                if code and code != "_CASH":
                    holding_by_code[code] = raw
        equity = _finite(snapshot.get("equity"), 0.0)
        cash = _finite(snapshot.get("cash"), 0.0)
        if equity > 0 and cash > 0:
            holding_by_code["_CASH"] = {
                "actual_weight": cash / equity,
                "target_weight": None,
                "market_value": cash,
            }
        for code in set(rows_by_code) | set(holding_by_code):
            rows_by_code.setdefault(code, {})[date] = holding_by_code.get(code, {})

    intervals: list[dict[str, Any]] = []
    for code in sorted(rows_by_code):
        states = [rows_by_code[code].get(date, {}) for date in dates]
        index = 0
        while index < len(states):
            current = states[index]
            active = _finite(current.get("actual_weight")) > 0
            if not active:
                index += 1
                continue
            end = index
            current_target = current.get("target_weight")
            while end + 1 < len(states):
                next_state = states[end + 1]
                next_active = _finite(next_state.get("actual_weight")) > 0
                next_target = next_state.get("target_weight")
                same_target = (
                    current_target is None and next_target is None
                ) or math.isclose(
                    _finite(current_target, math.nan),
                    _finite(next_target, math.nan),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                if not next_active or not same_target:
                    break
                end += 1
            intervals.append(_interval_row(code, dates, states, index, end))
            index = end + 1

    markers: list[dict[str, Any]] = []
    previous: dict[str, float] = {}
    sorted_decisions = sorted(decisions, key=lambda item: _date_key(item.signal_date))
    for decision in sorted_decisions:
        signal_date = _date_key(decision.signal_date)
        after = _target_weights(decision) if decision.action is DecisionKind.SET_TARGETS else dict(previous)
        _, actual_before = _actual_before(positions_history, signal_date)
        _, before_snapshot = _latest_actual_snapshot(positions_history, signal_date)
        before_equity = _finite((before_snapshot or {}).get("equity"), 0.0)
        fills = _execution_rows(
            trade_events or (),
            decision_id=str(decision.decision_id),
            signal_date=signal_date,
        )
        executed_codes: set[str] = set()
        executed_notional = 0.0
        for fill in fills:
            status = str(fill.get("status", "")).upper()
            filled = _finite(fill.get("filled"), 0.0)
            if filled <= 0 or status not in {"FILLED", "PARTIAL", "PARTIALLY_FILLED"}:
                continue
            code = str(fill.get("ts_code") or fill.get("code") or "").strip()
            if code:
                executed_codes.add(code)
            notional = _finite(fill.get("notional"), math.nan)
            executed_notional += abs(notional if math.isfinite(notional) else filled * _finite(fill.get("price"), 0.0))
        executed_changed_positions = len(executed_codes)
        turnover_nav = before_equity if before_equity > 0 else _finite(initial_capital, 0.0)
        execution_turnover = 0.5 * executed_notional / turnover_nav if turnover_nav > 0 else None
        markers.append(
            {
                "signal_date": signal_date,
                "effective_trade_date": _date_key(fills[0].get("trade_date")) if fills else None,
                "changed_positions": executed_changed_positions,
                "target_changed_positions": _changed_positions(previous, after),
                "actual_changed_positions": executed_changed_positions,
                "execution_turnover": execution_turnover,
                "turnover": execution_turnover,
                "quality_status": decision.quality_status.value,
                "cash_target_weight": max(0.0, 1.0 - sum(after.values())),
                "decision_id": decision.decision_id,
            }
        )
        previous = after

    instruments = [
        {"ts_code": code, "name": None}
        for code in sorted(rows_by_code)
    ]
    return {
        "schema_version": "1",
        "start_date": dates[0] if dates else "",
        "end_date": dates[-1] if dates else "",
        "instruments": instruments,
        "intervals": intervals,
        "rebalance_markers": markers,
    }


def _snapshot(as_of_date: str | None, weights: Mapping[str, float]) -> dict[str, Any]:
    clean = {code: _finite(weight) for code, weight in weights.items() if _finite(weight) > 0}
    return {
        "as_of_date": as_of_date,
        "source": "ACTUAL_POSITION",
        "weights": clean,
        "cash_weight": max(0.0, 1.0 - sum(clean.values())),
    }


def _latest_actual_snapshot(
    positions_history: Sequence[Mapping[str, Any]],
    signal_date: str,
) -> tuple[str | None, Mapping[str, Any] | None]:
    eligible = [
        snapshot
        for snapshot in positions_history
        if _date_key(snapshot.get("trade_date"))
        and _date_key(snapshot.get("trade_date")) <= signal_date
    ]
    if not eligible:
        return None, None
    snapshot = max(eligible, key=lambda item: _date_key(item.get("trade_date")))
    return _date_key(snapshot.get("trade_date")), snapshot


def _actual_before(
    positions_history: Sequence[Mapping[str, Any]],
    signal_date: str,
) -> tuple[str | None, dict[str, float]]:
    """Return the latest account state at or before execution starts."""
    date, snapshot = _latest_actual_snapshot(positions_history, signal_date)
    if snapshot is None:
        return None, {}
    return date, _weights_from_snapshot(snapshot)


def _execution_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    decision_id: str,
    signal_date: str,
) -> list[Mapping[str, Any]]:
    """Join modern execution rows by identity, with a legacy-only fallback."""
    identity_rows = []
    for row in rows:
        identities = {
            str(row.get(key) or "").strip()
            for key in ("decision_id", "signal_event_id", "event_id")
        }
        if decision_id in identities:
            identity_rows.append(row)
    if identity_rows:
        return identity_rows
    has_any_identity = any(
        any(str(row.get(key) or "").strip() for key in ("decision_id", "signal_event_id", "event_id"))
        for row in rows
    )
    if has_any_identity:
        # Native execution uses the decision id in signal_event_id. Older
        # execution artifacts may carry only an event id, so a same-signal
        # date is the narrowest safe compatibility bridge.
        same_signal_rows = [
            row for row in rows
            if _date_key(row.get("signal_date") or row.get("signal_week")) == signal_date
        ]
        return same_signal_rows
    return [
        row for row in rows
        if _date_key(row.get("signal_date") or row.get("signal_week")) == signal_date
    ]


def build_rebalance_evidence(
    result: FundRotationRunResult,
    evaluation_dates: Sequence[str],
    strategy_metadata: Mapping[str, Any],
    decision_trace: Sequence[Mapping[str, Any]],
    initial_capital: float | None = None,
) -> dict[str, Any]:
    """Build index and one causal decision bundle per signal date."""
    del evaluation_dates
    decisions = sorted(result.decisions, key=lambda item: _date_key(item.signal_date))
    trace_by_date = {
        _date_key(item.get("signal_date")): item
        for item in decision_trace
        if isinstance(item, Mapping) and _date_key(item.get("signal_date"))
    }
    orders = result.orders if isinstance(result.orders, list) else []
    fills = result.trade_events if isinstance(result.trade_events, list) else []
    bundles: dict[str, dict[str, Any]] = {}
    index: list[dict[str, Any]] = []
    previous_target: dict[str, float] = {}
    positions_history = result.positions_history if isinstance(result.positions_history, list) else []
    for sequence, decision in enumerate(decisions, start=1):
        signal_date = _date_key(decision.signal_date)
        after = _target_weights(decision) if decision.action is DecisionKind.SET_TARGETS else dict(previous_target)
        trace = trace_by_date.get(signal_date, {})
        before_date, before_snapshot = _latest_actual_snapshot(positions_history, signal_date)
        before_weights = _weights_from_snapshot(before_snapshot or {})
        before_equity = _finite((before_snapshot or {}).get("equity"), 0.0)
        decision_orders = _execution_rows(
            orders,
            decision_id=str(decision.decision_id),
            signal_date=signal_date,
        )
        decision_fills = _execution_rows(
            fills,
            decision_id=str(decision.decision_id),
            signal_date=signal_date,
        )
        target_changed_positions = _changed_positions(previous_target, after)
        required_changed_positions = _changed_positions(before_weights, after)
        required_turnover = _turnover(before_weights, after)
        executed_codes: set[str] = set()
        executed_notional = 0.0
        for row in decision_fills:
            status = str(row.get("status", "")).upper()
            filled = _finite(row.get("filled"), 0.0)
            if filled <= 0 or status not in {"FILLED", "PARTIAL", "PARTIALLY_FILLED"}:
                continue
            code = str(row.get("ts_code") or row.get("code") or "").strip()
            if code:
                executed_codes.add(code)
            notional = _finite(row.get("notional"), math.nan)
            if not math.isfinite(notional):
                notional = abs(filled * _finite(row.get("price"), 0.0))
            executed_notional += abs(notional)
        executed_changed_positions = len(executed_codes)
        turnover_nav = before_equity if before_equity > 0 else _finite(initial_capital, 0.0)
        execution_turnover = (
            0.5 * executed_notional / turnover_nav
            if turnover_nav > 0
            else None
        )
        candidates = []
        if isinstance(trace, Mapping):
            for raw_candidate in trace.get("candidates", ()):
                if not isinstance(raw_candidate, Mapping):
                    continue
                candidate = dict(raw_candidate)
                code = str(candidate.get("ts_code") or "")
                candidate["previous_weight"] = before_weights.get(code, 0.0)
                candidate["before_weight"] = before_weights.get(code, 0.0)
                candidate["target_weight"] = after.get(code, 0.0)
                candidates.append(candidate)
        before = _snapshot(before_date, before_weights)
        bundle = {
            "schema_version": "1",
            "signal_date": signal_date,
            "sequence": sequence,
            "quality": {
                "decision_status": str(decision.quality_status.value),
                "reasons": [str(decision.reason_code)] if decision.reason_code else [],
            },
            "before": before,
            "after_target": {
                **_snapshot(signal_date, after),
                "source": "TARGET",
                "as_of_signal_date": signal_date,
            },
            "decision": {
                "strategy": dict(strategy_metadata),
                "cluster_snapshot": trace.get("cluster_snapshot"),
                "candidates": candidates,
            },
            "execution": {
                "first_trade_date": _date_key(decision_fills[0].get("trade_date")) if decision_fills else None,
                "last_trade_date": _date_key(decision_fills[-1].get("trade_date")) if decision_fills else None,
                "orders": decision_orders,
                "fills": decision_fills,
                "summary": {
                    "filled": sum(1 for row in decision_fills if str(row.get("status", "FILLED")).upper() == "FILLED"),
                    "partial": sum(1 for row in decision_fills if str(row.get("status", "")).upper() == "PARTIAL"),
                    "blocked": sum(1 for row in decision_orders if str(row.get("status", "")).upper() in {"BLOCKED", "REJECTED"}),
                    "commission": sum(_finite(row.get("commission") or row.get("fee")) for row in decision_fills),
                    "turnover": execution_turnover,
                    "target_turnover": _turnover(previous_target, after),
                    "required_turnover": required_turnover,
                    "execution_turnover": execution_turnover,
                    "target_changed_positions": target_changed_positions,
                    "required_changed_positions": required_changed_positions,
                    "executed_changed_positions": executed_changed_positions,
                    "actual_changed_positions": executed_changed_positions,
                },
            },
        }
        bundles[signal_date] = bundle
        index.append(
            {
                "signal_date": signal_date,
                "sequence": sequence,
                "quality_status": str(decision.quality_status.value),
                "changed_positions": executed_changed_positions,
                "target_changed_positions": target_changed_positions,
                "required_changed_positions": required_changed_positions,
                "actual_changed_positions": executed_changed_positions,
                "executed_changed_positions": executed_changed_positions,
                "target_count": len(after),
                "turnover": execution_turnover,
                "target_turnover": _turnover(previous_target, after),
                "required_turnover": required_turnover,
                "execution_turnover": execution_turnover,
                "cash_target_weight": max(0.0, 1.0 - sum(after.values())),
                "cluster_snapshot_date": (trace.get("cluster_snapshot") or {}).get("snapshot_date") if isinstance(trace.get("cluster_snapshot"), Mapping) else None,
                "has_execution": bool(decision_orders or decision_fills),
            }
        )
        previous_target = after

    return {"schema_version": "1", "items": bundles, "index": index}


def _runtime_series_points(series: object) -> list[dict[str, Any]]:
    index = getattr(series, "index", ())
    values = getattr(series, "tolist", lambda: ())()
    points: list[dict[str, Any]] = []
    for raw_date, raw_value in zip(index, values):
        value = _finite(raw_value, math.nan)
        date = _date_key(raw_date)
        if date and math.isfinite(value):
            points.append({"date": date, "value": value})
    return points


def build_strategy_evidence(
    *,
    result: FundRotationRunResult,
    run_id: str,
    decision_trace: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Publish generic Strategy Score evidence without frontend recomputation."""
    trace = decision_trace if decision_trace is not None else result.decision_trace
    by_instrument: dict[str, dict[str, Any]] = {}
    for raw_decision in trace:
        if not isinstance(raw_decision, Mapping):
            continue
        date = _date_key(raw_decision.get("signal_date"))
        candidates = raw_decision.get("candidates", ())
        if not date or not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            continue
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, Mapping):
                continue
            code = str(raw_candidate.get("ts_code") or "").strip()
            if not code:
                continue
            instrument = by_instrument.setdefault(
                code,
                {"score": None, "score_components": {}, "legacy_indicators": {}},
            )
            stages = raw_candidate.get("stages")
            ranking_eligible = (
                bool(stages.get("ranking_eligible"))
                if isinstance(stages, Mapping)
                else True
            )
            score = raw_candidate.get("score")
            if isinstance(score, Mapping):
                value = _finite(score.get("value"), math.nan)
                if not math.isfinite(value):
                    continue
                score_entry = instrument.get("score")
                if not isinstance(score_entry, dict):
                    score_entry = {
                        "id": str(score.get("id") or "primary_score"),
                        "label": str(score.get("label") or "策略得分（周频）"),
                        "display_label": str(score.get("display_label") or score.get("label") or "策略得分（周频）"),
                        "model_label": str(score.get("model_label") or "Strategy Score"),
                        "frequency": str(score.get("frequency") or "WEEKLY"),
                        "direction": str(score.get("direction") or "HIGHER_BETTER"),
                        "scope": str(score.get("scope") or "UNKNOWN"),
                        "model_id": str(score.get("model_id") or "strategy_score"),
                        "model_version": str(score.get("model_version") or "1"),
                        "points": [],
                    }
                    instrument["score"] = score_entry
                score_entry["points"].append(
                    {
                        "date": date,
                        "value": value,
                        "eligible": bool(score.get("eligible", ranking_eligible)),
                        "rank": stages.get("rank") if isinstance(stages, Mapping) and bool(score.get("eligible", ranking_eligible)) else None,
                        "selected": bool(stages.get("portfolio_selected")) if isinstance(stages, Mapping) else False,
                        "subject_id": score.get("subject_id"),
                    }
                )
                for component_id, component_value in (score.get("components") or {}).items():
                    component_number = _finite(component_value, math.nan)
                    if not math.isfinite(component_number):
                        continue
                    component = instrument["score_components"].setdefault(
                        str(component_id),
                        {
                            "label": str(component_id),
                            "points": [],
                        },
                    )
                    component["points"].append({"date": date, "value": component_number})
                continue

            # Legacy v1 evidence is retained as an indicator only. It is not
            # promoted to a generic score when scope/representative identity is
            # unavailable.
            if isinstance(stages, Mapping) and not ranking_eligible:
                continue
            metric = raw_candidate.get("primary_metric")
            if not isinstance(metric, Mapping):
                continue
            metric_id = str(metric.get("id") or "strategy_metric").strip()
            label = str(metric.get("label") or metric_id).strip()
            value = _finite(metric.get("value"), math.nan)
            if not math.isfinite(value):
                continue
            series = instrument["legacy_indicators"].setdefault(
                metric_id,
                {
                    "id": metric_id,
                    "label": label,
                    "formula_id": f"strategy.{metric_id}",
                    "window": metric.get("window"),
                    "unit": str(metric.get("unit") or "score"),
                    "points": [],
                },
            )
            series["points"].append({"date": date, "value": value})

    benchmark_name: str | None = None
    benchmark_points: list[dict[str, Any]] = []
    for name, raw_series in result.benchmark_equity.items():
        benchmark_name = str(name)
        raw_points = _runtime_series_points(raw_series)
        baseline = next(
            (point["value"] for point in raw_points if point["value"] > 0),
            None,
        )
        if baseline:
            benchmark_points = [
                {**point, "value": point["value"] / baseline}
                for point in raw_points
            ]
        break

    benchmark = (
        {
            "ts_code": benchmark_name or "BENCHMARK",
            "name": benchmark_name,
            "normalized_price": benchmark_points,
        }
        if benchmark_points
        else None
    )
    instruments: dict[str, dict[str, Any]] = {}
    for code, evidence in by_instrument.items():
        indicators = []
        for series in evidence["legacy_indicators"].values():
            series["points"] = sorted(series["points"], key=lambda point: point["date"])
            indicators.append(series)
        score = evidence["score"]
        if score is None and not evidence["legacy_indicators"]:
            continue
        if isinstance(score, dict):
            score["points"] = sorted(score["points"], key=lambda point: point["date"])
        for component in evidence["score_components"].values():
            component["points"] = sorted(component["points"], key=lambda point: point["date"])
        instruments[code] = {
            "schema_version": "2",
            "benchmark": benchmark,
            "indicators": sorted(indicators, key=lambda item: item["id"]),
            "score": score,
            "score_components": evidence["score_components"],
            "evidence_version": "2",
        }
    return {
        "schema_version": "2",
        "run_id": run_id,
        "instruments": instruments,
    }
