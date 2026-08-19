/** Multi-variant strategy editor. */

import { Copy, Trash2, GripVertical } from "lucide-react";
import { StrategyConfigForm } from "./StrategyConfigForm";
import type { StrategyDetail } from "./types";

export interface VariantDraft {
  /** Temporary UI-only key; never sent as backend variant identity. */
  uiKey: string;
  strategyId: string;
  label: string;
  params: Record<string, unknown>;
}

interface Props {
  strategies: StrategyDetail[];
  variants: VariantDraft[];
  onChange: (variants: VariantDraft[]) => void;
  disabled?: boolean;
  onUnsupportedChange?: (uiKey: string, unsupported: string[]) => void;
}

export function createVariantUiKey(): string {
  return crypto.randomUUID();
}

export function StrategyVariantsEditor({
  strategies,
  variants,
  onChange,
  disabled = false,
  onUnsupportedChange,
}: Props) {
  const strategyMap = new Map(
    strategies.map((strategy) => [strategy.strategy_id, strategy]),
  );

  const addVariant = (strategyId: string): void => {
    const strategy = strategyMap.get(strategyId);
    if (!strategy) return;
    onChange([
      ...variants,
      {
        uiKey: createVariantUiKey(),
        strategyId,
        label: "",
        params: { ...strategy.default_config },
      },
    ]);
  };

  const updateVariant = (
    uiKey: string,
    patch: Partial<VariantDraft>,
  ): void => {
    onChange(
      variants.map((variant) =>
        variant.uiKey === uiKey ? { ...variant, ...patch } : variant,
      ),
    );
  };

  const copyVariant = (uiKey: string): void => {
    const source = variants.find((variant) => variant.uiKey === uiKey);
    if (!source) return;
    onChange([
      ...variants,
      {
        ...source,
        uiKey: createVariantUiKey(),
        label: source.label ? `${source.label} (副本)` : "副本",
        params: structuredClone(source.params),
      },
    ]);
  };

  const removeVariant = (uiKey: string): void => {
    if (variants.length <= 1) return;
    onUnsupportedChange?.(uiKey, []);
    onChange(variants.filter((variant) => variant.uiKey !== uiKey));
  };

  return (
    <div className="space-y-4">
      {variants.map((variant, index) => {
        const strategy = strategyMap.get(variant.strategyId);
        return (
          <div key={variant.uiKey} className="rounded-lg border p-4 space-y-3">
            <div className="flex items-center gap-2">
              <GripVertical className="h-4 w-4 text-muted-foreground shrink-0" />
              <select
                value={variant.strategyId}
                onChange={(event) => {
                  const strategyId = event.target.value;
                  const nextStrategy = strategyMap.get(strategyId);
                  onUnsupportedChange?.(variant.uiKey, []);
                  updateVariant(variant.uiKey, {
                    strategyId,
                    params: nextStrategy
                      ? { ...nextStrategy.default_config }
                      : {},
                  });
                }}
                disabled={disabled}
                className="flex-1 rounded border px-2 py-1.5 text-sm disabled:opacity-50"
              >
                {strategies.map((item) => (
                  <option key={item.strategy_id} value={item.strategy_id}>
                    {item.name}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={variant.label}
                onChange={(event) =>
                  updateVariant(variant.uiKey, { label: event.target.value })
                }
                placeholder="变体标签（可选）"
                disabled={disabled}
                className="w-32 rounded border px-2 py-1.5 text-sm disabled:opacity-50"
              />
              <button
                type="button"
                onClick={() => copyVariant(variant.uiKey)}
                disabled={disabled}
                className="rounded p-1.5 hover:bg-muted disabled:opacity-30"
                title="复制变体"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => removeVariant(variant.uiKey)}
                disabled={disabled || variants.length <= 1}
                className="rounded p-1.5 hover:bg-red-50 disabled:opacity-30"
                title="删除变体"
              >
                <Trash2 className="h-3.5 w-3.5 text-red-500" />
              </button>
            </div>

            {strategy ? (
              <StrategyConfigForm
                schema={strategy.config_schema}
                defaults={strategy.default_config}
                descriptions={strategy.parameter_descriptions}
                value={variant.params}
                onChange={(params) =>
                  updateVariant(variant.uiKey, { params })
                }
                onUnsupportedChange={(unsupported) =>
                  onUnsupportedChange?.(variant.uiKey, unsupported)
                }
                disabled={disabled}
              />
            ) : (
              <div className="text-xs text-muted-foreground">未选择策略</div>
            )}

            <div className="text-xs text-muted-foreground">
              变体 {index + 1} · {strategy?.strategy_id ?? "—"}
            </div>
          </div>
        );
      })}

      <div className="flex gap-2">
        <select
          value=""
          onChange={(event) => {
            if (event.target.value) {
              addVariant(event.target.value);
              event.target.value = "";
            }
          }}
          disabled={disabled}
          className="rounded border px-2 py-1.5 text-sm disabled:opacity-50"
        >
          <option value="" disabled>
            + 添加策略变体
          </option>
          {strategies.map((strategy) => (
            <option key={strategy.strategy_id} value={strategy.strategy_id}>
              {strategy.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
