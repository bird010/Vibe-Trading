import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchBacktestDetail: vi.fn(),
  fetchBacktestEquity: vi.fn(),
  fetchCandidatePool: vi.fn(),
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

function chart(runId: string, tsCode: string) {
  return {
    run_id: runId,
    ts_code: tsCode,
    signals: [],
    trades: [],
    ohlcv: [],
    positions: [],
    orders: [],
    ohlcv_source: { available: false },
    mode: "RESEARCH_ONLY" as const,
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

  it("loads candidate pool data for the selected run and clears it on run change", async () => {
    const candidatePool = { run_id: "run-1", reclusters: [] };
    api.fetchBacktestDetail.mockResolvedValue(detail("run-1"));
    api.fetchCandidatePool.mockResolvedValue(candidatePool);

    await useBacktestDetail.getState().openRun("variant-1", "run-1");
    await useBacktestDetail.getState().loadCandidatePool();

    expect(api.fetchCandidatePool).toHaveBeenCalledWith(
      "run-1",
      expect.any(AbortSignal),
    );
    expect(useBacktestDetail.getState().candidatePool).toEqual(candidatePool);

    api.fetchBacktestDetail.mockResolvedValue(detail("run-2"));
    await useBacktestDetail.getState().openRun("variant-2", "run-2");
    expect(useBacktestDetail.getState().candidatePool).toBeNull();
  });

  it("keeps detail data when candidate pool loading fails", async () => {
    api.fetchBacktestDetail.mockResolvedValue(detail("run-1"));
    api.fetchCandidatePool.mockRejectedValue(new Error("candidate pool unavailable"));

    await useBacktestDetail.getState().openRun("variant-1", "run-1");
    await useBacktestDetail.getState().loadCandidatePool();

    expect(useBacktestDetail.getState().detail?.run_id).toBe("run-1");
    expect(useBacktestDetail.getState().candidatePool).toBeNull();
    expect(useBacktestDetail.getState().candidatePoolError).toBe(
      "candidate pool unavailable",
    );
  });

  it("discards a late candidate pool response from the previous run", async () => {
    let resolveFirst: ((value: { run_id: string; reclusters: [] }) => void) | null = null;
    api.fetchBacktestDetail.mockImplementation((runId: string) => Promise.resolve(detail(runId)));
    api.fetchCandidatePool.mockImplementation((runId: string) => {
      if (runId === "run-a") {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({ run_id: "run-b", reclusters: [] });
    });

    await useBacktestDetail.getState().openRun("variant-a", "run-a");
    const firstLoad = useBacktestDetail.getState().loadCandidatePool();
    await useBacktestDetail.getState().openRun("variant-b", "run-b");
    await useBacktestDetail.getState().loadCandidatePool();
    resolveFirst?.({ run_id: "run-a", reclusters: [] });
    await firstLoad;

    expect(useBacktestDetail.getState().selectedRunId).toBe("run-b");
    expect(useBacktestDetail.getState().candidatePool?.run_id).toBe("run-b");
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

  it("loads chart evidence for every instrument into the chart map", async () => {
    const runDetail = detail("run-1");
    runDetail.instruments = [
      { ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true },
      { ts_code: "159915.SZ", has_signal: true, has_order: true, has_trade: true, has_position: true },
    ];
    api.fetchBacktestDetail.mockResolvedValue(runDetail);
    api.fetchInstrumentChart.mockImplementation((_runId: string, tsCode: string) =>
      Promise.resolve(chart("run-1", tsCode)),
    );

    await useBacktestDetail.getState().openRun("variant-1", "run-1");
    await useBacktestDetail.getState().loadCharts();

    expect(api.fetchInstrumentChart).toHaveBeenCalledTimes(2);
    expect(useBacktestDetail.getState().charts).toEqual({
      "510300.SH": chart("run-1", "510300.SH"),
      "159915.SZ": chart("run-1", "159915.SZ"),
    });
  });

  it("keeps successful charts and records an error when one instrument fails", async () => {
    const runDetail = detail("run-1");
    runDetail.instruments = [
      { ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true },
      { ts_code: "159915.SZ", has_signal: true, has_order: true, has_trade: true, has_position: true },
    ];
    api.fetchBacktestDetail.mockResolvedValue(runDetail);
    api.fetchInstrumentChart.mockImplementation((_runId: string, tsCode: string) =>
      tsCode === "510300.SH"
        ? Promise.resolve(chart("run-1", tsCode))
        : Promise.reject(new Error("159915.SZ chart unavailable")),
    );

    await useBacktestDetail.getState().openRun("variant-1", "run-1");
    await useBacktestDetail.getState().loadCharts();

    expect(useBacktestDetail.getState().charts).toEqual({
      "510300.SH": chart("run-1", "510300.SH"),
    });
    expect(useBacktestDetail.getState().chartErrors).toEqual({
      "159915.SZ": "159915.SZ chart unavailable",
    });
  });

  it("does not add a previous run's late chart responses to the current run", async () => {
    const firstCharts = new Map<string, (value: ReturnType<typeof chart>) => void>();
    api.fetchBacktestDetail.mockImplementation((runId: string) => Promise.resolve(detail(runId)));
    api.fetchInstrumentChart.mockImplementation((runId: string, tsCode: string) => {
      if (runId === "run-a") {
        return new Promise((resolve) => firstCharts.set(tsCode, resolve));
      }
      return Promise.resolve(chart(runId, tsCode));
    });

    await useBacktestDetail.getState().openRun("variant-a", "run-a");
    const firstLoad = useBacktestDetail.getState().loadCharts();
    await useBacktestDetail.getState().openRun("variant-b", "run-b");
    await useBacktestDetail.getState().loadCharts();
    firstCharts.get("510300.SH")?.(chart("run-a", "510300.SH"));
    await firstLoad;

    expect(useBacktestDetail.getState().selectedRunId).toBe("run-b");
    expect(useBacktestDetail.getState().charts).toEqual({
      "510300.SH": chart("run-b", "510300.SH"),
    });
  });
});
