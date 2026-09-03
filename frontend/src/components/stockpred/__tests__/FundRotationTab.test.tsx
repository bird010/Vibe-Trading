import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const mockState = vi.hoisted(() => ({
  catalogVersion: "v1",
  strategies: [
    {
      strategy_id: "baseline",
      name: "基线策略",
      description: "Baseline",
      interface_version: "1.0",
      implementation_hash: "abc",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "correlation_representative",
      name: "相关性聚类代表ETF",
      description: "Correlation representative ETF",
      interface_version: "1.0",
      implementation_hash: "def",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "ai_rotation_r11_persist_geom",
      name: "持续几何动量相关性代表ETF",
      description: "R11",
      interface_version: "1.0",
      implementation_hash: "r11",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "ai_rotation_r34_staged_reentry",
      name: "半仓试探再入场持续几何动量相关性代表ETF",
      description: "R34",
      interface_version: "1.0",
      implementation_hash: "r34",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "ai_rotation_r39_incumbent_carry",
      name: "持续目标承接释放资金",
      description: "R39",
      interface_version: "1.0",
      implementation_hash: "r39",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "ai_rotation_r59_r39_signal_r57_positive_slope",
      name: "R57 三因子正斜率信号承接释放资金",
      description: "R59 tuning1",
      interface_version: "1.0",
      implementation_hash: "r59",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "ai_rotation_r76_fixed_short_bond",
      name: "R76 固定短债",
      description: "R76 fixed short bond",
      interface_version: "1.0",
      implementation_hash: "r76",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
    {
      strategy_id: "ai_rotation_r83_r81_r57_r77_combo",
      name: "R83 动态代表 R57 信号 R77 防御",
      description: "R83",
      interface_version: "1.0",
      implementation_hash: "r83",
      supported_universe: ["cn_etf"],
      warmup_trade_days: 260,
      required_datasets: ["fund"],
      required_fields: ["close"],
      frequency: "weekly",
    },
  ],
  strategyDetails: new Map([
    [
      "baseline",
      {
        strategy_id: "baseline",
        name: "基线策略",
        description: "Baseline",
        interface_version: "1.0",
        implementation_hash: "abc",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h1",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "correlation_representative",
      {
        strategy_id: "correlation_representative",
        name: "相关性聚类代表ETF",
        description: "Correlation representative ETF",
        interface_version: "1.0",
        implementation_hash: "def",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h2",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "ai_rotation_r11_persist_geom",
      {
        strategy_id: "ai_rotation_r11_persist_geom",
        name: "持续几何动量相关性代表ETF",
        description: "R11",
        interface_version: "1.0",
        implementation_hash: "r11",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h-r11",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "ai_rotation_r34_staged_reentry",
      {
        strategy_id: "ai_rotation_r34_staged_reentry",
        name: "半仓试探再入场持续几何动量相关性代表ETF",
        description: "R34",
        interface_version: "1.0",
        implementation_hash: "r34",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h-r34",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "ai_rotation_r39_incumbent_carry",
      {
        strategy_id: "ai_rotation_r39_incumbent_carry",
        name: "持续目标承接释放资金",
        description: "R39",
        interface_version: "1.0",
        implementation_hash: "r39",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h-r39",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "ai_rotation_r59_r39_signal_r57_positive_slope",
      {
        strategy_id: "ai_rotation_r59_r39_signal_r57_positive_slope",
        name: "R57 三因子正斜率信号承接释放资金",
        description: "R59 tuning1",
        interface_version: "1.0",
        implementation_hash: "r59",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h-r59",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "ai_rotation_r76_fixed_short_bond",
      {
        strategy_id: "ai_rotation_r76_fixed_short_bond",
        name: "R76 固定短债",
        description: "R76 fixed short bond",
        interface_version: "1.0",
        implementation_hash: "r76",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h-r76",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
    [
      "ai_rotation_r83_r81_r57_r77_combo",
      {
        strategy_id: "ai_rotation_r83_r81_r57_r77_combo",
        name: "R83 动态代表 R57 信号 R77 防御",
        description: "R83",
        interface_version: "1.0",
        implementation_hash: "r83",
        supported_universe: ["cn_etf"],
        warmup_trade_days: 260,
        required_datasets: ["fund"],
        required_fields: ["close"],
        frequency: "weekly",
        config_schema: { type: "object", properties: {} },
        config_schema_version: "v1",
        config_schema_hash: "h-r83",
        default_config: {},
        parameter_descriptions: {},
        artifact_roles: [],
      },
    ],
  ]),
  catalogLoading: false,
  catalogError: null,
  batches: [],
  activeBatchId: null,
  activeBatch: null,
  comparison: null,
  loading: false,
  error: null,
  events: [],
  fetchCatalog: vi.fn(),
  fetchBatches: vi.fn(),
  submitStrategyBatch: vi.fn(),
  selectBatch: vi.fn(),
  cancelActiveBatch: vi.fn(),
  connectBatchSSE: vi.fn(),
  disconnectSSE: vi.fn(),
  reset: vi.fn(),
}));

vi.mock("../fund-rotation/useFundRotation", () => ({
  useFundRotation: () => mockState,
}));

import { FundRotationTab } from "../fund-rotation/FundRotationTab";

describe("FundRotationTab (batch UI)", () => {
  beforeEach(() => {
    mockState.batches = [];
    mockState.activeBatchId = null;
    mockState.activeBatch = null;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      json: async () => null,
      text: async () => null,
    }));
  });

  it("renders RESEARCH_ONLY warning banner", () => {
    render(<FundRotationTab />);
    expect(
      screen.getByText(/RESEARCH_ONLY.*仅供研究/),
    ).toBeDefined();
  });

  it("renders strategy config section", () => {
    render(<FundRotationTab />);
    expect(screen.getByText("策略批次配置")).toBeDefined();
    expect(screen.getByText("开始日期")).toBeDefined();
    expect(screen.getByText("结束日期")).toBeDefined();
    expect(screen.getByText("初始资金")).toBeDefined();
  });

  it("uses the requested default dates and R83 research champion strategy", () => {
    render(<FundRotationTab />);

    expect(screen.getByLabelText("开始日期")).toHaveValue("2022-08-01");
    expect(screen.getByLabelText("结束日期")).toHaveValue("2026-08-01");
    expect(screen.getAllByRole("combobox")[0]).toHaveValue(
      "ai_rotation_r83_r81_r57_r77_combo",
    );
  });

  it("renders empty history section", () => {
    render(<FundRotationTab />);
    expect(screen.getByText("历史批次")).toBeDefined();
    expect(screen.getByText(/暂无批次/)).toBeDefined();
    expect(screen.queryByText("批次进度")).toBeNull();
  });

  it("renders a history batch", () => {
    mockState.batches = [{ batch_id: "batch-history", status: "SUCCEEDED" }];
    mockState.activeBatchId = "batch-history";
    mockState.activeBatch = {
      batch_id: "batch-history",
      state: { schema_version: "1", stage: "SUCCEEDED", batch_id: "batch-history", mode: "RESEARCH_ONLY" },
      resolved: {
        batch_id: "batch-history",
        schema_version: "1",
        mode: "RESEARCH_ONLY",
        catalog_version: "v1",
        framework_implementation_hash: "hash",
        variants: [{ variant_key: "v1", strategy_id: "baseline", data_start: "2020-01-01", decision_start_date: "2020-01-01", anchor_decision_date: "2020-01-01", status: "SUCCEEDED", run_id: "run-1" }],
        plan: { data_start: "2020-01-01", earliest_decision_start_date: "2020-01-01", evaluation_start_date: "2020-01-01", evaluation_end_date: "2020-12-31", variants: [] },
        executed_order: [],
      },
      child_runs: [{ schema_version: "1", stage: "SUCCEEDED", batch_id: "batch-history", run_id: "run-1", variant_key: "v1", strategy_id: "baseline", mode: "RESEARCH_ONLY" }],
      mode: "RESEARCH_ONLY",
    };
    mockState.selectBatch.mockClear();

    render(<FundRotationTab />);

    expect(screen.getByText("历史批次")).toBeDefined();
    expect(screen.getByText("batch-histor…")).toBeDefined();
    expect(screen.getAllByText("完成").length).toBeGreaterThan(0);
    const batchRow = screen.getByRole("button", {
      name: /batch-histor…完成/,
    });
    expect(batchRow).toHaveClass("bg-blue-50", "text-blue-700");
    fireEvent.click(batchRow);
    expect(mockState.selectBatch).toHaveBeenCalledWith("batch-history");
    expect(screen.queryByText("批次进度")).toBeNull();
    expect(screen.queryByText("逐期选基明细")).toBeNull();
  });

  it("renders comparison section", () => {
    render(<FundRotationTab />);
    expect(screen.getByText("策略比较")).toBeDefined();
  });

  it("renders submit button", () => {
    render(<FundRotationTab />);
    expect(screen.getByText("提交策略批次")).toBeDefined();
  });

  it("renders variant editor with strategy option", () => {
    render(<FundRotationTab />);
    expect(screen.getByText("+ 添加策略变体")).toBeDefined();
    expect(
      screen.getAllByText("半仓试探再入场持续几何动量相关性代表ETF").length,
    ).toBeGreaterThan(0);
  });
});
