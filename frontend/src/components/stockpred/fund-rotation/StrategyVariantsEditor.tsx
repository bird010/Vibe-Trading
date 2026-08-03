/** Phase 5 Task 3 — multi-variant strategy editor (§21). */

import { Copy, Trash2, GripVertical } from "lucide-react";
import { StrategyConfigForm } from "./StrategyConfigForm";
import type { StrategyDetail } from "./types";

export interface VariantDraft {
  /** Temporary UI-only key (not the backend variant_key). */
  uiKey: string;
  strategyId: string;
  label: string;
  params: Record<string, unknown>;
}

interface Props {
  /** All available strategies from Catalog. */
  strategies: StrategyDetail[];
  /** Variants being edited. */
  variants: VariantDraft[];
  /** Callback with full variant list on any change. */
  onChange: (variants: VariantDraft[]) => void;
  disabled?: boolean;
}

let _keyCounter = 0;
function nextKey(): string {
  _keyCounter += 1;
  return `v${_keyCounter}`;
}

export function StrategyVariantsEditor({
  strategies,
  variants,
  onChange,
  disabled = false,
}: Props) {
  const strategyMap = new Map(strategies.map((s) => [s.strategy_id, s]));

  const addVariant = (strategyId: string) => {
    const strategy = strategyMap.get(strategyId);
    if (!strategy) return;
    onChange([
      ...variants,
      {
        uiKey: nextKey(),
        strategyId,
        label: "",
        params: { ...strategy.default_config },
      },
    ]);
  };

  const updateVariant = (uiKey: string, patch: Partial<VariantDraft>) => {
    onChange(variants.map((v) => (v.uiKey === uiKey ? { ...v, ...patch } : v)));
  };

  const copyVariant = (uiKey: string) => {
    const source = variants.find((v) => v.uiKey === uiKey);
    if (!source) return;
    onChange([
      ...variants,
      { ...source, uiKey: nextKey(), label: `${source.label} (副本)` },
    ]);
  };

  const removeVariant = (uiKey: string) => {
    if (variants.length <= 1) return; // keep at least one
    onChange(variants.filter((v) => v.uiKey !== uiKey));
  };

  return (
    <div className="space-y-4">
      {/* Variant cards */}
      {variants.map((variant, idx) => {
        const strategy = strategyMap.get(variant.strategyId);
        return (
          <div
            key={variant.uiKey}
            className="rounded-lg border p-4 space-y-3"
          >
            {/* Header: strategy selector + actions */}
            <div className="flex items-center gap-2">
              <GripVertical className="h-4 w-4 text-muted-foreground shrink-0" />
              <select
                value={variant.strategyId}
                onChange={(e) => {
                  const newId = e.target.value;
                  const newStrategy = strategyMap.get(newId);
                  updateVariant(variant.uiKey, {
                    strategyId: newId,
                    params: newStrategy
                      ? { ...newStrategy.default_config }
                      : {},
                  });
                }}
                disabled={disabled}
                className="flex-1 rounded border px-2 py-1.5 text-sm disabled:opacity-50"
              >
                {strategies.map((s) => (
                  <option key={s.strategy_id} value={s.strategy_id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={variant.label}
                onChange={(e) =>
                  updateVariant(variant.uiKey, { label: e.target.value })
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

            {/* Per-variant strategy params */}
            {strategy ? (
              <StrategyConfigForm
                schema={strategy.config_schema}
                defaults={strategy.default_config}
                descriptions={strategy.parameter_descriptions}
                value={variant.params}
                onChange={(params) => updateVariant(variant.uiKey, { params })}
                disabled={disabled}
              />
            ) : (
              <div className="text-xs text-muted-foreground">
                未选择策略
              </div>
            )}

            {/* Variant number */}
            <div className="text-xs text-muted-foreground">
              变体 {idx + 1} · {strategy?.strategy_id ?? "—"}
            </div>
          </div>
        );
      })}

      {/* Add variant button */}
      <div className="flex gap-2">
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) {
              addVariant(e.target.value);
              e.target.value = "";
            }
          }}
          disabled={disabled}
          className="rounded border px-2 py-1.5 text-sm disabled:opacity-50"
        >
          <option value="" disabled>
            + 添加策略变体
          </option>
          {strategies.map((s) => (
            <option key={s.strategy_id} value={s.strategy_id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
