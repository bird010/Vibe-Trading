"""StockPred holders tool: top10 holders, pledge, holder trade, repurchase from Lance."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value

_TABLE_MAP = {
    "top10": "raw_top10_holders",
    "top10_float": "raw_top10_floatholders",
    "pledge_stat": "raw_pledge_stat",
    "pledge_detail": "raw_pledge_detail",
    "holder_trade": "raw_holdertrade",
    "repurchase": "raw_repurchase",
    "holder_number": "fact_holdernumber",
}


class StockpredHoldersTool(StockPredBaseTool):
    name = "get_stockpred_holders"
    description = (
        "Fetch A-share shareholder data from StockPred local DB: "
        "top10 holders, pledge, holder trades, repurchase, holder count. "
        'data_type: top10 | top10_float | pledge_stat | pledge_detail | holder_trade | repurchase | holder_number. '
        'Example: {"code": "000001.SZ", "data_type": "top10"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A-share code, e.g. 000001.SZ"},
            "data_type": {
                "type": "string",
                "enum": list(_TABLE_MAP.keys()),
                "description": "Which holder data to fetch.",
            },
        },
        "required": ["code", "data_type"],
    }

    def execute(self, **kwargs: Any) -> str:
        code = kwargs["code"]
        data_type = kwargs.get("data_type", "top10")
        table = _TABLE_MAP.get(data_type)
        if not table:
            return self._error(f"Unknown data_type: {data_type}")

        df = self._query_lance(table, filt=f"ts_code = '{_sanitize_filter_value(code)}'")
        if df.empty:
            return self._ok([], code=code, data_type=data_type)
        df = df.tail(50)
        return self._ok(_rows_to_safe_dicts(df), code=code, data_type=data_type)
