import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VariantRunsTable } from "../VariantRunsTable";
import type { BatchDetail, ComparisonReports } from "../types";

const BATCH: BatchDetail = {
  batch_id: "batch-1",
  state: {
    schema_version: "2",
    stage: "PARTIAL_SUCCEEDED",
    batch_id: "batch-1",
    mode: "RESEARCH_ONLY",
  },
  resolved: {
    batch_id: "batch-1",
    schema_version: "1",
    mode: "RESEARCH_ONLY",
    catalog_version: "catalog",
    framework_implementation_hash: "framework",
    variants: [
      {
        variant_key: "variant-ok",
        strategy_id: "correlation_representative",
        implementation_hash: "strategy",
        resolved_config_hash: "config",
        run_id: "run-success",
        data_start: "20230101",
        decision_start_date: "20231229",
      },
      {
        variant_key: "variant-failed",
        strategy_id: "correlation_all_members",
        implementation_hash: "strategy-2",
        resolved_config_hash: "config-2",
        run_id: "run-failed",
        data_start: "20230201",
        decision_start_date: "20231222",
      },
    ],
    plan: {
      data_start: "20230101",
      earliest_decision_start_date: "20231222",
      evaluation_start_date: "20240102",
      evaluation_end_date: "20241231",
      variants: [],
    },
    executed_order: [],
  },
  child_runs: [
    {
      schema_version: "2",
      stage: "SUCCEEDED",
      batch_id: "batch-1",
      run_id: "run-success",
      variant_key: "variant-ok",
      strategy_id: "correlation_representative",
      mode: "RESEARCH_ONLY",
      quality_status: "VALID",
    },
    {
      schema_version: "2",
      stage: "FAILED",
      batch_id: "batch-1",
      run_id: "run-failed",
      variant_key: "variant-failed",
      strategy_id: "correlation_all_members",
      mode: "RESEARCH_ONLY",
      error: "strategy failed",
    },
  ],
  mode: "RESEARCH_ONLY",
};

const REPORTS: ComparisonReports = {
  comparison_available: false,
  comparable_variant_count: 1,
  contract: { fingerprint: "fingerprint", components: {} },
  ranking: [
    {
      rank: 1,
      variant_key: "variant-ok",
      strategy_id: "correlation_representative",
      run_id: "run-success",
      quality_status: "VALID",
      annual_return: 0.1,
    },
  ],
  excluded: [{ variant_key: "variant-failed", reason: "technical_failure" }],
  quality_warnings: [],
};

describe("VariantRunsTable", () => {
  it("shows successful and failed variants with explicit detail actions", () => {
    const onViewDetail = vi.fn();
    render(
      <VariantRunsTable
        batch={BATCH}
        reports={REPORTS}
        onViewDetail={onViewDetail}
      />,
    );

    expect(screen.queryByText("标签")).toBeNull();
    expect(screen.getByText("排除：technical_failure")).toBeDefined();
    expect(screen.getByText("查看详情")).toBeDefined();
    expect(screen.getByText("查看错误")).toBeDefined();

    fireEvent.click(screen.getByText("查看详情"));
    expect(onViewDetail).toHaveBeenCalledWith("variant-ok", "run-success");

    fireEvent.click(screen.getByText("查看错误"));
    expect(onViewDetail).toHaveBeenCalledWith("variant-failed", "run-failed");
  });
});
