from __future__ import annotations

import importlib.util
from pathlib import Path


_RUNNER_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "run_r81_combination_batch.py"
)
_SPEC = importlib.util.spec_from_file_location("run_r81_combination_batch", _RUNNER_PATH)
_RUNNER = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_RUNNER)


def test_r81_batch_runner_accepts_output_root():
    args = _RUNNER._parser().parse_args(
        [
            "--idempotency-key",
            "test-key",
            "--champion",
            "champion",
            "--challenger",
            "challenger",
            "--output-root",
            r"C:\custom\r81runs",
        ]
    )

    assert args.output_root == r"C:\custom\r81runs"
