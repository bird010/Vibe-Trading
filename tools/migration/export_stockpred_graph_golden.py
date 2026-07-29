"""Export deterministic golden artifacts from the frozen StockPred oracle."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd


TRADE_COLUMNS = [
    "timestamp",
    "code",
    "side",
    "price",
    "status",
    "reason",
    "signal_date",
    "exit_delay_days",
]


def select_calendar_window(
    trade_dates: list[str],
    *,
    start: str,
    end: str,
    data_lookback_days: int,
) -> tuple[list[str], int]:
    """Return prior+target dates and the target lookback count."""
    normalized = sorted(dict.fromkeys(str(value).replace("-", "") for value in trade_dates))
    start_date = str(start).replace("-", "")
    end_date = str(end).replace("-", "")
    target = [value for value in normalized if start_date <= value <= end_date]
    if not target:
        raise ValueError("no open trading dates in requested range")
    first_position = normalized.index(target[0])
    window_start = max(0, first_position - max(int(data_lookback_days), 0))
    window = [value for value in normalized[window_start:] if value <= end_date]
    return window, len(target)


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _select_top(details: pd.DataFrame, top_n: int) -> pd.DataFrame:
    ordered = details.copy()
    if ordered.empty:
        return ordered
    required = {"trade_date", "score", "ts_code"}
    if not required.issubset(ordered.columns):
        missing = sorted(required - set(ordered.columns))
        raise ValueError(f"details missing selection columns: {missing}")
    eligible = ordered.groupby("trade_date")["ts_code"].transform("size") >= int(top_n)
    ordered = ordered.loc[eligible]
    ordered = ordered.sort_values(
        ["trade_date", "score", "ts_code"],
        ascending=[True, False, True],
        kind="stable",
    )
    return ordered.groupby("trade_date", sort=True).head(int(top_n)).reset_index(drop=True)


def eligible_eval_dates(details: pd.DataFrame, *, top_n: int) -> list[str]:
    """Return dates included in StockPred's Top-N return series."""
    selected = _select_top(details, top_n)
    if selected.empty:
        return []
    return sorted(str(value) for value in selected["trade_date"].unique())


def _iso_date(value: Any) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _optional_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def normalize_trades(details: pd.DataFrame, *, top_n: int) -> pd.DataFrame:
    """Convert selected StockPred entry/exit rows into event records."""
    if details.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    selected = _select_top(details, top_n)
    events: list[dict[str, Any]] = []
    for row in selected.to_dict(orient="records"):
        code = str(row["ts_code"])
        signal_date = _iso_date(row.get("trade_date"))
        entered = bool(row.get("entered", False))
        if not entered:
            events.append(
                {
                    "timestamp": _iso_date(row.get("entry_date") or row.get("trade_date")),
                    "code": code,
                    "side": "BUY",
                    "price": _optional_number(row.get("entry_price")),
                    "status": "REJECTED",
                    "reason": row.get("entry_block_reason"),
                    "signal_date": signal_date,
                    "exit_delay_days": 0,
                }
            )
            continue
        events.append(
            {
                "timestamp": _iso_date(row.get("entry_date")),
                "code": code,
                "side": "BUY",
                "price": _optional_number(row.get("entry_price")),
                "status": "FILLED",
                "reason": None,
                "signal_date": signal_date,
                "exit_delay_days": 0,
            }
        )
        if row.get("exit_date") is not None and not pd.isna(row.get("exit_date")):
            delay = int(row.get("exit_delay_days") or 0)
            events.append(
                {
                    "timestamp": _iso_date(row.get("exit_date")),
                    "code": code,
                    "side": "SELL",
                    "price": _optional_number(row.get("exit_price")),
                    "status": "FILLED",
                    "reason": "exit_delayed" if delay > 0 else None,
                    "signal_date": signal_date,
                    "exit_delay_days": delay,
                }
            )
    frame = pd.DataFrame(events, columns=TRADE_COLUMNS)
    frame["reason"] = pd.Series(
        [event["reason"] for event in events],
        dtype=object,
    )
    return frame


def normalize_equity(
    eval_dates: list[str],
    cumulative_returns: list[float],
) -> pd.DataFrame:
    """Convert StockPred cumulative returns into Vibe-style equity rows."""
    if len(eval_dates) != len(cumulative_returns):
        raise ValueError("eval_dates and cumulative_returns must have equal length")
    return pd.DataFrame(
        {
            "time": [_iso_date(value) for value in eval_dates],
            "equity": [1.0 + float(value) for value in cumulative_returns],
        }
    )


def extract_metrics(result: object) -> dict[str, object]:
    """Extract JSON-safe scalar summary fields from a result dataclass."""
    if not is_dataclass(result):
        raise TypeError("StockPred result must be a dataclass instance")
    metrics: dict[str, object] = {}
    for item in fields(result):
        value = getattr(result, item.name)
        if isinstance(value, bool | int | float | str) or value is None:
            metrics[item.name] = value
    return dict(sorted(metrics.items()))


def run_oracle(
    backtest_module: Any,
    *,
    trade_dates: list[str],
    start: str,
    end: str,
    top_n: int,
    eval_step: int,
    data_lookback_days: int = 180,
) -> Any:
    """Run StockPred with a calendar anchored to an explicit date range."""
    window, lookback_days = select_calendar_window(
        trade_dates,
        start=start,
        end=end,
        data_lookback_days=data_lookback_days,
    )
    original_provider = backtest_module.get_recent_trade_dates
    backtest_module.get_recent_trade_dates = lambda n_days: window[-int(n_days) :]
    try:
        config = backtest_module.BacktestConfig(
            lookback_days=lookback_days,
            data_lookback_days=data_lookback_days,
            forward_days=5,
            top_n=top_n,
            eval_step=eval_step,
            n_workers=1,
        )
        result = backtest_module.run_backtest(config)
    finally:
        backtest_module.get_recent_trade_dates = original_provider
    start_date = str(start).replace("-", "")
    end_date = str(end).replace("-", "")
    if any(not (start_date <= str(value) <= end_date) for value in result.eval_dates):
        raise RuntimeError("oracle returned evaluation dates outside requested range")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export frozen StockPred Graph golden artifacts")
    parser.add_argument("--stockpred-root", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--eval-step", type=int, default=5)
    return parser


def write_golden_bundle(
    out: Path,
    *,
    oracle_commit: str,
    config: dict[str, object],
    details: pd.DataFrame,
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    metrics: dict[str, object],
) -> None:
    """Write a complete immutable golden bundle."""
    out.mkdir(parents=True, exist_ok=False)
    ordered = details.copy()
    if {"trade_date", "score", "ts_code"}.issubset(ordered.columns):
        ordered = ordered.sort_values(
            ["trade_date", "score", "ts_code"],
            ascending=[True, False, True],
            kind="stable",
        ).reset_index(drop=True)
    selected = _select_top(ordered, int(config.get("top_n", 50))) if not ordered.empty else ordered.copy()

    ordered.to_parquet(out / "details.parquet", index=False)
    selected.to_csv(out / "selected.csv", index=False)
    trades.to_csv(out / "trades.csv", index=False)
    equity.to_csv(out / "equity.csv", index=False)
    (out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default)
        + "\n",
        encoding="utf-8",
    )
    (out / "manifest.json").write_text(
        json.dumps(
            {"oracle_commit": oracle_commit, "config": config},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_trade_dates(root: Path, end: str) -> list[str]:
    import lance

    dataset = lance.dataset(root / "data" / "lance" / "market_core" / "dim_trade_cal.lance")
    cutoff = str(end).replace("-", "")
    table = dataset.to_table(
        columns=["cal_date"],
        filter=f"exchange = 'SSE' AND is_open = 1 AND cal_date <= '{cutoff}'",
    )
    return sorted(str(value) for value in table.column("cal_date").to_pylist())


def _load_backtest_module(root: Path) -> Any:
    source = str((root / "src").resolve())
    if source not in sys.path:
        sys.path.insert(0, source)
    return importlib.import_module("stockpred_ai.graph.backtest")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.stockpred_root.expanduser().resolve()
    dirty = _git_output(root, "status", "--porcelain", "--", "src/stockpred_ai/graph")
    if dirty:
        raise RuntimeError("StockPred Graph oracle has uncommitted changes")
    oracle_commit = _git_output(root, "rev-parse", "HEAD")
    module = _load_backtest_module(root)
    result = run_oracle(
        module,
        trade_dates=_load_trade_dates(root, args.end),
        start=args.start,
        end=args.end,
        top_n=args.top_n,
        eval_step=args.eval_step,
    )
    config = {
        "start": args.start,
        "end": args.end,
        "top_n": args.top_n,
        "eval_step": args.eval_step,
        "forward_days": 5,
    }
    write_golden_bundle(
        args.out,
        oracle_commit=oracle_commit,
        config=config,
        details=result.details_df,
        trades=normalize_trades(result.details_df, top_n=args.top_n),
        equity=normalize_equity(
            eligible_eval_dates(result.details_df, top_n=args.top_n),
            result.cumulative_returns,
        ),
        metrics=extract_metrics(result),
    )
    print(json.dumps({"status": "ok", "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
