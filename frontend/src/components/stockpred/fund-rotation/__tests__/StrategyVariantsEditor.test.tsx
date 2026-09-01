import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StrategyVariantsEditor } from "../StrategyVariantsEditor";
import type { StrategyDetail } from "../types";

const STRATEGIES: StrategyDetail[] = [
  {
    strategy_id: "baseline",
    name: "基线策略",
    description: "Baseline ETF轮动",
    interface_version: "1.0",
    implementation_hash: "abc",
    supported_universe: ["cn_etf"],
    warmup_trade_days: 260,
    required_datasets: ["fund"],
    required_fields: ["close", "amount"],
    frequency: "weekly",
    config_schema: {
      type: "object",
      properties: {
        k: { type: "integer", description: "聚类数", default: 8 },
      },
    },
    config_schema_version: "v1",
    config_schema_hash: "h1",
    default_config: { k: 8 },
    parameter_descriptions: { k: "聚类数" },
    artifact_roles: [],
  },
  {
    strategy_id: "corr",
    name: "相关性策略",
    description: "Correlation-based",
    interface_version: "1.0",
    implementation_hash: "def",
    supported_universe: ["cn_etf"],
    warmup_trade_days: 260,
    required_datasets: ["fund"],
    required_fields: ["close"],
    frequency: "weekly",
    config_schema: {
      type: "object",
      properties: {
        threshold: { type: "number", description: "阈值", default: 0.5 },
      },
    },
    config_schema_version: "v1",
    config_schema_hash: "h2",
    default_config: { threshold: 0.5 },
    parameter_descriptions: { threshold: "阈值" },
    artifact_roles: [],
  },
];

describe("StrategyVariantsEditor", () => {
  it("renders with one initial variant", () => {
    const onChange = vi.fn();
    render(
      <StrategyVariantsEditor
        strategies={STRATEGIES}
        variants={[
          { uiKey: "v1", strategyId: "baseline", params: { k: 8 } },
        ]}
        onChange={onChange}
      />,
    );
    expect(screen.getAllByText("基线策略").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/变体\s*1/)).toBeDefined();
  });

  it("removes the variant label control and keeps the strategy selector flexible", () => {
    const onChange = vi.fn();
    render(
      <StrategyVariantsEditor
        strategies={[
          { ...STRATEGIES[0], name: "一个非常长的策略名称用于验证布局不会挤压控件" },
        ]}
        variants={[
          {
            uiKey: "v1",
            strategyId: "baseline",
            params: { k: 8 },
          },
        ]}
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("combobox", { name: "策略" })).toHaveClass(
      "min-w-0",
      "flex-1",
      "truncate",
    );
    expect(screen.getByTestId("variant-first-row")).toHaveClass("min-w-0");
    expect(screen.getByTestId("variant-drag-handle")).toHaveClass("shrink-0");
    expect(screen.queryByPlaceholderText("变体标签（可选）")).toBeNull();
    expect(screen.getByTitle("复制变体")).toHaveClass("shrink-0");
    expect(screen.getByTitle("删除变体")).toHaveClass("shrink-0");
  });

  it("adds a variant via the dropdown", () => {
    const onChange = vi.fn();
    const variants = [
      { uiKey: "v1", strategyId: "baseline", params: { k: 8 } },
    ];
    render(
      <StrategyVariantsEditor
        strategies={STRATEGIES}
        variants={variants}
        onChange={onChange}
      />,
    );
    // Select "相关性策略" from the add dropdown
    const selects = screen.getAllByRole("combobox");
    // The add dropdown is the last select
    const addSelect = selects[selects.length - 1];
    fireEvent.change(addSelect, { target: { value: "corr" } });
    expect(onChange).toHaveBeenCalledTimes(1);
    const newVariants = onChange.mock.calls[0][0];
    expect(newVariants.length).toBe(2);
    expect(newVariants[1].strategyId).toBe("corr");
  });

  it("copies a variant", () => {
    const onChange = vi.fn();
    render(
      <StrategyVariantsEditor
        strategies={STRATEGIES}
        variants={[
          { uiKey: "v1", strategyId: "baseline", params: { k: 8 } },
        ]}
        onChange={onChange}
      />,
    );
    const copyBtn = screen.getByTitle("复制变体");
    fireEvent.click(copyBtn);
    expect(onChange).toHaveBeenCalledTimes(1);
    const newVariants = onChange.mock.calls[0][0];
    expect(newVariants.length).toBe(2);
  });

  it("prevents removing the last variant", () => {
    const onChange = vi.fn();
    render(
      <StrategyVariantsEditor
        strategies={STRATEGIES}
        variants={[
          { uiKey: "v1", strategyId: "baseline", params: {} },
        ]}
        onChange={onChange}
      />,
    );
    const deleteBtn = screen.getByTitle("删除变体");
    fireEvent.click(deleteBtn);
    // Should not trigger onChange (keep at least one)
    expect(onChange).not.toHaveBeenCalled();
  });

  it("removes a variant when more than one exists", () => {
    const onChange = vi.fn();
    render(
      <StrategyVariantsEditor
        strategies={STRATEGIES}
        variants={[
          { uiKey: "v1", strategyId: "baseline", params: {} },
          { uiKey: "v2", strategyId: "corr", params: {} },
        ]}
        onChange={onChange}
      />,
    );
    const deleteBtns = screen.getAllByTitle("删除变体");
    fireEvent.click(deleteBtns[0]);
    expect(onChange).toHaveBeenCalledTimes(1);
    const newVariants = onChange.mock.calls[0][0];
    expect(newVariants.length).toBe(1);
  });

  it("respects disabled prop", () => {
    render(
      <StrategyVariantsEditor
        strategies={STRATEGIES}
        variants={[
          { uiKey: "v1", strategyId: "baseline", params: { k: 8 } },
        ]}
        onChange={vi.fn()}
        disabled
      />,
    );
    const deleteBtn = screen.getByTitle("删除变体");
    expect(deleteBtn.hasAttribute("disabled")).toBe(true);
  });
});
