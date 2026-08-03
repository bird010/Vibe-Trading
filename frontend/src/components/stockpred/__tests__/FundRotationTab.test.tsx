import { render, screen } from "@testing-library/react";
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
  ],
  strategyDetails: new Map(),
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

  it("renders progress section", () => {
    render(<FundRotationTab />);
    expect(screen.getByText("批次进度")).toBeDefined();
    expect(screen.getByText(/暂无批次/)).toBeDefined();
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
    expect(screen.getByText("未选择策略")).toBeDefined();
  });
});
