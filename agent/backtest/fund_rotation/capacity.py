"""ADV20 capacity and slippage — §13.3.

Causal ADV: excludes execution day. Slippage formula:
slippage_bps = min(max_bps, base_bps + 200 * participation_rate).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CapacityEstimate:
    """Fail-closed, share-quantity capacity estimate for one candidate."""

    capacity_quantity: int
    adv: float | None
    max_participation: float | None
    execution_horizon: float | None
    lot_size: int | None
    is_valid: bool
    reason_code: str

    @property
    def capacity_shares(self) -> int:
        return self.capacity_quantity


@dataclass(frozen=True)
class RepresentativeSelection:
    """Deterministic representative decision and its audit evidence."""

    selected_representative: str | None
    target_quantity: int
    filled_quantity: int
    used_fallback: bool
    reason_code: str
    candidates_considered: tuple[str, ...]
    diagnostics: dict[str, object]

    @property
    def representative(self) -> str | None:
        return self.selected_representative


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def estimate_capacity(
    adv: object,
    max_participation: object,
    execution_horizon: object,
    lot_size: object,
    tradable_state: object,
) -> CapacityEstimate:
    """Estimate executable shares using only explicit, causal evidence.

    ``adv`` is average daily shares, ``execution_horizon`` is the number of
    trading periods available for execution, and all quantities are rounded
    down to ``lot_size``.  Unknown or invalid inputs never become unlimited
    capacity.
    """
    adv_value = _finite_number(adv)
    participation = _finite_number(max_participation)
    horizon = _finite_number(execution_horizon)
    lot_value = _finite_number(lot_size)
    lot = int(lot_value) if lot_value is not None and lot_value.is_integer() else None
    if not isinstance(tradable_state, bool) or tradable_state is False:
        return CapacityEstimate(0, adv_value, participation, horizon, lot, False,
                                 "CAPACITY_EVIDENCE_UNAVAILABLE"
                                 if tradable_state is None else "NOT_TRADABLE")
    if (
        adv_value is None or adv_value < 0.0
        or participation is None or not 0.0 <= participation <= 1.0
        or horizon is None or horizon <= 0.0
        or lot is None or lot <= 0
    ):
        return CapacityEstimate(0, adv_value, participation, horizon, lot, False,
                                 "CAPACITY_EVIDENCE_UNAVAILABLE")
    try:
        raw_capacity = adv_value * participation * horizon
    except (OverflowError, ValueError):
        return CapacityEstimate(0, adv_value, participation, horizon, lot, False,
                                 "CAPACITY_EVIDENCE_UNAVAILABLE")
    if not math.isfinite(raw_capacity):
        return CapacityEstimate(0, adv_value, participation, horizon, lot, False,
                                 "CAPACITY_EVIDENCE_UNAVAILABLE")
    capacity = int(raw_capacity // lot) * lot
    if capacity <= 0:
        return CapacityEstimate(capacity, adv_value, participation, horizon, lot, True,
                                 "CAPACITY_ZERO")
    return CapacityEstimate(capacity, adv_value, participation, horizon, lot, True,
                             "CAPACITY_AVAILABLE")


def _candidate_value(candidate: Mapping[str, object], *names: str) -> object:
    for name in names:
        if name in candidate:
            return candidate[name]
    return None


def _candidate_code(candidate: Mapping[str, object]) -> str | None:
    code = _candidate_value(candidate, "code", "ts_code", "representative")
    return code if isinstance(code, str) and code else None


def _parse_causal_timestamp(value: object) -> tuple[pd.Timestamp, bool] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    date_formats = ("%Y%m%d", "%Y-%m-%d")
    time_formats = (
        "%Y%m%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )
    if re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", text):
        for date_format in date_formats:
            try:
                return pd.Timestamp(datetime.strptime(text, date_format)), False
            except ValueError:
                continue
    if re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}", text):
        for time_format in time_formats:
            try:
                return pd.Timestamp(datetime.strptime(text, time_format)), True
            except ValueError:
                continue
    return None


def _strict_non_negative_int(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or not number.is_integer() or number < 0:
        return None
    return int(number)


def select_capacity_aware_representative(
    candidates: object,
    target_quantity: object,
    market_observation: object,
    prior_representative: object,
) -> RepresentativeSelection:
    """Select a same-cluster/identity representative with deterministic fallback.

    Candidate records are mappings supplied by the caller's frozen U1 view.
    The decision cutoff and volume date are checked before capacity is used;
    therefore future volume cannot unlock a representative.
    """
    try:
        target_number = _finite_number(target_quantity)
        target = int(target_number) if target_number is not None and target_number.is_integer() else -1
    except (TypeError, ValueError, OverflowError):
        target = -1
    if target <= 0 or not isinstance(market_observation, Mapping):
        return RepresentativeSelection(None, max(target, 0), 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    required_market_fields = (
        "max_participation",
        "execution_horizon",
        "lot_size",
    )
    if any(field not in market_observation for field in required_market_fields):
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    if not isinstance(candidates, (list, tuple)):
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})

    max_participation = market_observation["max_participation"]
    execution_horizon = market_observation["execution_horizon"]
    market_lot_size = market_observation["lot_size"]
    participation_value = _finite_number(max_participation)
    horizon_value = _finite_number(execution_horizon)
    lot_value = _finite_number(market_lot_size)
    if (
        participation_value is None
        or not 0.0 <= participation_value <= 1.0
        or horizon_value is None
        or horizon_value <= 0.0
        or lot_value is None
        or not lot_value.is_integer()
        or lot_value <= 0
    ):
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    cutoff_parsed = _parse_causal_timestamp(
        _candidate_value(market_observation, "decision_cutoff", "cutoff")
    )
    if cutoff_parsed is None:
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    cutoff_timestamp, cutoff_has_time = cutoff_parsed
    if not cutoff_has_time:
        cutoff_timestamp = cutoff_timestamp + pd.Timedelta(hours=15)
    anti_flap_periods = _strict_non_negative_int(
        market_observation.get("anti_flap_periods", 0)
    )
    prior_periods_held = _strict_non_negative_int(
        market_observation.get("prior_periods_held", 0)
    )
    if anti_flap_periods is None or prior_periods_held is None:
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    prior = prior_representative if isinstance(prior_representative, str) else None
    candidate_records = [raw for raw in candidates if isinstance(raw, Mapping)]
    prior_record = next(
        (raw for raw in candidate_records if _candidate_code(raw) == prior),
        None,
    )
    scope_cluster_key = next(
        (key for key in ("target_cluster_id", "cluster_id") if key in market_observation),
        None,
    )
    scope_identity_key = next(
        (key for key in ("target_identity_key", "identity_key") if key in market_observation),
        None,
    )
    if (scope_cluster_key is None) != (scope_identity_key is None):
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    scope_cluster = (
        market_observation.get(scope_cluster_key)
        if scope_cluster_key is not None
        else None
    )
    scope_identity = (
        market_observation.get(scope_identity_key)
        if scope_identity_key is not None
        else None
    )
    if (
        scope_cluster_key is not None
        and (scope_cluster is None or scope_identity is None)
    ):
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    explicit_scope = scope_cluster is not None and scope_identity is not None
    if prior_record is not None and not explicit_scope:
        scope_cluster = _candidate_value(prior_record, "cluster_id", "cluster")
        scope_identity = _candidate_value(prior_record, "identity_key", "identity")
    if scope_cluster is None or scope_identity is None:
        return RepresentativeSelection(None, target, 0, False,
                                       "CAPACITY_EVIDENCE_UNAVAILABLE", (),
                                       {"blocked_codes": [], "capacity_status": "unavailable"})
    normalized: list[tuple[str, Mapping[str, object], CapacityEstimate]] = []
    blocked: list[str] = []
    blocked_reasons: dict[str, str] = {}
    for raw in candidate_records:
        code = _candidate_code(raw)
        if code is None:
            continue
        if (
            _candidate_value(raw, "cluster_id", "cluster") != scope_cluster
            or _candidate_value(raw, "identity_key", "identity") != scope_identity
        ):
            blocked.append(code)
            blocked_reasons[code] = "OUT_OF_SCOPE"
            continue
        if raw.get("visible") is not True:
            blocked.append(code)
            blocked_reasons[code] = "NOT_VISIBLE_AT_CUTOFF"
            continue
        volume_parsed = _parse_causal_timestamp(raw.get("volume_date"))
        known_parsed = [
            _parse_causal_timestamp(raw.get(date_name))
            for date_name in ("known_at", "as_of_date")
        ]
        if volume_parsed is None:
            blocked.append(code)
            blocked_reasons[code] = "INVALID_VOLUME_DATE"
            continue
        if any(value is None for value in known_parsed):
            blocked.append(code)
            blocked_reasons[code] = "INVALID_KNOWN_TIME"
            continue
        volume_date, volume_has_time = volume_parsed
        volume_end = (
            volume_date + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            if not volume_has_time
            else volume_date
        )
        known_dates = [value[0] for value in known_parsed if value is not None]
        if volume_date > cutoff_timestamp:
            blocked.append(code)
            blocked_reasons[code] = "FUTURE_VOLUME"
            continue
        if any(value > cutoff_timestamp for value in known_dates):
            blocked.append(code)
            blocked_reasons[code] = "NOT_VISIBLE_AT_CUTOFF"
            continue
        if any(value > volume_end for value in known_dates):
            blocked.append(code)
            blocked_reasons[code] = "INVALID_KNOWN_TIME"
            continue
        estimate = estimate_capacity(
            _candidate_value(raw, "adv", "adv_shares", "average_daily_volume"),
            max_participation,
            execution_horizon,
            market_lot_size,
            raw.get("tradable"),
        )
        normalized.append((code, raw, estimate))

    def sort_key(item: tuple[str, Mapping[str, object], CapacityEstimate]) -> tuple[float, str]:
        score = _finite_number(_candidate_value(item[1], "score", "rank"))
        return (-(score if score is not None else float("-inf")), item[0])

    normalized.sort(key=sort_key)
    considered = tuple(code for code, _, _ in normalized) + tuple(code for code in blocked if code not in {x[0] for x in normalized})
    by_code = {code: estimate for code, _, estimate in normalized}
    anti_flap_active = prior is not None and prior_periods_held < anti_flap_periods
    if prior in by_code:
        prior_estimate = by_code[prior]
        if prior_estimate.is_valid and prior_estimate.capacity_quantity >= target:
            return RepresentativeSelection(prior, target, target, False, "CAPACITY_CARRY", considered,
                                           {"blocked_codes": blocked, "blocked_reasons": blocked_reasons,
                                            "capacity_status": "selected", "capacity_quantity": prior_estimate.capacity_quantity,
                                            "anti_flap_active": anti_flap_active,
                                            "anti_flap_periods_remaining": max(0, anti_flap_periods - prior_periods_held)})
        if (
            anti_flap_active
            and prior_estimate.is_valid
            and prior_estimate.capacity_quantity > 0
        ):
            return RepresentativeSelection(
                prior,
                target,
                min(target, prior_estimate.capacity_quantity),
                False,
                "CAPACITY_ANTIFLAP_CARRY",
                considered,
                {
                    "blocked_codes": blocked,
                    "blocked_reasons": blocked_reasons,
                    "capacity_status": "partially_selected",
                    "capacity_quantity": prior_estimate.capacity_quantity,
                    "capacity_limited": True,
                    "anti_flap_active": anti_flap_active,
                    "anti_flap_periods_remaining": max(
                        0, anti_flap_periods - prior_periods_held
                    ),
                },
            )
        blocked.append(prior)
        blocked_reasons[prior] = prior_estimate.reason_code if prior_estimate.capacity_quantity == 0 else "CAPACITY_INSUFFICIENT"

    for code, _, estimate in normalized:
        if estimate.is_valid and estimate.capacity_quantity >= target:
            return RepresentativeSelection(code, target, target, code != prior, "CAPACITY_FALLBACK" if code != prior else "CAPACITY_CARRY", considered,
                                           {"blocked_codes": blocked, "blocked_reasons": blocked_reasons,
                                            "capacity_status": "selected", "capacity_quantity": estimate.capacity_quantity,
                                            "anti_flap_active": anti_flap_active,
                                            "anti_flap_periods_remaining": max(0, anti_flap_periods - prior_periods_held)})
        if code not in blocked:
            blocked.append(code)
        blocked_reasons[code] = estimate.reason_code if estimate.capacity_quantity == 0 else "CAPACITY_INSUFFICIENT"
    return RepresentativeSelection(None, target, 0, False, "CAPACITY_CASH_FALLBACK", considered,
                                   {"blocked_codes": blocked, "blocked_reasons": blocked_reasons,
                                    "capacity_status": "cash",
                                    "anti_flap_active": anti_flap_active,
                                    "anti_flap_periods_remaining": max(0, anti_flap_periods - prior_periods_held)})


@dataclass(frozen=True)
class ADVResult:
    """Result of causal ADV computation."""

    adv_value: float  # CNY
    observations: int
    is_valid: bool
    as_of_date: str
    lookback: int


def compute_adv20(
    market: pd.DataFrame,
    code: str,
    as_of_date: str,
    lookback: int = 20,
    min_obs: int = 10,
    amount_multiplier: float = 1000.0,
) -> ADVResult:
    """§13.3 — Compute causal ADV (average daily amount).

    Uses data strictly BEFORE as_of_date (excludes execution day).

    Args:
        market: DataFrame with [ts_code, trade_date, amount].
        code: ETF code.
        as_of_date: Execution date (excluded from window).
        lookback: Max days to look back.
        min_obs: Minimum valid observations.
        amount_multiplier: Convert stored amount unit to CNY.
            Tushare fund_daily stores amount in 千元 (thousands),
            so default 1000.0 converts to 元. Set to 1.0 if already in CNY.

    Returns:
        ADVResult with adv_value in CNY.
    """
    if market.empty or "ts_code" not in market.columns:
        return ADVResult(adv_value=0.0, observations=0, is_valid=False,
                         as_of_date=as_of_date, lookback=lookback)

    stock = market[market["ts_code"].astype(str) == str(code)].copy()
    if stock.empty:
        return ADVResult(adv_value=0.0, observations=0, is_valid=False,
                         as_of_date=as_of_date, lookback=lookback)

    # Strictly before execution day
    stock = stock[stock["trade_date"].astype(str) < as_of_date]
    stock = stock.sort_values("trade_date")

    # Take last `lookback` days
    window = stock.tail(lookback)

    # Filter valid amounts (non-null, non-negative) and convert to CNY
    amounts = window["amount"].dropna()
    amounts = amounts[amounts >= 0] * amount_multiplier
    obs = len(amounts)

    if obs < min_obs:
        return ADVResult(adv_value=0.0, observations=obs, is_valid=False,
                         as_of_date=as_of_date, lookback=lookback)

    adv = float(amounts.mean())
    return ADVResult(adv_value=adv, observations=obs, is_valid=True,
                     as_of_date=as_of_date, lookback=lookback)


def apply_capacity_and_slippage(
    requested_shares: int,
    price: float,
    adv_value: float,
    max_participation: float,
    lot_size: int,
    base_slippage_bps: float,
    max_slippage_bps: float,
) -> tuple[int, float, float]:
    """§13.3 — Apply ADV capacity cap and compute slippage.

    Args:
        requested_shares: Desired trade size.
        price: Execution price.
        adv_value: ADV in CNY (0 = invalid).
        max_participation: Max fraction of ADV (e.g. 0.05).
        lot_size: Minimum trade unit.
        base_slippage_bps: Base slippage in bps.
        max_slippage_bps: Cap on slippage.

    Returns:
        (filled_shares, participation_rate, slippage_bps)
    """
    if adv_value <= 0 or price <= 0 or requested_shares <= 0:
        return (0, 0.0, 0.0)

    # Capacity: max notional = participation * ADV
    max_notional = max_participation * adv_value
    max_shares = int(max_notional / price)
    # Round down to lot
    max_shares = (max_shares // lot_size) * lot_size

    filled = min(requested_shares, max_shares)
    if filled <= 0:
        return (0, 0.0, 0.0)

    # Participation rate of actual fill
    fill_notional = filled * price
    participation = fill_notional / adv_value

    # Slippage formula
    slippage_bps = min(max_slippage_bps, base_slippage_bps + 200.0 * participation)

    return (filled, participation, slippage_bps)


class ADVIndex:
    """Pre-computed causal ADV index for fast lookup.

    Semantics identical to compute_adv20: for a given (code, trade_date),
    returns the mean amount over the last `lookback` trading days STRICTLY
    BEFORE trade_date, requiring at least `min_obs` valid observations.

    Implementation: per-code sorted date array + rolling mean/count (no shift).
    Query uses searchsorted to find the last row with date < trade_date.
    """

    def __init__(
        self,
        adv_grouped: dict[str, pd.DataFrame],
        lookback: int,
        min_obs: int,
        amount_multiplier: float = 1000.0,
    ):
        self._lookback = lookback
        self._min_obs = min_obs
        self._amount_multiplier = amount_multiplier
        # Per-code compact arrays
        self._data: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._build(adv_grouped)

    def _build(self, adv_grouped: dict[str, pd.DataFrame]) -> None:
        for code, df in adv_grouped.items():
            if df.empty or "trade_date" not in df.columns or "amount" not in df.columns:
                continue
            sorted_df = df.sort_values("trade_date")
            dates = sorted_df["trade_date"].astype(str).values
            amounts = pd.to_numeric(sorted_df["amount"], errors="coerce").copy()
            # Invalid amounts (NaN, negative) -> NaN
            amounts[amounts < 0] = np.nan
            # Rolling mean and count (no shift)
            rolling_mean = amounts.rolling(self._lookback, min_periods=1).mean()
            rolling_count = amounts.rolling(self._lookback, min_periods=1).count()
            self._data[code] = (
                dates,
                (rolling_mean * self._amount_multiplier).values,
                rolling_count.values.astype(np.int64),
            )

    def get(self, code: str, trade_date: str) -> ADVResult:
        """Lookup causal ADV for (code, trade_date).

        Returns ADVResult with adv_value in CNY, strictly excluding trade_date.
        """
        entry = self._data.get(code)
        if entry is None:
            return ADVResult(
                adv_value=0.0, observations=0, is_valid=False,
                as_of_date=trade_date, lookback=self._lookback,
            )
        dates, means, counts = entry
        # searchsorted 'left': first index where dates[i] >= trade_date
        i = int(np.searchsorted(dates, trade_date, side="left")) - 1
        if i < 0:
            return ADVResult(
                adv_value=0.0, observations=0, is_valid=False,
                as_of_date=trade_date, lookback=self._lookback,
            )
        obs = int(counts[i])
        if obs < self._min_obs:
            return ADVResult(
                adv_value=0.0, observations=obs, is_valid=False,
                as_of_date=trade_date, lookback=self._lookback,
            )
        return ADVResult(
            adv_value=float(means[i]), observations=obs, is_valid=True,
            as_of_date=trade_date, lookback=self._lookback,
        )
