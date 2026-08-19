"""StockPred macro tool: macro series + bond yield curves from Lance."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value


class StockpredMacroTool(StockPredBaseTool):
    name = "get_stockpred_macro"
    description = (
        "Fetch China macro time series from StockPred local DB: "
        "Shibor, LPR, GDP, CPI, PPI, M2, PMI, bond yield curves. "
        'Example: {"series_id": "rate.shibor_on"} or {"curve_type": "treasury"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "series_id": {
                "type": "string",
                "description": "Macro series ID, e.g. rate.shibor_on, macro.gdp_yoy, price.cpi_yoy.",
            },
            "curve_type": {
                "type": "string",
                "description": "Bond yield curve type: treasury | commercial_bank | medium_short_note.",
            },
        },
    }

    def execute(self, **kwargs: Any) -> str:
        series_id = kwargs.get("series_id")
        curve_type = kwargs.get("curve_type")

        if series_id:
            df = self._query_lance("fact_macro_series", filt=f"series_id = '{_sanitize_filter_value(series_id)}'")
            if df.empty:
                return self._ok([], series_id=series_id)
            df = df.tail(100)
            return self._ok(_rows_to_safe_dicts(df), series_id=series_id)

        if curve_type:
            df = self._query_lance("fact_bond_yield_curve", filt=f"curve_type = '{_sanitize_filter_value(curve_type)}'")
            if df.empty:
                return self._ok([], curve_type=curve_type)
            return self._ok(_rows_to_safe_dicts(df), curve_type=curve_type)

        return self._error("Provide series_id or curve_type.")
