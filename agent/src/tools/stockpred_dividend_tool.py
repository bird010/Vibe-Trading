"""StockPred dividend tool: dividend history from Lance source_raw."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value


class StockpredDividendTool(StockPredBaseTool):
    name = "get_stockpred_dividend"
    description = (
        "Fetch A-share dividend history from StockPred local DB: "
        "cash/stock dividends, ex-dates, record dates. "
        'Example: {"code": "600519.SH"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A-share code, e.g. 600519.SH"},
        },
        "required": ["code"],
    }

    def execute(self, **kwargs: Any) -> str:
        code = kwargs["code"]
        df = self._query_lance("raw_dividend", filt=f"ts_code = '{_sanitize_filter_value(code)}'")
        if df.empty:
            return self._ok([], code=code)
        df = df.tail(50)
        return self._ok(_rows_to_safe_dicts(df), code=code)
