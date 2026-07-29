"""StockPred graph tool: graph features, correlations, industry momentum from Lance."""

from __future__ import annotations

from typing import Any

from src.tools.stockpred_base import StockPredBaseTool, _rows_to_safe_dicts, _sanitize_filter_value


class StockpredGraphTool(StockPredBaseTool):
    name = "get_stockpred_graph"
    description = (
        "Fetch StockPred knowledge-graph features: 42-dim factor scores, "
        "inter-stock correlations, industry momentum. "
        'data_type: features | correlation | industry_momentum. '
        'Example: {"code": "000001.SZ", "data_type": "features"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A-share code (for features/correlation)."},
            "data_type": {
                "type": "string",
                "enum": ["features", "correlation", "industry_momentum"],
                "description": "Which graph data to fetch.",
            },
            "trade_date": {
                "type": "string",
                "description": "Filter by trade_date (YYYYMMDD). Optional.",
            },
        },
        "required": ["data_type"],
    }

    _TABLE_MAP = {
        "features": "fact_graph_features",
        "correlation": "fact_correlation",
        "industry_momentum": "fact_industry_momentum",
    }

    def execute(self, **kwargs: Any) -> str:
        data_type = kwargs["data_type"]
        code = kwargs.get("code")
        trade_date = kwargs.get("trade_date")

        table = self._TABLE_MAP.get(data_type)
        if not table:
            return self._error(f"Unknown data_type: {data_type}")

        # Build filter
        filters = []
        if code:
            col = "ts_code" if data_type != "correlation" else "ts_code_a"
            filters.append(f"{col} = '{_sanitize_filter_value(code)}'")
        if trade_date:
            filters.append(f"trade_date = '{_sanitize_filter_value(trade_date)}'")

        filt = " AND ".join(filters) if filters else None
        df = self._query_lance(table, filt=filt)
        if df.empty:
            return self._ok([], data_type=data_type, code=code)
        df = df.tail(100)
        return self._ok(_rows_to_safe_dicts(df), data_type=data_type, code=code)
