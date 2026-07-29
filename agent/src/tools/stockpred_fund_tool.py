"""StockPred fund tool: fund daily, NAV, portfolio, size, manager from Lance."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value

_TABLE_MAP = {
    "daily": "fund",
    "nav": "fact_fund_nav",
    "portfolio": "fact_fund_portfolio",
    "size": "fact_fund_size",
}


class StockpredFundTool(StockPredBaseTool):
    name = "get_stockpred_fund"
    description = (
        "Fetch fund data from StockPred local DB: "
        "daily OHLCV, NAV, portfolio holdings, fund size. "
        'data_type: daily | nav | portfolio | size. '
        'Example: {"code": "510300.SH", "data_type": "daily"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Fund code, e.g. 510300.SH"},
            "data_type": {
                "type": "string",
                "enum": list(_TABLE_MAP.keys()),
                "description": "Which fund data to fetch.",
            },
        },
        "required": ["code", "data_type"],
    }

    def execute(self, **kwargs: Any) -> str:
        code = kwargs["code"]
        data_type = kwargs.get("data_type", "daily")
        table = _TABLE_MAP.get(data_type)
        if not table:
            return self._error(f"Unknown data_type: {data_type}")

        df = self._query_lance(table, filt=f"ts_code = '{_sanitize_filter_value(code)}'")
        if df.empty:
            return self._ok([], code=code, data_type=data_type)
        df = df.tail(100)
        return self._ok(_rows_to_safe_dicts(df), code=code, data_type=data_type)
