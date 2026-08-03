import { useEffect, useMemo, useRef, useState } from "react";
import {
  Play,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  XCircle,
} from "lucide-react";
import { useFundRotation } from "./useFundRotation";
import {
  StrategyVariantsEditor,
  createVariantUiKey,
  type VariantDraft,
} from "./StrategyVariantsEditor";
import { StrategyComparison } from "./StrategyComparison";
import type { StrategyDetail } from "./types";

const RESEARCH_WARNING = "RESEARCH_ONLY · 仅供研究，不构成投资建议";
const TERMINAL_STAGES = new Set([
  "SUCCEEDED",
  "PARTIAL_SUCCEEDED",
  "FAILED",
  "CANCELED",
  "FAILED_INTERRUPTED",
]);
const BATCH_STAGE_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  VALIDATING: "校验中",
  SNAPSHOTTING_DATA: "快照数据",
  RUNNING_STRATEGIES: "运行策略",
  COMPARING: "比较中",
  WRITING_RESULTS: "写入结果",
  SUCCEEDED: "完成",
  PARTIAL_SUCCEEDED: "部分完成",
  FAILED: "失败",
  CANCELED: "已取消",
  FAILED_INTERRUPTED: "中断",
};

export function FundRotationTab() {
  const {
    strategies,
    strategyDetails,
    catalogLoading,
    catalogError,
    batches,
    activeBatch,
    activeBatchId,
    comparison,
    loading,
    error,
    events,
    fetchCatalog,
    fetchBatches,
    submitStrategyBatch,
    selectBatch,
    cancelActiveBatch,
  } = useFundRotation();

  const [variants, setVariants] = useState<VariantDraft[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [initialCapital, setInitialCapital] = useState(1_000_000);
  const [unsupportedByVariant, setUnsupportedByVariant] = useState<
    Record<string, string[]>
  >({});
  const idempotencyRef = useRef<{
    key: string;
    payloadSignature: string;
  } | null>(null);

  useEffect(() => {
    void fetchCatalog();
    void fetchBatches();
  }, [fetchCatalog, fetchBatches]);

  useEffect(() => {
    if (strategies.length > 0 && variants.length === 0) {
      const strategy = strategies[0];
      setVariants([
        {
          uiKey: createVariantUiKey(),
          strategyId: strategy.strategy_id,
          label: "",
          params: {
            ...((strategyDetails.get(strategy.strategy_id)?.default_config) ?? {}),
          },
        },
      ]);
    }
  }, [strategies, strategyDetails, variants.length]);

  const detailMap = useMemo(
    () =>
      new Map(
        strategies.map((strategy) => [
          strategy.strategy_id,
          strategyDetails.get(strategy.strategy_id) ?? null,
        ]),
      ),
    [strategies, strategyDetails],
  );
  const availableStrategyDetails = useMemo(
    () =>
      Array.from(detailMap.values()).filter(
        (detail): detail is StrategyDetail => detail !== null,
      ),
    [detailMap],
  );

  const payloadSignature = useMemo(
    () =>
      JSON.stringify({
        variants: variants.map(({ strategyId, label, params }) => ({
          strategyId,
          label,
          params,
        })),
        startDate,
        endDate,
        initialCapital,
      }),
    [variants, startDate, endDate, initialCapital],
  );

  const hasUnsupportedFields = Object.values(unsupportedByVariant).some(
    (items) => items.length > 0,
  );
  const missingStrategyDetail = variants.some(
    (variant) => !strategyDetails.has(variant.strategyId),
  );

  const handleSubmit = async (): Promise<void> => {
    const existingIntent = idempotencyRef.current;
    const intent =
      existingIntent?.payloadSignature === payloadSignature
        ? existingIntent
        : { key: crypto.randomUUID(), payloadSignature };
    idempotencyRef.current = intent;
    try {
      const result = await submitStrategyBatch(
        variants,
        startDate.replace(/-/g, ""),
        endDate.replace(/-/g, ""),
        { initial_capital: initialCapital },
        intent.key,
      );
      idempotencyRef.current = null;
      if (result.batch_id) await selectBatch(result.batch_id);
    } catch {
      // Keep the same key so an uncertain network outcome can be retried safely.
    }
  };

  const latestBatchStage = [...events]
    .reverse()
    .find((event) => event.scope === "BATCH" && event.stage)?.stage;
  const currentStage = latestBatchStage ?? activeBatch?.state?.stage ?? null;
  const isTerminal = currentStage ? TERMINAL_STAGES.has(currentStage) : false;

  const submitDisabled =
    loading ||
    variants.length === 0 ||
    !startDate ||
    !endDate ||
    catalogLoading ||
    hasUnsupportedFields ||
    missingStrategyDetail;

  return (
    <div className="space-y-6">
      <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        {RESEARCH_WARNING}
      </div>

      {catalogError && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="inline h-4 w-4 mr-1" />
          策略目录加载异常：{catalogError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm">策略批次配置</h3>
          <div className="grid grid-cols-3 gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">开始日期</span>
              <input
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
                disabled={loading}
                className="rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">结束日期</span>
              <input
                type="date"
                value={endDate}
                onChange={(event) => setEndDate(event.target.value)}
                disabled={loading}
                className="rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">初始资金</span>
              <input
                type="number"
                min={1}
                value={initialCapital}
                onChange={(event) => setInitialCapital(Number(event.target.value))}
                disabled={loading}
                className="rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
            </label>
          </div>

          {catalogLoading ? (
            <div className="text-xs text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-3 w-3 animate-spin" />
              加载策略目录…
            </div>
          ) : (
            <StrategyVariantsEditor
              strategies={availableStrategyDetails}
              variants={variants}
              onChange={(next) => {
                setVariants(next);
                if (idempotencyRef.current?.payloadSignature !== payloadSignature) {
                  idempotencyRef.current = null;
                }
              }}
              onUnsupportedChange={(uiKey, unsupported) =>
                setUnsupportedByVariant((current) => ({
                  ...current,
                  [uiKey]: unsupported,
                }))
              }
              disabled={loading}
            />
          )}

          <button
            onClick={() => void handleSubmit()}
            disabled={submitDisabled}
            className="w-full rounded bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                提交中…
              </>
            ) : (
              <>
                <Play className="h-4 w-4" />
                提交策略批次
              </>
            )}
          </button>

          {hasUnsupportedFields && (
            <div className="text-xs text-red-600">
              当前策略包含客户端不支持的配置字段，无法提交。
            </div>
          )}
          {error && (
            <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </div>
          )}

          {batches.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-1">
                历史批次
              </h4>
              <div className="max-h-36 overflow-y-auto space-y-0.5">
                {batches.map((batch) => (
                  <button
                    key={batch.batch_id}
                    onClick={() => void selectBatch(batch.batch_id)}
                    className={`w-full text-left text-xs px-2 py-1 rounded flex justify-between ${
                      batch.batch_id === activeBatchId
                        ? "bg-blue-50 text-blue-700"
                        : "hover:bg-muted"
                    }`}
                  >
                    <span className="font-mono">
                      {batch.batch_id.slice(0, 12)}…
                    </span>
                    <span>{BATCH_STAGE_LABELS[batch.status] ?? batch.status}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm flex items-center gap-2">
            批次进度
            {!isTerminal && activeBatchId && (
              <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
            )}
            {currentStage && (
              <span className="text-xs font-normal text-muted-foreground">
                {BATCH_STAGE_LABELS[currentStage] ?? currentStage}
              </span>
            )}
          </h3>

          {currentStage && isTerminal && (
            <div className="flex items-center gap-2 text-xs">
              {currentStage === "SUCCEEDED" ||
              currentStage === "PARTIAL_SUCCEEDED" ? (
                <CheckCircle2 className="h-4 w-4 text-green-500" />
              ) : currentStage === "CANCELED" ? (
                <XCircle className="h-4 w-4 text-gray-400" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-red-500" />
              )}
              <span>{BATCH_STAGE_LABELS[currentStage] ?? currentStage}</span>
            </div>
          )}

          {!isTerminal && activeBatchId && (
            <button
              onClick={() => void cancelActiveBatch()}
              className="rounded border border-red-300 px-2 py-1 text-xs text-red-600 hover:bg-red-50"
            >
              取消批次
            </button>
          )}

          {events.length > 0 && (
            <div className="max-h-64 overflow-y-auto space-y-0.5 text-xs">
              {events.slice(-20).map((event) => (
                <div key={event.seq} className="flex gap-2 border-b py-0.5">
                  <span className="text-muted-foreground font-mono shrink-0">
                    #{event.seq}
                  </span>
                  <span className="text-muted-foreground">
                    {BATCH_STAGE_LABELS[event.stage ?? ""] ?? event.event_type}
                  </span>
                  {event.variant_key && (
                    <span className="font-mono text-muted-foreground">
                      {event.variant_key}
                    </span>
                  )}
                  {event.message && <span className="truncate">{event.message}</span>}
                </div>
              ))}
            </div>
          )}

          {!activeBatchId && events.length === 0 && (
            <div className="text-xs text-muted-foreground">
              暂无批次 — 提交策略变体后开始
            </div>
          )}
        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            策略比较
          </h3>
          {comparison ? (
            <StrategyComparison reports={comparison} />
          ) : activeBatch ? (
            <div className="text-xs text-muted-foreground">
              {isTerminal &&
              currentStage !== "SUCCEEDED" &&
              currentStage !== "PARTIAL_SUCCEEDED"
                ? "批次未成功完成，无比较结果"
                : "批次完成后将显示比较结果"}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">
              提交批次后查看策略比较结果
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
