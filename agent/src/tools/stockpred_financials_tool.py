"""StockPred financials tool: income/balance/cashflow/fina_indicator from Lance."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value

# Table mapping: data_type → Lance table name
_TABLE_MAP = {
    "income": "raw_income",
    "balance": "raw_balancesheet",
    "cashflow": "raw_cashflow",
    "indicators": "fact_fina_indicator",
}


class StockpredFinancialsTool(StockPredBaseTool):
    name = "get_stockpred_financials"
    description = (
        "Fetch A-share financial statements and indicators from StockPred local DB. "
        "data_type: income | balance | cashflow | indicators. "
        'Example: {"code": "000001.SZ", "data_type": "indicators"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A-share code, e.g. 000001.SZ"},
            "data_type": {
                "type": "string",
                "enum": ["income", "balance", "cashflow", "indicators"],
                "description": "Which financial data to fetch.",
            },
        },
        "required": ["code", "data_type"],
    }

    def execute(self, **kwargs: Any) -> str:
        code = kwargs["code"]
        data_type = kwargs.get("data_type", "indicators")
        table = _TABLE_MAP.get(data_type)
        if not table:
            return self._error(f"Unknown data_type: {data_type}")

        df = self._query_lance(table, filt=f"ts_code = '{_sanitize_filter_value(code)}'")
        if df.empty:
            return self._ok([], table=table, code=code)

        # Cap to most recent 50 rows
        df = df.tail(50)
        return self._ok(_rows_to_safe_dicts(df), table=table, code=code)
