from __future__ import annotations

import pandas as pd

from src.stockpred.graph.service import GraphSignalConfig, GraphSignalService


class _Gateway:
    def __init__(self) -> None:
        self.dates = pd.bdate_range("2025-09-01", periods=72).strftime("%Y%m%d").tolist()
        self.codes = [f"{600000 + index:06d}.SH" for index in range(8)]
        rows: list[dict[str, object]] = []
        factors: list[dict[str, object]] = []
        for code_index, code in enumerate(self.codes):
            for date_index, trade_date in enumerate(self.dates):
                close = 10.0 + code_index + date_index * (0.01 + code_index * 0.001)
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": trade_date,
                        "open": close - 0.02,
                        "high": close + 0.05,
                        "low": close - 0.05,
                        "close": close,
                        "pct_chg": 0.1,
                        "vol": 1000.0 + date_index,
                        "amount": close * 1000.0,
                    }
                )
                factors.append(
                    {"ts_code": code, "trade_date": trade_date, "adj_factor": 1.0}
                )
        self.price_rows = pd.DataFrame(rows)
        self.factor_rows = pd.DataFrame(factors)
        self.requested_ends: list[str] = []

    def trade_dates(self, start: str, end: str) -> list[str]:
        self.requested_ends.append(end)
        return [date for date in self.dates if start <= date <= end]

    def stock_dimension(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "name": self.codes,
                "industry": ["A", "B"] * 4,
                "list_date": ["20200101"] * 8,
                "delist_date": [""] * 8,
                "list_status": ["L"] * 8,
                "exchange": ["SSE"] * 8,
                "market": ["主板"] * 8,
            }
        )

    def name_history(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "security_name": self.codes,
                "effective_from": ["20200101"] * 8,
                "effective_to": [""] * 8,
                "ann_date": ["20200101"] * 8,
                "change_reason": ["上市"] * 8,
            }
        )

    def industry_history(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "industry_code": ["A", "B"] * 4,
                "industry_name": ["A", "B"] * 4,
                "level": ["L1"] * 8,
                "effective_from": ["20200101"] * 8,
                "effective_to": [""] * 8,
                "source": ["test"] * 8,
            }
        )

    def _slice(self, frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        self.requested_ends.append(end)
        return frame[frame["trade_date"].between(start, end)].copy()

    def prices(self, start: str, end: str, codes=None) -> pd.DataFrame:
        result = self._slice(self.price_rows, start, end)
        return result[result["ts_code"].isin(codes)].copy() if codes is not None else result

    def adjustment_factors(self, start: str, end: str, codes=None) -> pd.DataFrame:
        result = self._slice(self.factor_rows, start, end)
        return result[result["ts_code"].isin(codes)].copy() if codes is not None else result

    def daily_basic(self, start: str, end: str) -> pd.DataFrame:
        self.requested_ends.append(end)
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "trade_date": [end] * 8,
                "turnover_rate": [1.0] * 8,
                "pe_ttm": [20.0] * 8,
                "pb": [1.0] * 8,
                "total_mv": [1e9] * 8,
            }
        )

    def moneyflow(self, start: str, end: str) -> pd.DataFrame:
        self.requested_ends.append(end)
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "trade_date": [end] * 8,
                "buy_elg_amount": [100.0] * 8,
                "sell_elg_amount": [80.0] * 8,
                "net_mf_amount": [20.0] * 8,
            }
        )

    def index_weights(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        self.requested_ends.append(end)
        return pd.DataFrame(
            {
                "index_code": [index_code],
                "con_code": [self.codes[0]],
                "trade_date": [end],
                "weight": [0.5],
            }
        )

    def financials_pit(self, start: str, end: str, *, eval_date: str) -> pd.DataFrame:
        self.requested_ends.extend([end, eval_date])
        return pd.DataFrame(
            columns=["ts_code", "ann_date", "end_date", "eps", "dt_eps", "roe", "grossprofit_margin"]
        )

    def append_future(self, future_date: str) -> None:
        future_prices = self.price_rows[self.price_rows["trade_date"] == self.dates[-1]].copy()
        future_prices["trade_date"] = future_date
        future_prices["close"] *= 100.0
        future_factors = self.factor_rows[self.factor_rows["trade_date"] == self.dates[-1]].copy()
        future_factors["trade_date"] = future_date
        self.price_rows = pd.concat([self.price_rows, future_prices], ignore_index=True)
        self.factor_rows = pd.concat([self.factor_rows, future_factors], ignore_index=True)


def test_signal_service_does_not_change_when_future_rows_are_appended() -> None:
    gateway = _Gateway()
    eval_date = gateway.dates[-2]
    config = GraphSignalConfig(data_lookback_days=70, min_listed_trade_days=1)
    service = GraphSignalService(gateway)

    before = service.evaluate(eval_date, config)
    gateway.append_future("20260106")
    after = service.evaluate(eval_date, config)

    pd.testing.assert_frame_equal(before, after)
    assert gateway.requested_ends
    assert all(end <= eval_date for end in gateway.requested_ends)
    expected_order = before.sort_values(
        ["score", "ts_code"],
        ascending=[False, True],
        kind="stable",
    )["ts_code"].tolist()
    assert before["ts_code"].tolist() == expected_order
