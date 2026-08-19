import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useRotationAnalysis } from "./useRotationAnalysis";
import { HoldingsWeightTimeline } from "./holdings/HoldingsWeightTimeline";
import { RebalanceNavigator } from "./rebalance/RebalanceNavigator";
import { PortfolioChangeChart } from "./rebalance/PortfolioChangeChart";
import { WhyDecisionPanel } from "./rebalance/WhyDecisionPanel";
import { ExecutionSummary } from "./rebalance/ExecutionSummary";
import { syncFundRotationUrl } from "./deepLinks";
import { useBacktestDetail } from "./useBacktestDetail";

export function RotationAnalysisTab({ runId }: { runId: string }) {
  const {
    timeline,
    rebalanceIndex,
    loading,
    error,
    rebalanceDetails,
    selectedSignalDate,
    timelineWindow,
    candidateView,
    setCandidateView,
    decisionLoading,
    decisionErrors,
    selectSignalDate,
    setTimelineWindow,
    openRun,
  } = useRotationAnalysis();
  const { selectTab, selectInstrument } = useBacktestDetail();
  const [rebalanceFilter, setRebalanceFilter] = useState<"changed" | "target_changed" | "all" | "cash" | "degraded" | "rejected">("changed");
  const selectedDecision = selectedSignalDate ? rebalanceDetails[selectedSignalDate] : undefined;

  useEffect(() => {
    if (runId) void openRun(runId);
  }, [openRun, runId]);

  const handleSignalDate = (signalDate: string) => {
    syncFundRotationUrl({ runId, tab: "rotation_analysis", signalDate }, "push");
    void selectSignalDate(signalDate);
  };

  const openInstrumentChart = (tsCode: string) => {
    if (tsCode === "_CASH") return;
    syncFundRotationUrl({
      runId,
      tab: "chart",
      instrument: tsCode,
      focusDate: selectedSignalDate,
      strategyScore: "primary_score",
    }, "push");
    selectTab("chart");
    void selectInstrument(tsCode);
  };

  return (
    <section className="space-y-4" data-testid="rotation-analysis-tab">
      <div>
        <h3 className="text-sm font-semibold">轮动分析</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          以 signal date 为中心查看持仓迁移、调仓原因与执行结果。
        </p>
      </div>

      {loading && (
        <div className="flex items-center gap-2 rounded border bg-muted/20 px-3 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载持仓时间线与调仓索引…
        </div>
      )}
      {error && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {error}
        </div>
      )}

      <section className="space-y-2 rounded border p-3">
        <h4 className="text-sm font-medium">持仓与权重变化</h4>
        {!loading && !timeline && (
          <p className="text-xs text-muted-foreground">
            该历史运行未保存完整实际持仓时间序列，无法生成持仓权重图。
          </p>
        )}
        {timeline && (
          <HoldingsWeightTimeline
            data={timeline}
            selectedSignalDate={selectedSignalDate}
            window={timelineWindow}
            onWindowChange={setTimelineWindow}
            onSelectSignalDate={handleSignalDate}
          />
        )}
      </section>

      <section className="space-y-2 rounded border p-3">
        <h4 className="text-sm font-medium">调仓决策</h4>
        {!loading && !rebalanceIndex && (
          <p className="text-xs text-muted-foreground">
            该历史运行未保存调仓导航索引。
          </p>
        )}
        {rebalanceIndex && (
          <RebalanceNavigator
            items={rebalanceIndex.items}
            selectedSignalDate={selectedSignalDate}
            filter={rebalanceFilter}
            onFilterChange={setRebalanceFilter}
            onSelect={handleSignalDate}
          />
        )}
        {selectedSignalDate && (
          <div className="space-y-4">
            {decisionLoading && <div className="rounded border bg-muted/10 px-3 py-4 text-xs text-muted-foreground">加载调仓决策证据…</div>}
            {decisionErrors[selectedSignalDate] && <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">{decisionErrors[selectedSignalDate]}</div>}
            {selectedDecision && <div className="grid gap-4 lg:grid-cols-4"><PortfolioChangeChart before={selectedDecision.before} afterTarget={selectedDecision.after_target} onInstrumentClick={openInstrumentChart} /><WhyDecisionPanel decision={selectedDecision} candidateView={candidateView} onCandidateViewChange={setCandidateView} onInstrumentClick={openInstrumentChart} /><ExecutionSummary execution={selectedDecision.execution} before={selectedDecision.before} afterTarget={selectedDecision.after_target} /></div>}
          </div>
        )}
      </section>
    </section>
  );
}
