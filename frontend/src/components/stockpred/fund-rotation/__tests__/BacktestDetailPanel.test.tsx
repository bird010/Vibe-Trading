import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BacktestDetailResponse,
  CandidatePoolResponse,
  ComparisonEquityData,
  InstrumentChartResponse,
} from "../types";

const useBacktestDetail = vi.hoisted(() => vi.fn());

vi.mock("../useBacktestDetail", () => ({ useBacktestDetail }));
vi.mock("../FundRotationEquityChart", () => ({
  FundRotationEquityChart: () => <div data-testid="equity-chart" />,
}));
vi.mock("../ClusterIntervalChart", () => ({
  ClusterIntervalChart: ({
    equity,
    candidatePool,
    charts,
    period,
  }: {
    equity: ComparisonEquityData | null;
    candidatePool: CandidatePoolResponse | null;
    charts: Record<string, InstrumentChartResponse>;
    period?: BacktestDetailResponse["period"];
  }) => (
    <div
      data-testid="cluster-interval-chart"
      data-equity={equity ? "present" : "empty"}
      data-candidate-pool={candidatePool ? "present" : "empty"}
      data-charts={Object.keys(charts).join(",")}
      data-period-start={period?.evaluation_start_date ?? ""}
      data-period-end={period?.evaluation_end_date ?? ""}
    />
  ),
}));

import { BacktestDetailPanel } from "../BacktestDetailPanel";

function detail(instruments: BacktestDetailResponse["instruments"]): BacktestDetailResponse {
  return {
    schema_version: "2",
    run_id: "run-1",
    status: "SUCCEEDED",
    mode: "RESEARCH_ONLY",
    result_published: true,
    partial: false,
    publishable_for_comparison: true,
    period: {},
    identity: {},
    resolved_config: {},
    summary: {},
    metrics: {},
    instruments,
    artifacts: [],
    events: [],
  };
}

function state(runDetail: BacktestDetailResponse, loadCharts: ReturnType<typeof vi.fn>) {
  return {
    selectedVariantKey: "variant-1",
    selectedRunId: "run-1",
    detail: runDetail,
    equity: null as ComparisonEquityData | null,
    candidatePool: null as CandidatePoolResponse | null,
    candidatePoolLoading: false,
    candidatePoolError: null,
    activeTab: "chart" as const,
    charts: {},
    loading: false,
    chartLoading: false,
    error: null,
    chartErrors: {},
    closeRun: vi.fn(),
    selectTab: vi.fn(),
    loadCharts,
    loadCandidatePool: vi.fn(),
  };
}

function chart(tsCode: string, signalDate: string, tradeDate: string, name?: string): InstrumentChartResponse {
  return {
    ts_code: tsCode,
    run_id: "run-1",
    name,
    signals: [{ date: signalDate, target_weight: 0.25 }],
    trades: [{ trade_date: tradeDate, action: "BUY", status: "FILLED", filled: 10, price: 3.5 }],
    ohlcv: [],
    positions: [],
    orders: [],
    ohlcv_source: { available: true, version: 1 },
    mode: "RESEARCH_ONLY",
  };
}

describe("BacktestDetailPanel chart lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not batch-load charts when the detail has no instruments", async () => {
    const loadCharts = vi.fn();
    useBacktestDetail.mockReturnValue(state(detail([]), loadCharts));

    render(<BacktestDetailPanel />);

    await waitFor(() => expect(loadCharts).not.toHaveBeenCalled());
  });

  it("batch-loads charts once when the detail has instruments", async () => {
    const loadCharts = vi.fn();
    useBacktestDetail.mockReturnValue(
      state(detail([{ ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true }]), loadCharts),
    );

    render(<BacktestDetailPanel />);

    await waitFor(() => expect(loadCharts).toHaveBeenCalledTimes(1));
  });

  it("shows missing task overview fields and lifecycle without duplicating identity", () => {
    const runDetail = detail([]);
    runDetail.batch_id = "batch-1";
    runDetail.variant_key = "variant-1";
    runDetail.strategy_id = "correlation_representative";
    runDetail.label = "旧标签数据";
    runDetail.quality_status = "RESEARCH_ONLY_UNVERIFIED_UNIVERSE";
    runDetail.events = [
      { seq: 1, ts: "2024-01-01T09:00:00Z", stage: "PREPARING_DATA" },
      { seq: 2, ts: "2024-01-01T09:01:00Z", stage: "SUCCEEDED" },
    ];
    const panelState = state(runDetail, vi.fn());
    panelState.activeTab = "overview";
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByText("任务总览")).toBeInTheDocument();
    expect(screen.getByText("batch-1")).toBeInTheDocument();
    expect(screen.getByText("执行生命周期")).toBeInTheDocument();
    expect(screen.getByText("PREPARING_DATA")).toBeInTheDocument();
    expect(screen.queryByText("参数标签")).toBeNull();
    expect(screen.queryByText("旧标签数据")).toBeNull();
    expect(screen.getAllByText("Run identity")).toHaveLength(1);
  });

  it("wires chart responses through yearly evidence with chronological years", async () => {
    const loadCharts = vi.fn();
    const runDetail = detail([
      { ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true },
      { ts_code: "159915.SZ", has_signal: true, has_order: true, has_trade: true, has_position: true },
    ]);
    const panelState = state(runDetail, loadCharts);
    panelState.charts = {
      "510300.SH": chart("510300.SH", "20240109", "20240110"),
      "159915.SZ": chart("159915.SZ", "20240115", "20240116"),
    };
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    await waitFor(() => expect(screen.getAllByRole("heading", { level: 3 }).slice(1).map((heading) => heading.textContent)).toEqual([
      "2024 年 · 操作标的 2 个",
    ]));
    expect(screen.getByRole("heading", { name: "510300.SH · K 线与交易证据" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "159915.SZ · K 线与交易证据" })).toBeInTheDocument();
    expect(screen.getByText("510300.SH · 数据快照版本：1")).toBeInTheDocument();
    expect(screen.getByText("159915.SZ · 数据快照版本：1")).toBeInTheDocument();
    expect(screen.queryByText("信号")).toBeNull();
    expect(screen.queryByText("25.0%")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(loadCharts).not.toHaveBeenCalled();
  });

  it("shows the candidate pool tab with representative code, name and category", () => {
    const runDetail = detail([]);
    const panelState = state(runDetail, vi.fn());
    panelState.activeTab = "candidate_pool";
    panelState.candidatePool = {
      run_id: "run-1",
      reclusters: [
        {
          week: "20240105",
          num_etfs: 603,
          overall: "REJECT",
          max_cluster_share: 0.9,
          max_cluster_share_status: "REJECT",
          effective_cluster_count: 1.2,
          effective_cluster_count_status: "REJECT",
          representatives: [
            {
              cluster_id: 1,
              cluster_size: 558,
              selected_code: "510300.SH",
              selected_name: "沪深300ETF",
              selected_fund_type: "股票型",
              lock_maintained: false,
              exclusion_reason: "",
            },
            {
              cluster_id: 2,
              cluster_size: 1,
              selected_code: null,
              selected_name: null,
              selected_fund_type: null,
              lock_maintained: false,
              exclusion_reason: "SINGLE_MEMBER_CLUSTER",
            },
          ],
        },
      ],
    };
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByRole("button", { name: "基金候选池" })).toBeInTheDocument();
    expect(screen.getByText("510300.SH")).toBeInTheDocument();
    expect(screen.getByText("沪深300ETF")).toBeInTheDocument();
    expect(screen.getByText("股票型")).toBeInTheDocument();
    expect(screen.getByText(/门禁：\s*REJECT/)).toBeInTheDocument();
    expect(screen.getByText("SINGLE_MEMBER_CLUSTER")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows Economic Role candidates and labels the interval as a role interval", () => {
    const runDetail = detail([]);
    runDetail.strategy_id = "ai_rotation_r80_economic_role_fixed_rep";
    runDetail.artifacts = [
      {
        role: "role_history",
        file: "strategy_role_history.json",
        media_type: "application/json",
        producer: "ai_rotation_r80_economic_role_fixed_rep",
        columns: [],
      },
    ];
    const loadCharts = vi.fn();
    const panelState = state(runDetail, loadCharts);
    panelState.activeTab = "candidate_pool";
    panelState.candidatePool = {
      run_id: "run-1",
      kind: "ECONOMIC_ROLE",
      reclusters: [],
      role_snapshots: [
        {
          signal_date: "20250103",
          is_refresh: true,
          roles: [
            {
              role_id: "CN_GROWTH_EQUITY",
              role_name: "中国成长权益",
              members: ["159915.SZ", "159949.SZ"],
              members_as_of: "20250103",
              representative: "159915.SZ",
              representative_as_of: "20250103",
              selection_mode: "REGULAR_REFRESH",
            },
          ],
        },
      ],
    };
    panelState.charts = {
      "159915.SZ": chart("159915.SZ", "20250103", "20250106", "中国成长 ETF"),
      "159949.SZ": chart("159949.SZ", "20250103", "20250106", "创业板 ETF"),
    };
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByRole("button", { name: "角色区间" })).toBeInTheDocument();
    expect(screen.getByText("基金候选池 · Economic Role")).toBeInTheDocument();
    expect(screen.getByText("中国成长权益")).toBeInTheDocument();
    expect(screen.getAllByText("中国成长 ETF（159915.SZ）").length).toBeGreaterThan(0);
    expect(screen.getByText("创业板 ETF（159949.SZ）")).toBeInTheDocument();
    expect(loadCharts).not.toHaveBeenCalled();
  });

  it("loads Role member charts when the candidate pool tab is active", async () => {
    const runDetail = detail([]);
    runDetail.artifacts = [
      {
        role: "role_history",
        file: "strategy_role_history.json",
        media_type: "application/json",
        producer: "ai_rotation_r80_economic_role_fixed_rep",
        columns: [],
      },
    ];
    const loadCharts = vi.fn();
    const panelState = state(runDetail, loadCharts);
    panelState.activeTab = "candidate_pool";
    panelState.candidatePool = {
      run_id: "run-1",
      kind: "ECONOMIC_ROLE",
      reclusters: [],
      role_snapshots: [
        {
          signal_date: "20250103",
          is_refresh: true,
          roles: [
            {
              role_id: "CN_GROWTH_EQUITY",
              role_name: "中国成长权益",
              members: ["159915.SZ"],
              members_as_of: "20250103",
              representative: "159915.SZ",
              representative_as_of: "20250103",
              selection_mode: "REGULAR_REFRESH",
            },
          ],
        },
      ],
    };
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    await waitFor(() => expect(loadCharts).toHaveBeenCalledTimes(1));
  });

  it("shows a local candidate pool error without replacing the detail panel", () => {
    const panelState = state(detail([]), vi.fn());
    panelState.activeTab = "candidate_pool";
    panelState.candidatePoolError = "candidate pool unavailable";
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByText("candidate pool unavailable")).toBeInTheDocument();
    expect(screen.getByText("单次回测详情")).toBeInTheDocument();
  });

  it("lazy-loads candidate pool data when its tab is active and data is absent", async () => {
    const loadCandidatePool = vi.fn();
    const panelState = state(detail([]), vi.fn());
    panelState.activeTab = "candidate_pool";
    panelState.loadCandidatePool = loadCandidatePool;
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    await waitFor(() => expect(loadCandidatePool).toHaveBeenCalledTimes(1));
  });

  it("shows the cluster interval tab and lazy-loads both data sources", async () => {
    const loadCharts = vi.fn();
    const loadCandidatePool = vi.fn();
    const panelState = state(
      detail([{ ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true }]),
      loadCharts,
    );
    panelState.activeTab = "cluster_interval";
    panelState.loadCandidatePool = loadCandidatePool;
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByRole("button", { name: "聚类区间" })).toBeInTheDocument();
    expect(screen.getByTestId("cluster-interval-chart")).toBeInTheDocument();
    await waitFor(() => {
      expect(loadCandidatePool).toHaveBeenCalledTimes(1);
      expect(loadCharts).toHaveBeenCalledTimes(1);
    });
  });

  it("passes cached equity, candidate pool and charts without reloading them", async () => {
    const loadCharts = vi.fn();
    const loadCandidatePool = vi.fn();
    const panelState = state(detail([]), loadCharts);
    panelState.activeTab = "cluster_interval";
    panelState.equity = {
      dates: ["20240102"],
      series: { strategy: [1] },
    };
    panelState.candidatePool = { run_id: "run-1", reclusters: [] };
    panelState.charts = { "510300.SH": chart("510300.SH", "20240102", "20240103") };
    panelState.loadCandidatePool = loadCandidatePool;
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByTestId("cluster-interval-chart")).toHaveAttribute("data-equity", "present");
    expect(screen.getByTestId("cluster-interval-chart")).toHaveAttribute("data-candidate-pool", "present");
    expect(screen.getByTestId("cluster-interval-chart")).toHaveAttribute("data-charts", "510300.SH");
    await waitFor(() => {
      expect(loadCandidatePool).not.toHaveBeenCalled();
      expect(loadCharts).not.toHaveBeenCalled();
    });
  });

  it("loads missing charts when the cluster interval cache is partial", async () => {
    const loadCharts = vi.fn();
    const loadCandidatePool = vi.fn();
    const panelState = state(
      detail([
        { ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true },
        { ts_code: "159915.SZ", has_signal: true, has_order: true, has_trade: true, has_position: true },
      ]),
      loadCharts,
    );
    panelState.activeTab = "cluster_interval";
    panelState.candidatePool = { run_id: "run-1", reclusters: [] };
    panelState.charts = { "510300.SH": chart("510300.SH", "20240102", "20240103") };
    panelState.loadCandidatePool = loadCandidatePool;
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    await waitFor(() => expect(loadCharts).toHaveBeenCalledTimes(1));
    expect(loadCandidatePool).not.toHaveBeenCalled();
  });

  it("keeps successful cluster charts visible, lists only failures, and retries missing items", () => {
    const loadCharts = vi.fn();
    const runDetail = detail([
      { ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true },
      { ts_code: "159915.SZ", has_signal: true, has_order: true, has_trade: true, has_position: true },
    ]);
    runDetail.period = {
      evaluation_start_date: "20240102",
      evaluation_end_date: "20241231",
    };
    const panelState = state(runDetail, loadCharts);
    panelState.activeTab = "cluster_interval";
    panelState.candidatePool = { run_id: "run-1", reclusters: [] };
    panelState.charts = { "510300.SH": chart("510300.SH", "20240102", "20240103") };
    panelState.chartErrors = { "159915.SZ": "K 线不可用" };
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByTestId("cluster-interval-chart")).toHaveAttribute("data-charts", "510300.SH");
    expect(screen.getByTestId("cluster-interval-chart")).toHaveAttribute("data-period-start", "20240102");
    expect(screen.getByTestId("cluster-interval-chart")).toHaveAttribute("data-period-end", "20241231");
    expect(screen.getByText("159915.SZ：K 线不可用")).toBeInTheDocument();
    expect(screen.queryByText(/510300\.SH：/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重试失败项" }));
    expect(loadCharts).toHaveBeenCalledTimes(1);
  });

  it("keeps cached cluster charts visible while missing charts are loading", () => {
    const panelState = state(detail([
      { ts_code: "510300.SH", has_signal: true, has_order: true, has_trade: true, has_position: true },
      { ts_code: "159915.SZ", has_signal: true, has_order: true, has_trade: true, has_position: true },
    ]), vi.fn());
    panelState.activeTab = "cluster_interval";
    panelState.candidatePool = { run_id: "run-1", reclusters: [] };
    panelState.charts = { "510300.SH": chart("510300.SH", "20240102", "20240103") };
    panelState.chartLoading = true;
    useBacktestDetail.mockReturnValue(panelState);

    render(<BacktestDetailPanel />);

    expect(screen.getByTestId("cluster-interval-chart")).toBeInTheDocument();
    expect(screen.getByText("正在加载其余基金行情…")).toBeInTheDocument();
  });
});
