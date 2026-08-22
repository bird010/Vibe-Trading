import { useEffect, useMemo, useRef, useState } from "react";
import {
  Play,
  Loader2,
  AlertTriangle,
  TrendingUp,
} from "lucide-react";
import { useFundRotation } from "./useFundRotation";
import { useBacktestDetail } from "./useBacktestDetail";
import {
  StrategyVariantsEditor,
  createVariantUiKey,
  type VariantDraft,
} from "./StrategyVariantsEditor";
import { StrategyComparison } from "./StrategyComparison";
import { VariantRunsTable } from "./VariantRunsTable";
import { BacktestDetailPanel } from "./BacktestDetailPanel";
import type { StrategyDetail } from "./types";
import { fetchBatchDetail } from "./api";
import { readFundRotationUrl } from "./deepLinks";

const RESEARCH_WARNING = "RESEARCH_ONLY · 仅供研究，不构成投资建议";
const DEFAULT_STRATEGY_ID = "ai_rotation_r34_staged_reentry";
const FALLBACK_STRATEGY_ID = "ai_rotation_r11_persist_geom";
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
    comparisonEquity,
    loading,
    error,
    events,
    fetchCatalog,
    fetchBatches,
    submitStrategyBatch,
    selectBatch,
  } = useFundRotation();
  const {
    selectedVariantKey,
    selectedRunId,
    openRun,
    closeRun,
  } = useBacktestDetail();

  const [variants, setVariants] = useState<VariantDraft[]>([]);
  const [startDate, setStartDate] = useState("2022-08-01");
  const [endDate, setEndDate] = useState("2026-08-01");
  const [initialCapital, setInitialCapital] = useState(1_000_000);
  const [unsupportedByVariant, setUnsupportedByVariant] = useState<
    Record<string, string[]>
  >({});
  const idempotencyRef = useRef<{
    key: string;
    payloadSignature: string;
  } | null>(null);
  const deepLinkRunId = readFundRotationUrl().runId;
  const deepLinkRestoreStarted = useRef(false);

  useEffect(() => {
    void fetchCatalog();
    void fetchBatches();
  }, [fetchCatalog, fetchBatches]);

  useEffect(() => {
    closeRun();
  }, [activeBatchId, closeRun]);

  useEffect(() => {
    if (strategies.length > 0 && variants.length === 0) {
      const strategy =
        strategies.find(
          (item) => item.strategy_id === DEFAULT_STRATEGY_ID,
        ) ??
        strategies.find(
          (item) => item.strategy_id === FALLBACK_STRATEGY_ID,
        ) ?? strategies[0];
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

  useEffect(() => {
    if (!deepLinkRunId || deepLinkRestoreStarted.current || selectedRunId || batches.length === 0) return;
    deepLinkRestoreStarted.current = true;
    let cancelled = false;
    void Promise.all(
      batches.map(async (batch) => {
        try {
          return { batchId: batch.batch_id, detail: await fetchBatchDetail(batch.batch_id) };
        } catch {
          return null;
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      const match = results.find((result) => result?.detail.child_runs.some((child) => child.run_id === deepLinkRunId));
      if (match) void selectBatch(match.batchId);
    });
    return () => {
      cancelled = true;
    };
  }, [batches, deepLinkRunId, selectBatch, selectedRunId]);

  useEffect(() => {
    if (!deepLinkRunId || selectedRunId || !activeBatch) return;
    const child = activeBatch.child_runs.find((entry) => entry.run_id === deepLinkRunId);
    if (child) void openRun(child.variant_key, child.run_id);
  }, [activeBatch, deepLinkRunId, openRun, selectedRunId]);

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

  const handleSelectVariant = (variantKey: string): void => {
    if (!activeBatch) return;
    const variant = activeBatch.resolved.variants.find(
      (entry) => entry.variant_key === variantKey,
    );
    const child = activeBatch.child_runs.find(
      (entry) => entry.variant_key === variantKey,
    );
    const runId = child?.run_id ?? variant?.run_id;
    if (runId) void openRun(variantKey, runId);
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

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,30fr)_minmax(0,25fr)_minmax(0,45fr)]">
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
              onChange={setVariants}
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

        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm">历史批次</h3>
          {batches.length > 0 ? (
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
          ) : (
            <div className="text-xs text-muted-foreground">
              暂无批次
            </div>
          )}
        </div>

        <div className="space-y-4 rounded-lg border p-4 min-w-0">
          <h3 className="font-semibold text-sm flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            策略比较
          </h3>
          {comparison ? (
            <StrategyComparison
              reports={comparison}
              equity={comparisonEquity}
              onSelectVariant={handleSelectVariant}
            />
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

      {activeBatch && (
        <VariantRunsTable
          batch={activeBatch}
          reports={comparison}
          selectedVariantKey={selectedVariantKey}
          onViewDetail={(variantKey, runId) => void openRun(variantKey, runId)}
        />
      )}

      <BacktestDetailPanel />
    </div>
  );
}
