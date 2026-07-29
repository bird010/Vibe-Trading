"""Small, serializable timings and artifact counts for strategy batches."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterator

from src.stockpred.run_store import atomic_json

PHASES = ("data_load", "panel_build", "factor_compute", "execution", "artifact_write")


class PhaseTimer:
    def __init__(self, *, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self._timings = {phase: 0.0 for phase in PHASES}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if name not in self._timings:
            raise ValueError(f"unsupported phase: {name}")
        started = self._clock()
        try:
            yield
        finally:
            self._timings[name] += self._clock() - started

    def as_dict(self) -> dict[str, float]:
        return {name: round(seconds, 6) for name, seconds in self._timings.items()}


def write_phase_metrics(
    run_dir: Path,
    timer: PhaseTimer,
    *,
    read_metrics: dict[str, int] | None = None,
    batch_shared: dict[str, object] | None = None,
) -> dict[str, object]:
    artifacts = Path(run_dir) / "artifacts"
    files = [path for path in artifacts.rglob("*") if path.is_file()] if artifacts.is_dir() else []
    payload: dict[str, object] = {
        "timings": timer.as_dict(),
        "cache": {"hits": int((read_metrics or {}).get("cache_hits", 0)), "misses": int((read_metrics or {}).get("cache_misses", 0))},
        "rows_read": int((read_metrics or {}).get("rows_read", 0)),
        "artifacts": {"files": len(files), "bytes": sum(path.stat().st_size for path in files)},
    }
    if batch_shared is not None:
        payload["batch_shared"] = batch_shared
    atomic_json(Path(run_dir) / "phase_metrics.json", payload)
    return payload
