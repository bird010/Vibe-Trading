import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchBacktestDetail: vi.fn(),
  fetchBacktestEquity: vi.fn(),
  fetchInstrumentChart: vi.fn(),
}));

vi.mock("../api", () => api);

import type { BacktestDetailResponse } from "../types";
import { useBacktestDetail } from "../useBacktestDetail";

function detail(runId: string, instrument = "510300.SH"): BacktestDetailResponse {
  return {
    schema_version: "2",
    run_id: runId,
    batch_id: "batch-1",
    variant_key: `variant-${runId}`,
    strategy_id: "correlation_representative",
    status: "SUCCEEDED",
    quality_status: "VALID",
    mode: "RESEARCH_ONLY",
    result_published: true,
    partial: false,
    publishable_for_comparison: true,
    period: {},
    identity: {},
    resolved_config: {},
    summary: {},
    metrics: { sharpe: 1.2 },
    instruments: [
      {
        ts_code: instrument,
        has_signal: true,
        has_order: true,
        has_trade: true,
        has_position: true,
      },
    ],
    artifacts: [],
    events: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useBacktestDetail.getState().closeRun();
  api.fetchBacktestEquity.mockResolvedValue(null);
});

describe("useBacktestDetail", () => {
  it("loads a selected child run and chooses its first instrument", async () => {
    api.fetchBacktestDetail.mockResolvedValue(detail("run-1"));
    api.fetchBacktestEquity.mockResolvedValue({
      dates: ["20240102", "20240103"],
      series: { strategy: [1, 1.01] },
    });

    await useBacktestDetail.getState().openRun("variant-1", "run-1");

    const state = useBacktestDetail.getState();
    expect(state.selectedVariantKey).toBe("variant-1");
    expect(state.selectedRunId).toBe("run-1");
    expect(state.detail?.run_id).toBe("run-1");
    expect(state.selectedInstrument).toBe("510300.SH");
    expect(state.equity?.series.strategy).toEqual([1, 1.01]);
  });

  it("prefers an instrument with actual trades over signal-only instruments", async () => {
    const runDetail = detail("run-1");
    runDetail.instruments = [
      {
        ts_code: "510050.SH",
        has_signal: true,
        has_order: false,
        has_trade: false,
        has_position: false,
      },
      {
        ts_code: "159915.SZ",
        has_signal: true,
        has_order: true,
        has_trade: true,
        has_position: true,
      },
    ];
    api.fetchBacktestDetail.mockResolvedValue(runDetail);

    await useBacktestDetail.getState().openRun("variant-1", "run-1");

    expect(useBacktestDetail.getState().selectedInstrument).toBe("159915.SZ");
  });

  it("discards a late response from the previously selected run", async () => {
    let resolveFirst: ((value: BacktestDetailResponse) => void) | null = null;
    api.fetchBacktestDetail.mockImplementation((runId: string) => {
      if (runId === "run-a") {
        return new Promise<BacktestDetailResponse>((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve(detail("run-b", "159915.SZ"));
    });

    const first = useBacktestDetail.getState().openRun("variant-a", "run-a");
    await useBacktestDetail.getState().openRun("variant-b", "run-b");
    resolveFirst?.(detail("run-a"));
    await first;

    const state = useBacktestDetail.getState();
    expect(state.selectedRunId).toBe("run-b");
    expect(state.detail?.run_id).toBe("run-b");
    expect(state.selectedInstrument).toBe("159915.SZ");
  });

  it("loads chart evidence for the selected instrument", async () => {
    api.fetchBacktestDetail.mockResolvedValue(detail("run-1"));
    api.fetchInstrumentChart.mockResolvedValue({
      run_id: "run-1",
      ts_code: "510300.SH",
      signals: [],
      trades: [],
      ohlcv: [],
      positions: [],
      orders: [],
      ohlcv_source: { available: false },
      mode: "RESEARCH_ONLY",
    });
    await useBacktestDetail.getState().openRun("variant-1", "run-1");

    await useBacktestDetail.getState().selectInstrument("510300.SH");

    expect(api.fetchInstrumentChart).toHaveBeenCalledWith(
      "run-1",
      "510300.SH",
      2000,
      expect.any(AbortSignal),
    );
    expect(useBacktestDetail.getState().chart?.ts_code).toBe("510300.SH");
  });
});
