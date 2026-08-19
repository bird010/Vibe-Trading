"""UI-oriented services for run analysis and local process management.

This module centralizes the data shaping needed by the frontend workbench:

- parse run metadata into frontend-friendly context
- reconstruct market data for historical runs when artifacts are incomplete
- compute indicator overlays and trade markers
- read runner logs for the detail page
- (LocalApiManager removed – dead code)
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from backtest.stockpred_graph.performance import build_symbol_metrics

DEFAULT_ANALYSIS_PERIODS = [5, 20]
_SAFE_SYMBOL_SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def format_run_date(date_str: Optional[str]) -> Optional[str]:
    """Normalize supported date strings into ``YYYY-MM-DD``.

    Args:
        date_str: Raw date string from request or planner artifacts.

    Returns:
        The normalized date string, or ``None`` when the input is empty.
    """
    if not date_str:
        return None

    value = str(date_str).strip()
    if not value:
        return None
    if "-" in value and len(value) == 10:
        return value
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    # Handle datetime strings like "2022-04-13 16:00:00" → extract date part
    if "-" in value and len(value) > 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value


def load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Load a JSON file if it exists.

    Args:
        path: JSON file path.

    Returns:
        The decoded object when the file exists and is valid JSON.
    """
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def load_csv_records(path: Path) -> List[Dict[str, Any]]:
    """Load CSV rows as dictionaries.

    Args:
        path: CSV file path.

    Returns:
        Parsed CSV rows. Missing or unreadable files return an empty list.
    """
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def normalize_codes(raw_codes: Any) -> List[str]:
    """Normalize a run's code selection into a list of symbols.

    Args:
        raw_codes: Input from ``req.json``.

    Returns:
        A list of non-empty symbol strings.
    """
    if isinstance(raw_codes, list):
        return [str(code).strip() for code in raw_codes if str(code).strip()]
    if isinstance(raw_codes, str):
        return [code.strip() for code in raw_codes.split(",") if code.strip()]
    return []


def load_run_context(run_dir: Path) -> Dict[str, Any]:
    """Load normalized request context for a run detail page.

    Falls back to planner_output.json when req.json context is empty
    (common in session mode where the LLM extracts codes/dates).

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        A dictionary containing prompt, codes, dates, and the raw context.
    """
    request_data = load_json_file(run_dir / "req.json") or {}
    context = dict(request_data.get("context") or {})
    metric_schema_version = context.get("metric_schema_version")
    if (
        not metric_schema_version
        and context.get("strategy_type") == "stockpred_strategy"
        and (run_dir / "artifacts").is_dir()
    ):
        metric_schema_version = "legacy_portfolio_like_v1"
    prompt = str(request_data.get("prompt") or "").strip()
    codes = normalize_codes(context.get("codes"))
    start_date = format_run_date(context.get("start_date"))
    end_date = format_run_date(context.get("end_date"))

    # Fallback: extract from planner_output.json (session mode stores codes there)
    if not codes or not start_date or not end_date:
        planner = load_json_file(run_dir / "planner_output.json") or {}
        contract = planner.get("coding_contract") or {}
        req_ctx = (planner.get("requirements") or {}).get("context") or {}

        if not codes:
            raw = contract.get("target_scope") or req_ctx.get("codes") or contract.get("codes")
            codes = normalize_codes(raw) or codes
            if not codes:
                for req in contract.get("data_requirements") or []:
                    scope = req.get("symbol_scope", "")
                    if isinstance(scope, str) and scope.strip():
                        codes.extend(c.strip() for c in scope.split(",") if c.strip())
                codes = list(dict.fromkeys(codes))

        if not start_date:
            start_date = format_run_date(contract.get("start_date") or req_ctx.get("start_date"))
        if not end_date:
            end_date = format_run_date(contract.get("end_date") or req_ctx.get("end_date"))

    return {
        "prompt": prompt,
        "codes": codes,
        "start_date": start_date,
        "end_date": end_date,
        "strategy_type": context.get("strategy_type"),
        "metric_schema_version": metric_schema_version,
        "raw_context": context,
    }


def infer_indicator_periods(run_dir: Path) -> List[int]:
    """Infer moving-average periods from planner or design artifacts.

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        Sorted indicator periods. Defaults to ``[5, 20]`` when unspecified.
    """
    periods: set[int] = set()

    planner = load_json_file(run_dir / "planner_output.json") or {}
    contract = planner.get("coding_contract") or {}
    input_logic = contract.get("input_logic") or {}
    parameters = input_logic.get("parameters") or {}
    signal_params = parameters.get("signal_params") or {}

    for key, value in signal_params.items():
        if "ma" in str(key).lower():
            try:
                periods.add(int(value))
            except (TypeError, ValueError):
                continue

    design = load_json_file(run_dir / "design_spec.json") or {}
    defaults = design.get("defaults_and_tunables") or {}
    assumptions = defaults.get("parameter_assumptions") or {}
    for key, value in assumptions.items():
        if "ma" in str(key).lower():
            try:
                periods.add(int(value))
            except (TypeError, ValueError):
                continue

    if not periods:
        return list(DEFAULT_ANALYSIS_PERIODS)
    return sorted(periods)


def infer_run_stage(run_dir: Path) -> str:
    """Infer a human-friendly run stage from persisted artifacts.

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        A stage token suitable for UI badges.
    """
    state_data = load_json_file(run_dir / "state.json") or {}
    state_status = str(state_data.get("status") or "").lower()
    if state_status == "success":
        return "done"
    if state_status == "failed":
        return "failed"
    if (run_dir / "artifacts" / "metrics.csv").exists():
        return "backtest"
    if (run_dir / "review_report.json").exists():
        return "review"
    if (run_dir / "code" / "signal_engine.py").exists():
        return "coding"
    if (run_dir / "design_spec.json").exists():
        return "design"
    if (run_dir / "planner_output.json").exists():
        return "planning"
    if (run_dir / "req.json").exists():
        return "queued"
    return "unknown"


def collect_run_logs(run_dir: Path, line_limit: int = 200) -> List[Dict[str, Any]]:
    """Read stdout/stderr files into a flat log entry list.

    Args:
        run_dir: The run directory under ``runs/``.
        line_limit: Maximum number of lines to keep per file.

    Returns:
        Tagged log entries ordered by file, then line number.
    """
    entries: List[Dict[str, Any]] = []
    log_files = [
        ("stdout", run_dir / "logs" / "runner_stdout.txt"),
        ("stderr", run_dir / "logs" / "runner_stderr.txt"),
        ("compile", run_dir / "logs" / "compile_error.txt"),
    ]

    for source, path in log_files:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line_limit > 0:
            lines = lines[-line_limit:]
        for index, line in enumerate(lines, start=1):
            entries.append(
                {
                    "source": source,
                    "line_number": index,
                    "message": line,
                }
            )

    return entries


def build_trade_markers(
    trades: List[Dict[str, Any]],
    symbols: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Normalize trade rows into frontend marker objects.

    Args:
        trades: Trade rows loaded from ``trades.csv``.

    Returns:
        Marker dictionaries consumed by the detail chart.
    """
    markers: List[Dict[str, Any]] = []
    for row in trades:
        code = str(row.get("code") or "")
        if symbols and code not in symbols:
            continue
        side = str(row.get("side") or "").upper()
        timestamp = str(row.get("timestamp") or row.get("time") or "")
        exit_delay_days = row.get("exit_delay_days")
        markers.append(
            {
                "time": timestamp[:10],
                "timestamp": timestamp,
                "code": row.get("code"),
                "side": side,
                "price": _safe_float(row.get("price")),
                "qty": _safe_float(row.get("qty")),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "exit_delay_days": (
                    None
                    if exit_delay_days in (None, "")
                    else int(float(exit_delay_days))
                ),
                "text": f"{side} {row.get('code') or ''}".strip(),
            }
        )
    return markers


def load_graph_signal_series(
    run_dir: Path,
    symbols: Sequence[str] | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load selected StockPred Graph signals grouped by symbol."""
    context = load_run_context(run_dir)
    if context.get("strategy_type") != "stockpred_graph":
        return {}

    rows = load_csv_records(run_dir / "artifacts" / "selected_signals.csv")
    allowed = set(symbols or [])
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("ts_code") or row.get("code") or "")
        if not code or (allowed and code not in allowed):
            continue
        grouped.setdefault(code, []).append(
            {
                **row,
                "time": format_run_date(row.get("eval_date") or row.get("trade_date")) or "",
                "code": code,
                "score": _safe_float(row.get("score")),
                "rank": int(float(row.get("rank") or 0)),
                "risk_adjustment": (
                    None
                    if row.get("risk_adjustment") in (None, "")
                    else _safe_float(row.get("risk_adjustment"))
                ),
            }
        )
    return grouped


def _numeric_metric_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only non-empty symbols and finite numeric metric values."""
    metrics: List[Dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        metric_row: Dict[str, Any] = {"symbol": symbol}
        for key, value in row.items():
            if key == "symbol" or value in (None, ""):
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric_value):
                metric_row[key] = numeric_value
        metrics.append(metric_row)
    return metrics


def _load_graph_ohlcv_frames(run_dir: Path) -> Dict[str, pd.DataFrame]:
    """Load safe legacy Graph OHLCV artifacts without modifying the run."""
    artifacts = run_dir / "artifacts"
    if not artifacts.is_dir():
        return {}

    frames: Dict[str, pd.DataFrame] = {}
    for path in sorted(artifacts.glob("ohlcv_*.csv")):
        symbol = path.stem.removeprefix("ohlcv_")
        if not _SAFE_SYMBOL_SUFFIX_RE.fullmatch(symbol):
            continue
        rows = load_csv_records(path)
        if rows:
            frames[symbol] = pd.DataFrame(rows)
    return frames


def load_symbol_metrics(run_dir: Path) -> List[Dict[str, Any]]:
    """Load Graph per-symbol metrics, rebuilding legacy artifacts in memory."""
    if load_run_context(run_dir).get("strategy_type") not in {"stockpred_graph", "stockpred_strategy"}:
        return []

    persisted_path = run_dir / "artifacts" / "symbol_metrics.csv"
    persisted = load_csv_records(persisted_path)
    if persisted_path.exists():
        return _numeric_metric_rows(persisted)

    trades = pd.DataFrame(load_csv_records(run_dir / "artifacts" / "trades.csv"))
    prices = _load_graph_ohlcv_frames(run_dir)
    if trades.empty or "code" not in trades or not prices:
        return []
    try:
        return _numeric_metric_rows(build_symbol_metrics(trades, prices))
    except (KeyError, TypeError, ValueError):
        return []

def group_price_rows(price_rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group normalized price rows by symbol.

    Args:
        price_rows: Flat list of normalized price rows.

    Returns:
        A dictionary keyed by symbol.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in price_rows:
        code = str(row.get("code") or "UNKNOWN")
        grouped.setdefault(code, []).append(row)
    return grouped


def build_indicator_series(
    price_rows: List[Dict[str, Any]],
    periods: Optional[List[int]] = None,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Compute moving-average overlays from normalized price rows.

    Args:
        price_rows: Flat list of normalized OHLC rows.
        periods: Moving-average windows to compute.

    Returns:
        Nested dictionaries grouped by symbol and indicator label.
    """
    grouped = group_price_rows(price_rows)
    indicator_periods = sorted(set(periods or DEFAULT_ANALYSIS_PERIODS))
    output: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for code, rows in grouped.items():
        ordered_rows = sorted(
            (
                {
                    **row,
                    "time": str(row.get("time") or format_run_date(row.get("timestamp")) or ""),
                }
                for row in rows
            ),
            key=lambda item: item["time"],
        )
        closes = [float(row["close"]) for row in ordered_rows]
        code_series: Dict[str, List[Dict[str, Any]]] = {}
        for period in indicator_periods:
            label = f"ma{period}"
            values: List[Dict[str, Any]] = []
            for index, row in enumerate(ordered_rows):
                if index + 1 < period:
                    current = None
                else:
                    window = closes[index - period + 1 : index + 1]
                    current = round(sum(window) / period, 6)
                values.append({"time": row["time"], "value": current})
            code_series[label] = values
        output[code] = code_series

    return output


def _load_ohlcv_artifacts(run_dir: Path) -> List[Dict[str, Any]]:
    """Read individual ohlcv_*.csv files from the artifacts directory.

    The engine writes ``ohlcv_{code}.csv`` per symbol with columns
    ``trade_date,open,high,low,close,volume``. This function reads
    them all and returns a flat list of normalized rows.

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        Normalized OHLC rows, empty list if no files found.
    """
    artifacts = run_dir / "artifacts"
    if not artifacts.is_dir():
        return []
    ohlcv_files = sorted(artifacts.glob("ohlcv_*.csv"))
    if not ohlcv_files:
        return []
    rows: List[Dict[str, Any]] = []
    for f in ohlcv_files:
        code = f.stem.removeprefix("ohlcv_")
        for r in load_csv_records(f):
            ts = r.get("trade_date") or r.get("timestamp") or r.get("time") or r.get("")
            if not ts:
                continue
            rows.append({
                "time": ts, "timestamp": ts, "code": code,
                "open": r.get("open", 0), "high": r.get("high", 0),
                "low": r.get("low", 0), "close": r.get("close", 0),
                "volume": r.get("volume", 0),
            })
    return _normalize_price_rows(rows)


def _detail_publish_complete(run_dir: Path) -> bool:
    """Delegate to the shared strategy_detail implementation."""
    from src.stockpred.strategy_detail import detail_publish_complete
    return detail_publish_complete(run_dir)


def _try_materialize_detail(run_dir: Path) -> bool:
    """Attempt lazy OHLCV materialization for a stockpred strategy run.

    Writes ohlcv_*.csv into ``artifacts/`` using the pinned data snapshot.
    Returns True if OHLCV files are available in artifacts/ after the call.
    """
    artifacts = run_dir / "artifacts"
    if artifacts.is_dir() and any(artifacts.glob("ohlcv_*.csv")):
        # Only accept existing CSVs if the publish is complete
        if _detail_publish_complete(run_dir):
            return True
    manifest_path = run_dir / "detail_manifest.json"
    snapshot_path = run_dir / "data_snapshot.json"
    if not manifest_path.is_file() or not snapshot_path.is_file():
        return False
    try:
        from src.stockpred.contracts import DataSnapshotManifest
        from src.stockpred.gateway import StockPredDataGateway
        from src.stockpred.snapshot import resolve_stockpred_root
        from src.stockpred.strategy_detail import materialize_strategy_detail

        snapshot = DataSnapshotManifest.model_validate(
            json.loads(snapshot_path.read_text(encoding="utf-8"))
        )
        root = resolve_stockpred_root()
        gateway = StockPredDataGateway(root, snapshot)
        materialize_strategy_detail(run_dir, gateway)
        return True
    except Exception:
        return False


def load_price_series(run_dir: Path) -> List[Dict[str, Any]]:
    """Load chart-ready price rows: price_series.csv > ohlcv_*.csv > lazy materialization > API reconstruct.

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        Normalized OHLC rows sorted by (code, date).
    """
    artifact_path = run_dir / "artifacts" / "price_series.csv"
    if artifact_path.exists():
        return _normalize_price_rows(load_csv_records(artifact_path))
    # Only serve existing ohlcv_*.csv if the publish is complete
    if _detail_publish_complete(run_dir):
        rows = _load_ohlcv_artifacts(run_dir)
        if rows:
            return rows
    # Lazy materialization: write ohlcv_*.csv into artifacts/ from the
    # pinned data snapshot for stockpred strategy runs.
    if _try_materialize_detail(run_dir):
        rows = _load_ohlcv_artifacts(run_dir)
        if rows:
            return rows
    return reconstruct_price_series(run_dir)


def load_chart_symbols(run_dir: Path, context: Optional[Dict[str, Any]] = None) -> List[str]:
    """Load chart symbol names without materializing all chart rows."""
    artifacts = run_dir / "artifacts"

    # For staged runs with incomplete publish, use manifest codes (not partial CSV scan)
    manifest_path = run_dir / "detail_manifest.json"
    if manifest_path.is_file() and not _detail_publish_complete(run_dir):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            codes = [str(c) for c in manifest.get("codes", []) if c]
            if codes:
                return sorted(codes)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        # Fallback to context codes
        raw_codes = (context or load_run_context(run_dir)).get("codes") or []
        return sorted(str(code) for code in raw_codes if code)

    symbols: set[str] = set()

    price_path = artifacts / "price_series.csv"
    if price_path.exists():
        try:
            with price_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    code = str(row.get("code") or "").strip()
                    if code:
                        symbols.add(code)
        except OSError:
            symbols.clear()

    if not symbols and artifacts.is_dir():
        symbols.update(
            file_path.stem.removeprefix("ohlcv_")
            for file_path in artifacts.glob("ohlcv_*.csv")
            if file_path.stem.removeprefix("ohlcv_")
        )

    if not symbols:
        raw_codes = (context or load_run_context(run_dir)).get("codes") or []
        symbols.update(str(code) for code in raw_codes if code)

    return sorted(symbols)


def reconstruct_price_series(run_dir: Path) -> List[Dict[str, Any]]:
    """Rebuild OHLC rows for a historical run using its generated loader.

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        Normalized OHLC rows clipped to the run's visible backtest range.
    """
    context = load_run_context(run_dir)
    codes = context.get("codes") or []
    start_date = context.get("start_date")
    end_date = context.get("end_date")
    if not codes or not start_date or not end_date:
        return []

    signal_path = run_dir / "code" / "signal_engine.py"
    if not signal_path.exists():
        return []

    agent_root = Path(__file__).resolve().parents[1]
    try:
        from src.providers.llm import _ensure_dotenv

        _ensure_dotenv()
    except Exception:
        pass
    fetch_start_date = _compute_fetch_start_date(run_dir, start_date)

    try:
        source = context.get("source", "tushare")
        if source == "okx":
            from backtest.loaders.okx import DataLoader
        elif source == "yfinance":
            from backtest.loaders.yfinance_loader import DataLoader
        else:
            from backtest.loaders.tushare import DataLoader
        loader = DataLoader()
        data_map = loader.fetch(codes, fetch_start_date, end_date)
    except Exception as exc:
        print(f"[WARN] reconstruct_price_series: DataLoader failed ({exc})")
        return []

    if not data_map:
        print(f"[WARN] reconstruct_price_series: DataLoader returned empty")
        return []

    return _flatten_data_map(data_map, start_date=start_date)


def build_run_analysis(
    run_dir: Path,
    symbols: Optional[List[str]] = None,
    *,
    include_payload: bool = True,
    include_symbol_list: bool = False,
) -> Dict[str, Any]:
    """Build the analysis payload consumed by the run detail page.

    Args:
        run_dir: The run directory under ``runs/``.

    Returns:
        A serializable dictionary of chart, trade, and log data.
    """
    context = load_run_context(run_dir)
    chart_symbols = load_chart_symbols(run_dir, context) if include_symbol_list or not include_payload else []

    if not include_payload:
        return {
            "run_stage": infer_run_stage(run_dir),
            "run_context": context,
            "chart_symbols": chart_symbols,
            "price_series": {},
            "indicator_series": {},
            "trade_markers": [],
            "graph_signal_series": {},
            "strategy_snapshot": load_json_file(run_dir / "strategy_snapshot.json"),
            "run_logs": collect_run_logs(run_dir),
        }

    price_rows = load_price_series(run_dir)
    if include_symbol_list and not chart_symbols:
        chart_symbols = sorted({str(row.get("code") or "") for row in price_rows if row.get("code")})
    selected_symbols = {symbol for symbol in (symbols or []) if symbol}
    if selected_symbols:
        price_rows = [row for row in price_rows if str(row.get("code") or "") in selected_symbols]
    periods = infer_indicator_periods(run_dir)
    trades = load_csv_records(run_dir / "artifacts" / "trades.csv")

    return {
        "run_stage": infer_run_stage(run_dir),
        "run_context": context,
        "chart_symbols": chart_symbols,
        "price_series": group_price_rows(price_rows),
        "indicator_series": build_indicator_series(price_rows, periods) if price_rows else {},
        "trade_markers": build_trade_markers(trades, selected_symbols or None),
        "graph_signal_series": load_graph_signal_series(
            run_dir,
            sorted(selected_symbols) if selected_symbols else None,
        ),
        "strategy_snapshot": load_json_file(run_dir / "strategy_snapshot.json"),
        "run_logs": collect_run_logs(run_dir),
    }


def _safe_float(value: Any) -> Optional[float]:
    """Convert values to floats without raising.

    Args:
        value: Value to convert.

    Returns:
        A float or ``None`` when conversion fails.
    """
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_fetch_start_date(run_dir: Path, start_date: str) -> str:
    """Compute the lookback-aware fetch start date for a run.

    Args:
        run_dir: The run directory under ``runs/``.
        start_date: Visible backtest start date.

    Returns:
        The fetch start date used to rebuild market data.
    """
    planner = load_json_file(run_dir / "planner_output.json") or {}
    lookback = int(((planner.get("coding_contract") or {}).get("data_lookback_days")) or 0)
    if lookback <= 0:
        return start_date

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    buffer_days = int(lookback * 1.5) + 10
    return (start_dt - timedelta(days=buffer_days)).strftime("%Y-%m-%d")


def _normalize_price_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize stored price rows for charting.

    Args:
        rows: Raw rows loaded from CSV or generated data frames.

    Returns:
        Normalized price rows.
    """
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        timestamp = format_run_date(row.get("timestamp") or row.get("time"))
        if not timestamp:
            continue
        normalized.append(
            {
                "time": timestamp,
                "timestamp": timestamp,
                "code": str(row.get("code") or "UNKNOWN"),
                "open": float(row.get("open") or 0.0),
                "high": float(row.get("high") or 0.0),
                "low": float(row.get("low") or 0.0),
                "close": float(row.get("close") or 0.0),
                "volume": float(row.get("volume") or 0.0),
            }
        )
    return sorted(normalized, key=lambda item: (item["code"], item["time"]))


def _flatten_data_map(data_map: Dict[str, Any], start_date: str) -> List[Dict[str, Any]]:
    """Convert a fetched ``data_map`` into normalized price rows.

    Args:
        data_map: Symbol-keyed frame dictionary returned by ``DataLoader``.
        start_date: Visible backtest start date.

    Returns:
        Normalized price rows clipped to ``start_date``.
    """
    import pandas as pd

    clip_dt = pd.Timestamp(start_date)
    rows: List[Dict[str, Any]] = []

    for code, frame in data_map.items():
        current = frame.copy()
        if not isinstance(current.index, pd.DatetimeIndex):
            current.index = pd.to_datetime(current.index)
        current = current[current.index >= clip_dt]
        current = current.sort_index()
        for timestamp, row in current.iterrows():
            rows.append(
                {
                    "time": pd.Timestamp(timestamp).strftime("%Y-%m-%d"),
                    "timestamp": pd.Timestamp(timestamp).strftime("%Y-%m-%d"),
                    "code": code,
                    "open": round(float(row.get("open") or 0.0), 6),
                    "high": round(float(row.get("high") or 0.0), 6),
                    "low": round(float(row.get("low") or 0.0), 6),
                    "close": round(float(row.get("close") or 0.0), 6),
                    "volume": round(float(row.get("volume") or 0.0), 6),
                }
            )

    return rows
