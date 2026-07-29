"""StockPred index tool: index daily + weights from Lance."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value


class StockpredIndexTool(StockPredBaseTool):
    name = "get_stockpred_index"
    description = (
        "Fetch index daily data or constituent weights from StockPred local DB. "
        'data_type: daily | weights. '
        'Example: {"code": "000300.SH", "data_type": "daily"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Index code, e.g. 000300.SH"},
            "data_type": {
                "type": "string",
                "enum": ["daily", "weights"],
                "description": "daily bars or constituent weights.",
            },
        },
        "required": ["code", "data_type"],
    }

    def execute(self, **kwargs: Any) -> str:
        code = kwargs["code"]
        data_type = kwargs["data_type"]

        if data_type == "daily":
            table = "fact_index_daily"
            col = "ts_code"
        elif data_type == "weights":
            table = "fact_index_weight"
            col = "index_code"
        else:
            return self._error(f"Unknown data_type: {data_type}")

        df = self._query_lance(table, filt=f"{col} = '{_sanitize_filter_value(code)}'")
        if df.empty:
            return self._ok([], code=code, data_type=data_type)
        df = df.tail(100)
        return self._ok(_rows_to_safe_dicts(df), code=code, data_type=data_type)
