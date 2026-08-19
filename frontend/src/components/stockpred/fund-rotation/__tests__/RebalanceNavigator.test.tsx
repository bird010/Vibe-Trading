import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RebalanceNavigator } from "../rebalance/RebalanceNavigator";
import type { RebalanceIndexItem } from "../types";

const items: RebalanceIndexItem[] = [
  {
    signal_date: "20240103",
    sequence: 1,
    quality_status: "VALID",
    changed_positions: 1,
    target_count: 1,
    turnover: 0.5,
    target_changed_positions: 0,
    actual_changed_positions: 1,
    has_execution: true,
  },
  {
    signal_date: "20240110",
    sequence: 2,
    quality_status: "DEGRADED",
    changed_positions: 0,
    target_count: 1,
    turnover: 0,
    target_changed_positions: 1,
    actual_changed_positions: 0,
    has_execution: false,
  },
];

describe("RebalanceNavigator", () => {
  it("filters changed decisions and navigates to the next signal", () => {
    const onSelect = vi.fn();
    render(
      <RebalanceNavigator
        items={items}
        selectedSignalDate="20240103"
        filter="all"
        onFilterChange={vi.fn()}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByText("2024-01-03")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "下一次" }));
    expect(onSelect).toHaveBeenCalledWith("20240110");
  });

  it("separates target changes from actual rebalances", () => {
    render(
      <RebalanceNavigator
        items={items}
        selectedSignalDate="20240110"
        filter="target_changed"
        onFilterChange={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("2024-01-10")).toBeDefined();
    expect(screen.getByText("目标变化")).toBeDefined();
  });
});
