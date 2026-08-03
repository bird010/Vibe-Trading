import { useEffect, useRef, useState } from "react";
import { Play, Loader2, AlertTriangle, CheckCircle2, TrendingUp, XCircle } from "lucide-react";
import { useFundRotation } from "./useFundRotation";
import { StrategyVariantsEditor, type VariantDraft } from "./StrategyVariantsEditor";
import { StrategyComparison } from "./StrategyComparison";


const RESEARCH_WARNING = "RESEARCH_ONLY · 仅供研究，不构成投资建议";

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
    connectBatchSSE,
    disconnectSSE,
  } = useFundRotation();

  const [variants, setVariants] = useState<VariantDraft[]>(() => {
    const defaultStrat = strategies[0];
    if (!defaultStrat) return [];
    return [
      {
        uiKey: "v1",
        strategyId: defaultStrat.strategy_id,
        label: "",
        params: {},
      },
    ];
  });

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [initialCapital, setInitialCapital] = useState(1_000_000);
  const idempotencyRef = useRef<string | null>(null);

  useEffect(() => {
    fetchCatalog();
    fetchBatches();
  }, [fetchCatalog, fetchBatches]);

  // Initialize variant when catalog loads
  useEffect(() => {
    if (strategies.length > 0 && variants.length === 0) {
      const s = strategies[0];
      setVariants([
        {
          uiKey: "v1",
          strategyId: s.strategy_id,
          label: "",
          params: { ...((strategyDetails.get(s.strategy_id)?.default_config) ?? {}) },
        },
      ]);
    }
  }, [strategies, strategyDetails, variants.length]);

  const detailMap = new Map(strategies.map((s) => [s.strategy_id, strategyDetails.get(s.strategy_id) ?? null]));

  const handleSubmit = async () => {
    idempotencyRef.current = crypto.randomUUID();
    try {
      const result = await submitStrategyBatch(
        variants,
        startDate.replace(/-/g, ""),
        endDate.replace(/-/g, ""),
        { initial_capital: initialCapital },
        idempotencyRef.current,
      );
      if (result.batch_id) {
        selectBatch(result.batch_id);
        connectBatchSSE(result.batch_id);
      }
    } catch {
      // error set in store
    }
  };

  const currentStage = activeBatch?.state?.stage ?? events[events.length - 1]?.stage as string ?? null;
  const isTerminal = currentStage && ["SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED", "CANCELED", "FAILED_INTERRUPTED"].includes(currentStage);

  return (
    <div className="space-y-6">
      {/* Research warning */}
      <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        {RESEARCH_WARNING}
      </div>

      {/* Catalog error */}
      {catalogError && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="inline h-4 w-4 mr-1" />
          策略目录加载失败：{catalogError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left: Common params + variants */}
        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm">策略批次配置</h3>

          {/* Common params */}
          <div className="grid grid-cols-3 gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">开始日期</span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                disabled={loading}
                className="rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">结束日期</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                disabled={loading}
                className="rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">初始资金</span>
              <input
                type="number"
                value={initialCapital}
                onChange={(e) => setInitialCapital(Number(e.target.value))}
                disabled={loading}
                className="rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
            </label>
          </div>

          {/* Variants editor */}
          {catalogLoading ? (
            <div className="text-xs text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-3 w-3 animate-spin" />
              加载策略目录…
            </div>
          ) : (
            <StrategyVariantsEditor
              strategies={
                Array.from(detailMap.entries())
                  .filter(([, d]) => d !== null)
                  .map(([, d]) => d!)
              }
              variants={variants}
              onChange={setVariants}
              disabled={loading}
            />
          )}

          {/* Submit */}
          <button
            onClick={handleSubmit}
            disabled={loading || variants.length === 0 || !startDate || !endDate || catalogLoading}
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

          {error && (
            <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </div>
          )}

          {/* Batch history */}
          {batches.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-muted-foreground mb-1">
                历史批次
              </h4>
              <div className="max-h-36 overflow-y-auto space-y-0.5">
                {batches.map((b) => (
                  <button
                    key={b.batch_id}
                    onClick={() => {
                      selectBatch(b.batch_id);
                      disconnectSSE();
                    }}
                    className={`w-full text-left text-xs px-2 py-1 rounded flex justify-between ${
                      b.batch_id === activeBatchId
                        ? "bg-blue-50 text-blue-700"
                        : "hover:bg-muted"
                    }`}
                  >
                    <span className="font-mono">{b.batch_id.slice(0, 12)}…</span>
                    <span>{BATCH_STAGE_LABELS[b.status] ?? b.status}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Center: Progress + events */}
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
              {currentStage === "SUCCEEDED" || currentStage === "PARTIAL_SUCCEEDED" ? (
                <CheckCircle2 className="h-4 w-4 text-green-500" />
              ) : currentStage === "CANCELED" ? (
                <XCircle className="h-4 w-4 text-gray-400" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-red-500" />
              )}
              <span>
                {BATCH_STAGE_LABELS[currentStage] ?? currentStage}
              </span>
            </div>
          )}

          {/* Cancel button */}
          {!isTerminal && activeBatchId && (
            <button
              onClick={() => cancelActiveBatch()}
              className="rounded border border-red-300 px-2 py-1 text-xs text-red-600 hover:bg-red-50"
            >
              取消批次
            </button>
          )}

          {/* Event log */}
          {events.length > 0 && (
            <div className="max-h-64 overflow-y-auto space-y-0.5 text-xs">
              {events.slice(-20).map((e, i) => (
                <div key={i} className="flex gap-2 border-b py-0.5">
                  <span className="text-muted-foreground font-mono shrink-0">
                    #{e.seq}
                  </span>
                  <span className="text-muted-foreground">
                    {BATCH_STAGE_LABELS[e.stage ?? ""] ?? e.event_type}
                  </span>
                  {e.variant_key && (
                    <span className="font-mono text-muted-foreground">
                      {e.variant_key}
                    </span>
                  )}
                  {e.message && (
                    <span className="truncate">{e.message}</span>
                  )}
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

        {/* Right: Comparison / results */}
        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            策略比较
          </h3>

          {comparison ? (
            <StrategyComparison reports={comparison} />
          ) : activeBatch ? (
            <div className="text-xs text-muted-foreground">
              {isTerminal && currentStage !== "SUCCEEDED" && currentStage !== "PARTIAL_SUCCEEDED"
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
