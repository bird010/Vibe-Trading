"""Single-date StockPred Graph signal orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from src.stockpred.contracts import StockPredDataError
from src.stockpred.gateway import StockPredDataGateway
from src.stockpred.graph.adjustment import AdjustmentQuality, apply_qfq
from src.stockpred.graph.advisor import generate_advisory
from src.stockpred.graph.builder import build_daily_graph
from src.stockpred.graph.config import GraphConfig, PredictionConfig
from src.stockpred.graph.features import compute_all_graph_features
from src.stockpred.graph.portfolio import rank_signals
from src.stockpred.graph.predictor import predict_batch_vectorized
from src.stockpred.graph.universe import build_pit_universe


@dataclass(frozen=True)
class GraphSignalConfig:
    """Inputs needed to evaluate the frozen Graph model on one open date."""

    data_lookback_days: int = 180
    min_listed_trade_days: int = 60
    min_adj_coverage: float = 0.98
    benchmark_code: str = "000300.SH"
    exclude_st: bool = True
    require_pit_industry: bool = True
    allowed_exchanges: tuple[str, ...] = ("SSE", "SZSE")
    graph: GraphConfig = field(default_factory=GraphConfig)
    prediction: PredictionConfig = field(default_factory=PredictionConfig)


def _date(value: str) -> str:
    normalized = str(value).replace("-", "")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise StockPredDataError(
            "STOCKPRED_FILTER_INVALID",
            f"invalid evaluation date: {value!r}",
        ) from exc
    return normalized


def _calendar_start(eval_date: str, days: int) -> str:
    return (
        datetime.strptime(eval_date, "%Y%m%d") - timedelta(days=days)
    ).strftime("%Y%m%d")


def _filter_adjustment_complete(
    universe: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    lookback_rows: int,
    min_coverage: float,
) -> tuple[pd.DataFrame, pd.DataFrame, AdjustmentQuality]:
    expected_codes = set(universe["ts_code"].astype(str))
    if not expected_codes:
        quality = AdjustmentQuality(1.0, 0, 0, True)
        return universe.copy(), prices.iloc[0:0].copy(), quality
    recent = (
        prices[prices["ts_code"].astype(str).isin(expected_codes)]
        .sort_values(["ts_code", "trade_date"], kind="stable")
        .groupby("ts_code", group_keys=False)
        .tail(max(int(lookback_rows), 1))
    )
    present_codes = set(recent["ts_code"].astype(str))
    missing = recent["adj_factor_missing"].fillna(True) | recent["adj_close"].isna()
    incomplete_codes = set(recent.loc[missing, "ts_code"].astype(str))
    incomplete_codes.update(expected_codes - present_codes)
    complete_codes = expected_codes - incomplete_codes
    coverage = len(complete_codes) / len(expected_codes)
    quality = AdjustmentQuality(
        coverage=float(coverage),
        missing_rows=int(missing.sum()),
        missing_stocks=len(incomplete_codes),
        passed=coverage >= float(min_coverage),
    )
    if not quality.passed:
        raise StockPredDataError(
            "STOCKPRED_ADJUSTMENT_COVERAGE",
            (
                f"adjustment coverage {quality.coverage:.2%} is below "
                f"required {min_coverage:.2%}"
            ),
        )
    filtered_universe = universe[
        universe["ts_code"].astype(str).isin(complete_codes)
    ].reset_index(drop=True)
    filtered_prices = prices[
        prices["ts_code"].astype(str).isin(complete_codes)
    ].reset_index(drop=True)
    return filtered_universe, filtered_prices, quality


class GraphSignalService:
    """Load PIT inputs through the Gateway and evaluate one Graph cross-section."""

    def __init__(self, gateway: StockPredDataGateway) -> None:
        self.gateway = gateway

    def evaluate(
        self,
        eval_date: str,
        config: GraphSignalConfig = GraphSignalConfig(),
    ) -> pd.DataFrame:
        evaluation = _date(eval_date)
        trade_dates = self.gateway.trade_dates("19900101", evaluation)
        if evaluation not in trade_dates:
            raise StockPredDataError(
                "STOCKPRED_EVAL_DATE_CLOSED",
                f"evaluation date is not an open trading day: {evaluation}",
            )
        data_dates = trade_dates[-max(int(config.data_lookback_days), 1) :]
        data_start = data_dates[0]

        universe, stats = build_pit_universe(
            self.gateway.stock_dimension(),
            eval_date=evaluation,
            trade_dates=trade_dates,
            min_listed_trade_days=config.min_listed_trade_days,
            name_history=self.gateway.name_history(),
            industry_history=self.gateway.industry_history(),
            exclude_st=config.exclude_st,
        )
        if config.allowed_exchanges and "exchange" in universe.columns:
            universe = universe[
                universe["exchange"].isin(config.allowed_exchanges)
            ].copy()
        if config.require_pit_industry:
            universe = universe[
                universe["industry"].notna()
                & universe["industry"].astype(str).str.strip().ne("")
            ].copy()
        universe = universe.sort_values("ts_code", kind="stable").reset_index(drop=True)
        codes = universe["ts_code"].astype(str).tolist()

        prices = apply_qfq(
            self.gateway.prices(data_start, evaluation, codes),
            self.gateway.adjustment_factors(data_start, evaluation, codes),
        )
        universe, prices, quality = _filter_adjustment_complete(
            universe,
            prices,
            lookback_rows=config.data_lookback_days,
            min_coverage=config.min_adj_coverage,
        )
        codes = universe["ts_code"].astype(str).tolist()
        if not codes:
            return pd.DataFrame()

        daily_basic = self.gateway.daily_basic(evaluation, evaluation)
        daily_basic = daily_basic[daily_basic["ts_code"].astype(str).isin(codes)]
        moneyflow = self.gateway.moneyflow(
            _calendar_start(evaluation, 30),
            evaluation,
        )
        moneyflow = moneyflow[moneyflow["ts_code"].astype(str).isin(codes)]
        financials = self.gateway.financials_pit(
            _calendar_start(evaluation, 365),
            evaluation,
            eval_date=evaluation,
        )
        index_weights = self.gateway.index_weights(
            config.benchmark_code,
            evaluation,
            evaluation,
        )
        graph, _, _ = build_daily_graph(
            universe=universe,
            prices=prices,
            index_weights=index_weights,
            trade_date=evaluation,
            config=config.graph,
        )
        features = compute_all_graph_features(
            graph=graph,
            universe=universe,
            prices=prices,
            daily_basic=daily_basic,
            moneyflow=moneyflow,
            trade_date=evaluation,
            config=config.graph,
            fina_df=financials,
        )
        signals = generate_advisory(
            predict_batch_vectorized(features, cfg=config.prediction)
        )
        result = rank_signals(signals)
        result["eval_date"] = evaluation
        result["universe_size"] = len(universe)
        result["adjustment_coverage"] = quality.coverage
        for name, value in vars(stats).items():
            result[f"universe_{name}"] = value
        result.attrs["universe_stats"] = vars(stats)
        result.attrs["adjustment_quality"] = vars(quality)
        return result
