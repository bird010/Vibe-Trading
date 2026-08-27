import { describe, expect, it } from "vitest";
import { normalizeStrategyName } from "../strategyDisplay";

describe("normalizeStrategyName", () => {
  it("uses the strategy id code instead of a parent strategy prefix", () => {
    expect(
      normalizeStrategyName(
        "ai_rotation_r64_direct_corr_diversification",
        "R59 信号直接相关性约束 ETF 轮动",
      ),
    ).toBe("R64 信号直接相关性约束 ETF 轮动");
  });

  it("adds the canonical code when the legacy name has no code", () => {
    expect(
      normalizeStrategyName("ai_rotation_r05_mom_persist", "持续动量相关性代表ETF"),
    ).toBe("R05 持续动量相关性代表ETF");
  });
});
