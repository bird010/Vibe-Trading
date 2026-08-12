"""Fund-rotation attribution vertical slice.

This module keeps the first attribution layer deliberately small and
deterministic: it exposes accounting primitives, fixed counterfactual chains,
and quality gates that can be exercised without the full fund-rotation runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


ACCOUNTING_CONTRACT_VERSION = "daily_accounting_v1"
RECONCILIATION_FAILED = "ATTRIBUTION_RECONCILIATION_FAILED"
OPEN_PRICE_DEGRADED = "DEGRADED_OPEN_PRICE_UNAVAILABLE"


class AttributionContractError(ValueError):
    """Raised when attribution inputs violate the declared experiment contract."""


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    valuation_price: float


@dataclass(frozen=True)
class Fill:
    symbol: str
    quantity: float
    executed_price: float
    reference_price: float | None = None
    commission: float = 0.0
    other_fee: float = 0.0


@dataclass(frozen=True)
class PricePoint:
    prior_close: float | None
    open_price: float | None
    close_price: float | None


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    pre_quantity: float
    pre_price: float
    post_quantity: float
    post_price: float
    cash_in_lieu: float = 0.0


@dataclass(frozen=True)
class AccountDayInput:
    begin_cash: float
    begin_positions: tuple[Position, ...] = ()
    end_positions: tuple[Position, ...] = ()
    prices: Mapping[str, PricePoint] = field(default_factory=dict)
    fills: tuple[Fill, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    cash_income: float = 0.0
    tolerance: float = 1e-9


@dataclass(frozen=True)
class ReconciliationResult:
    actual_nav_change: float
    attributed_total: float
    residual: float
    tolerance: float
    status: str
    publishable: bool


@dataclass(frozen=True)
class AccountingDayResult:
    accounting_contract_version: str
    beginning_nav: float
    ending_cash: float
    ending_nav: float
    actual_nav_change: float
    pnl_components: dict[str, float | None]
    corporate_action_economic_effects: dict[str, float]
    reconciliation: ReconciliationResult
    quality_status: str
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class VariantSnapshot:
    name: str
    ideal_target_return: float
    executable_return: float
    identity: Mapping[str, object] = field(default_factory=dict)
    declared_differences: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainEffects:
    chain: tuple[str, ...]
    effects: dict[str, float]


@dataclass(frozen=True)
class CashAttributionEvent:
    amount: float
    reason: str


@dataclass(frozen=True)
class DrawdownAttribution:
    peak: str
    trough: str
    recovery: str | None
    max_drawdown: float
    component_contributions: dict[str, float]


@dataclass(frozen=True)
class RegimeAttribution:
    label: str
    regime_kind: str
    can_drive_trading: bool
    days: int
    total_return: float


def compute_accounting_day(day: AccountDayInput) -> AccountingDayResult:
    """Compute the real cash/NAV bridge and daily P&L attribution."""
    begin_positions = {position.symbol: position for position in day.begin_positions}
    end_positions = {position.symbol: position for position in day.end_positions}
    actions = {action.symbol: action for action in day.corporate_actions}

    beginning_nav = day.begin_cash + sum(
        position.quantity * position.valuation_price for position in day.begin_positions
    )

    sell_proceeds = sum(-fill.quantity * fill.executed_price for fill in day.fills if fill.quantity < 0)
    buy_cost = sum(fill.quantity * fill.executed_price for fill in day.fills if fill.quantity > 0)
    commission = sum(fill.commission for fill in day.fills)
    other_fee = sum(fill.other_fee for fill in day.fills)
    total_fees = commission + other_fee
    cash_in_lieu = sum(action.cash_in_lieu for action in day.corporate_actions)
    ending_cash = day.begin_cash + day.cash_income + cash_in_lieu + sell_proceeds - buy_cost - commission - other_fee
    ending_nav = ending_cash + sum(position.quantity * position.valuation_price for position in day.end_positions)
    actual_nav_change = ending_nav - beginning_nav

    corporate_effects = {
        action.symbol: (
            action.post_quantity * action.post_price
            + action.cash_in_lieu
            - action.pre_quantity * action.pre_price
        )
        for action in day.corporate_actions
    }
    corporate_effect = sum(corporate_effects.values())

    flags: list[str] = []
    invalid = False
    overnight_pnl: float | None = 0.0
    holding_intraday_pnl: float | None = 0.0
    holding_close_to_close_pnl: float | None = None

    symbols = set(begin_positions) | set(end_positions) | set(day.prices)
    for symbol in sorted(symbols):
        price = day.prices.get(symbol)
        if price is None:
            continue
        if price.close_price is None:
            flags.append(f"INVALID_CLOSE_PRICE_UNAVAILABLE:{symbol}")
            invalid = True
            continue
        q_after_action = _post_action_quantity(symbol, begin_positions, actions)
        if price.open_price is None:
            flags.append(f"{OPEN_PRICE_DEGRADED}:{symbol}")
            if price.prior_close is not None:
                if holding_close_to_close_pnl is None:
                    holding_close_to_close_pnl = 0.0
                holding_close_to_close_pnl += q_after_action * (price.close_price - price.prior_close)
            continue
        if price.prior_close is not None and overnight_pnl is not None:
            overnight_pnl += q_after_action * (price.open_price - price.prior_close)
        if holding_intraday_pnl is not None:
            holding_intraday_pnl += q_after_action * (price.close_price - price.open_price)

    trade_day_pnl = 0.0
    for fill in day.fills:
        price = day.prices.get(fill.symbol)
        if price is not None and price.close_price is not None:
            trade_day_pnl += fill.quantity * (price.close_price - fill.executed_price)

    pnl_components: dict[str, float | None] = {
        "corporate_action_economic_effect": corporate_effect,
        "overnight_pnl": overnight_pnl,
        "holding_intraday_pnl": holding_intraday_pnl,
        "trade_day_pnl": trade_day_pnl,
        "income": day.cash_income,
        "fees": -total_fees,
    }
    if holding_close_to_close_pnl is not None:
        pnl_components["holding_close_to_close_pnl"] = holding_close_to_close_pnl

    reconciliation = reconcile_attribution(
        actual_nav_change=actual_nav_change,
        attributed_components=pnl_components,
        tolerance=day.tolerance,
    )
    quality_status = "INVALID" if invalid else reconciliation.status
    return AccountingDayResult(
        accounting_contract_version=ACCOUNTING_CONTRACT_VERSION,
        beginning_nav=beginning_nav,
        ending_cash=ending_cash,
        ending_nav=ending_nav,
        actual_nav_change=actual_nav_change,
        pnl_components=pnl_components,
        corporate_action_economic_effects=corporate_effects,
        reconciliation=reconciliation,
        quality_status=quality_status,
        quality_flags=tuple(flags),
    )


def reconcile_attribution(
    actual_nav_change: float,
    attributed_components: Mapping[str, float | None],
    tolerance: float = 1e-9,
) -> ReconciliationResult:
    """Gate attribution publication on residual tolerance."""
    attributed_total = sum(value for value in attributed_components.values() if isinstance(value, int | float))
    residual = actual_nav_change - attributed_total
    publishable = abs(residual) <= tolerance
    status = "OK" if publishable else RECONCILIATION_FAILED
    return ReconciliationResult(
        actual_nav_change=actual_nav_change,
        attributed_total=attributed_total,
        residual=residual,
        tolerance=tolerance,
        status=status,
        publishable=publishable,
    )


def compute_execution_drag(
    actual_executed_account_return: float,
    reference_price_account_return: float,
) -> dict[str, float]:
    """Return signed execution effect and the equal/opposite drag convention."""
    effect = actual_executed_account_return - reference_price_account_return
    return {
        "execution_effect_return": effect,
        "execution_drag_return": -effect,
    }


def compute_strategy_component_effects(variants: Mapping[str, VariantSnapshot]) -> ChainEffects:
    """Compute fixed S0-S6 ideal/executable component effects."""
    chain = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
    _require_chain(variants, chain)
    _require_shared_identity(variants, chain)
    _require_declared_differences(
        variants,
        {
            "S1": ("momentum",),
            "S2": ("clustering",),
            "S3": ("representative_etf",),
            "S4": ("quality_selection",),
            "S5": ("weighting",),
            "S6": ("risk",),
        },
    )

    effects: dict[str, float] = {}
    for previous, current in zip(chain[:-1], chain[1:], strict=True):
        effects[f"ideal_component_effect_{current}"] = (
            variants[current].ideal_target_return - variants[previous].ideal_target_return
        )
        effects[f"executable_component_effect_{current}"] = (
            variants[current].executable_return - variants[previous].executable_return
        )

    effects["ideal_weighting_effect"] = variants["S5"].ideal_target_return - variants["S4"].ideal_target_return
    effects["executable_weighting_effect"] = variants["S5"].executable_return - variants["S4"].executable_return
    effects["ideal_risk_effect"] = variants["S6"].ideal_target_return - variants["S5"].ideal_target_return
    effects["executable_risk_effect"] = variants["S6"].executable_return - variants["S5"].executable_return
    return ChainEffects(chain=chain, effects=effects)


def compute_execution_ladder_effects(variants: Mapping[str, VariantSnapshot]) -> ChainEffects:
    """Compute fixed X0-X5 cumulative effect plus incremental effect/drag."""
    chain = ("X0", "X1", "X2", "X3", "X4", "X5")
    _require_chain(variants, chain)
    target_hash = variants["X0"].identity.get("strategy_target_hash")
    for name in chain[1:]:
        if variants[name].identity.get("strategy_target_hash") != target_hash:
            raise AttributionContractError("execution ladder variants must share the same strategy target")
    _require_declared_differences(
        variants,
        {
            "X1": ("tradability_filter",),
            "X2": ("limit_and_suspend_filter",),
            "X3": ("lot_rounding",),
            "X4": ("capacity",),
            "X5": ("fees_and_slippage",),
        },
    )

    effects: dict[str, float] = {}
    base_return = variants["X0"].executable_return
    for previous, current in zip(chain[:-1], chain[1:], strict=True):
        current_return = variants[current].executable_return
        previous_return = variants[previous].executable_return
        effects[f"cumulative_execution_effect_{current}"] = current_return - base_return
        effects[f"incremental_execution_effect_{current}"] = current_return - previous_return
        effects[f"incremental_execution_drag_{current}"] = previous_return - current_return
    return ChainEffects(chain=chain, effects=effects)


def classify_cash_events(events: tuple[CashAttributionEvent, ...]) -> dict[str, float]:
    """Separate cash caused by strategic choices from cash forced by execution/data limits."""
    intentional_reasons = {"momentum_threshold", "risk_state", "vol_target", "active_allocation"}
    unintentional_reasons = {
        "representative_missing",
        "data_gate",
        "blocked_order",
        "capacity",
        "odd_lot",
        "execution_failed",
    }
    totals = {"intentional_cash": 0.0, "unintentional_cash": 0.0}
    for event in events:
        if event.reason in intentional_reasons:
            totals["intentional_cash"] += event.amount
        elif event.reason in unintentional_reasons:
            totals["unintentional_cash"] += event.amount
        else:
            totals["unintentional_cash"] += event.amount
    return totals


def compute_drawdown_episodes(
    equity: list[tuple[str, float]],
    component_contributions: Mapping[str, Mapping[str, float]],
) -> list[DrawdownAttribution]:
    """Return recovered drawdown episodes with basic component sums."""
    if not equity:
        return []

    episodes: list[DrawdownAttribution] = []
    peak_index = 0
    peak_date, peak_value = equity[0]
    trough_index: int | None = None
    trough_date = peak_date
    trough_value = peak_value

    for index, (date, value) in enumerate(equity[1:], start=1):
        if value >= peak_value:
            if trough_index is not None and trough_value < peak_value:
                episodes.append(
                    _build_drawdown_episode(
                        equity=equity,
                        peak_index=peak_index,
                        trough_index=trough_index,
                        recovery=date,
                        component_contributions=component_contributions,
                    )
                )
            peak_index = index
            peak_date, peak_value = date, value
            trough_index = None
            trough_date, trough_value = date, value
            continue
        if trough_index is None or value < trough_value:
            trough_index = index
            trough_date, trough_value = date, value

    if trough_index is not None:
        episodes.append(
            _build_drawdown_episode(
                equity=equity,
                peak_index=peak_index,
                trough_index=trough_index,
                recovery=None,
                component_contributions=component_contributions,
            )
        )
    return episodes


def compute_regime_attribution(
    period_returns: Mapping[str, float],
    labels: Mapping[str, str],
    regime_kind: str,
    can_drive_trading: bool = False,
) -> dict[str, RegimeAttribution]:
    """Aggregate returns by regime and keep post-hoc analytics out of trading."""
    if regime_kind == "POST_HOC_ANALYTICS_ONLY" and can_drive_trading:
        raise AttributionContractError("post-hoc regimes cannot drive trading")

    grouped: dict[str, list[float]] = {}
    for date, value in period_returns.items():
        label = labels[date]
        grouped.setdefault(label, []).append(value)

    return {
        label: RegimeAttribution(
            label=label,
            regime_kind=regime_kind,
            can_drive_trading=can_drive_trading,
            days=len(values),
            total_return=sum(values),
        )
        for label, values in grouped.items()
    }


def compute_concentration_metrics(
    period_contributions: Mapping[str, float],
    top_n: int,
    warning_threshold: float,
) -> dict[str, float | str]:
    """Measure whether a small number of periods dominate total contribution."""
    if top_n <= 0:
        raise AttributionContractError("top_n must be positive")
    total_abs = sum(abs(value) for value in period_contributions.values())
    if total_abs == 0:
        top_share = 0.0
    else:
        top_share = sum(sorted((abs(value) for value in period_contributions.values()), reverse=True)[:top_n]) / total_abs
    return {
        "top_contribution_share": top_share,
        "quality_flag": "RETURN_CONCENTRATION_WARNING" if top_share > warning_threshold else "OK",
    }


def _post_action_quantity(
    symbol: str,
    begin_positions: Mapping[str, Position],
    actions: Mapping[str, CorporateAction],
) -> float:
    action = actions.get(symbol)
    if action is not None:
        return action.post_quantity
    position = begin_positions.get(symbol)
    return 0.0 if position is None else position.quantity


def _require_chain(variants: Mapping[str, VariantSnapshot], chain: tuple[str, ...]) -> None:
    missing = [name for name in chain if name not in variants]
    if missing:
        raise AttributionContractError(f"missing variants: {', '.join(missing)}")


def _require_shared_identity(variants: Mapping[str, VariantSnapshot], chain: tuple[str, ...]) -> None:
    first = dict(variants[chain[0]].identity)
    for name in chain[1:]:
        if dict(variants[name].identity) != first:
            raise AttributionContractError("strategy component variants must share shared identity fields")


def _require_declared_differences(
    variants: Mapping[str, VariantSnapshot],
    allowed_by_variant: Mapping[str, tuple[str, ...]],
) -> None:
    for name, allowed in allowed_by_variant.items():
        declared = variants[name].declared_differences
        if declared != allowed:
            raise AttributionContractError(f"{name} declared differences must be exactly {allowed}")


def _build_drawdown_episode(
    equity: list[tuple[str, float]],
    peak_index: int,
    trough_index: int,
    recovery: str | None,
    component_contributions: Mapping[str, Mapping[str, float]],
) -> DrawdownAttribution:
    peak_date, peak_value = equity[peak_index]
    trough_date, trough_value = equity[trough_index]
    dates = {date for date, _value in equity[peak_index + 1 : trough_index + 1]}
    return DrawdownAttribution(
        peak=peak_date,
        trough=trough_date,
        recovery=recovery,
        max_drawdown=(trough_value / peak_value) - 1.0,
        component_contributions={
            component: sum(value for date, value in by_date.items() if date in dates)
            for component, by_date in component_contributions.items()
        },
    )
