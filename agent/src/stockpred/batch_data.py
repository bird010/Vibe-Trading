"""Snapshot-scoped data reuse for batch Alpha strategy evaluation."""

from __future__ import annotations

import threading
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.stockpred.strategies.contracts import StrategyDescriptor
from src.stockpred.strategies.panel import build_panel_from_inputs


@dataclass(frozen=True)
class BatchStaticInputs:
    trade_dates: list[str]
    stock_dimension: pd.DataFrame
    name_history: pd.DataFrame
    industry_history: pd.DataFrame


class BatchDataContext:
    """Keep immutable batch inputs and one evaluation date's panels in memory."""

    def __init__(
        self,
        gateway: object,
        snapshot_digest: str,
        *,
        data_lookback_days: int = 180,
        batch_max_lookback: int | None = None,
        phase_timer: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.snapshot_digest = snapshot_digest
        self.data_lookback_days = data_lookback_days
        self.batch_max_lookback = batch_max_lookback
        self.phase_timer = phase_timer
        self._static: BatchStaticInputs | None = None
        self._panels: dict[tuple[str, str, int], dict[str, pd.DataFrame]] = {}
        self._active_eval_date: str | None = None
        self._lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0

    def static_inputs(self) -> BatchStaticInputs:
        with self._lock:
            if self._static is None:
                self.cache_misses += 1
                self._static = BatchStaticInputs(
                    trade_dates=self.gateway.trade_dates("19900101", "99991231"),
                    stock_dimension=self.gateway.stock_dimension(),
                    name_history=self.gateway.name_history(),
                    industry_history=self.gateway.industry_history(),
                )
            else:
                self.cache_hits += 1
            return self._static

    def panel(self, eval_date: str, max_lookback: int) -> dict[str, pd.DataFrame]:
        with self._lock:
            if self._active_eval_date != eval_date:
                self._panels.clear()
                self._active_eval_date = eval_date
            key = (self.snapshot_digest, eval_date, max_lookback)
            if key not in self._panels:
                self.cache_misses += 1
                static = self._static_inputs_unlocked()
                phase = self.phase_timer.phase("panel_build") if self.phase_timer is not None else nullcontext()
                with phase:
                    self._panels[key] = build_panel_from_inputs(
                        self.gateway,
                        eval_date=eval_date,
                        max_lookback=max_lookback,
                        trade_dates=static.trade_dates,
                        stock_dimension=static.stock_dimension,
                        name_history=static.name_history,
                        industry_history=static.industry_history,
                    )
            else:
                self.cache_hits += 1
            return self._panels[key]

    def panel_for_strategy(
        self,
        eval_date: str,
        descriptor: StrategyDescriptor,
        *,
        data_lookback_days: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        required = max(
            data_lookback_days if data_lookback_days is not None else self.data_lookback_days,
            descriptor.min_warmup_bars + 1,
        )
        full = self.panel(eval_date, max(required, self.batch_max_lookback or 0))
        return {name: frame.tail(required).copy() for name, frame in full.items()}

    def release_eval_date(self) -> None:
        with self._lock:
            self._panels.clear()
            self._active_eval_date = None

    def cache_metrics(self) -> dict[str, int]:
        return {"cache_hits": self.cache_hits, "cache_misses": self.cache_misses}

    def _static_inputs_unlocked(self) -> BatchStaticInputs:
        """Load static inputs; caller MUST hold self._lock."""
        if self._static is None:
            self.cache_misses += 1
            self._static = BatchStaticInputs(
                trade_dates=self.gateway.trade_dates("19900101", "99991231"),
                stock_dimension=self.gateway.stock_dimension(),
                name_history=self.gateway.name_history(),
                industry_history=self.gateway.industry_history(),
            )
        else:
            self.cache_hits += 1
        return self._static
