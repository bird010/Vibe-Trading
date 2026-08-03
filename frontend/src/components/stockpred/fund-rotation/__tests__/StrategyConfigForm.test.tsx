/** Phase 5 Task 2 — StrategyConfigForm tests. */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StrategyConfigForm } from "../StrategyConfigForm";

const SIMPLE_SCHEMA = {
  type: "object",
  properties: {
    k: { type: "integer", description: "聚类数", minimum: 1, maximum: 50 },
    top_n: { type: "integer", description: "持仓个数", minimum: 1, maximum: 20, default: 3 },
    mode: {
      type: "string",
      enum: ["momentum", "mean_reversion"],
      description: "策略模式",
      default: "momentum",
    },
    enable_filter: {
      type: "boolean",
      description: "启用过滤",
      default: false,
    },
  },
  required: ["k"],
};

const DEFAULT_CONFIG = { k: 8, top_n: 3, mode: "momentum", enable_filter: false };

const DESCRIPTIONS: Record<string, string> = {
  k: "聚类数 K",
  top_n: "持仓个数 N",
  mode: "策略模式",
  enable_filter: "启用流动性过滤",
};

describe("StrategyConfigForm", () => {
  it("renders all supported fields", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("聚类数 K")).toBeDefined();
    expect(screen.getByText("持仓个数 N")).toBeDefined();
    expect(screen.getByText("策略模式")).toBeDefined();
    expect(screen.getByText("启用流动性过滤")).toBeDefined();
  });

  it("shows required marker for required fields", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    const kLabel = screen.getByText("聚类数 K");
    expect(kLabel.parentElement?.textContent).toContain("*");
  });

  it("falls back to schema description when no label mapping", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={{}}
        value={{}}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("聚类数")).toBeDefined();
  });

  it("renders enum as select dropdown", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    const select = screen.getByDisplayValue("momentum");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByText("mean_reversion")).toBeDefined();
  });

  it("renders boolean as select with true/false", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    const selects = screen.getAllByRole("combobox");
    expect(selects.length).toBe(2);
  });

  it("renders number input for integer fields", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    const inputs = screen.getAllByRole("spinbutton");
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });

  it("calls onChange when field value changes", () => {
    const onChange = vi.fn();
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={onChange}
      />,
    );
    const inputs = screen.getAllByRole("spinbutton");
    fireEvent.change(inputs[0], { target: { value: "12" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ k: 12 }));
  });

  it("initialises from default config", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={{ k: 5, top_n: 2 }}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    const inputs = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(inputs[0].placeholder).toBe("5");
  });

  it("uses explicit value over default", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{ k: 3 }}
        onChange={() => {}}
      />,
    );
    const inputs = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(inputs[0].getAttribute("value")).toBe("3");
  });

  it("shows unsupported warning for unknown schema types", () => {
    const schemaWithUnsupported = {
      type: "object",
      properties: {
        ...SIMPLE_SCHEMA.properties,
        array_field: { type: "array", description: "不支持的类型" },
        oneof_field: { oneOf: [{ type: "string" }, { type: "number" }] },
      },
    };
    render(
      <StrategyConfigForm
        schema={schemaWithUnsupported}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/当前客户端无法安全编辑以下配置项/)).toBeDefined();
    expect(screen.getByText(/array_field/)).toBeDefined();
    expect(screen.getByText(/oneof_field/)).toBeDefined();
  });

  it("respects disabled prop", () => {
    render(
      <StrategyConfigForm
        schema={SIMPLE_SCHEMA}
        defaults={DEFAULT_CONFIG}
        descriptions={DESCRIPTIONS}
        value={{}}
        onChange={() => {}}
        disabled
      />,
    );
    const inputs = screen.getAllByRole("spinbutton");
    for (const input of inputs) {
      expect(input.hasAttribute("disabled")).toBe(true);
    }
  });
});
