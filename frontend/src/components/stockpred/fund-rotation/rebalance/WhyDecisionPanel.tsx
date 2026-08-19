import type { RebalanceDecisionResponse } from "../types";
import { ClusterRepresentativeMap } from "./ClusterRepresentativeMap";
import { RankingLane } from "./RankingLane";
import { StrategyPipeline } from "./StrategyPipeline";

interface Props {
  decision: RebalanceDecisionResponse;
  candidateView: "changed" | "top" | "all";
  onCandidateViewChange: (view: "changed" | "top" | "all") => void;
  onInstrumentClick: (tsCode: string) => void;
}

function topN(selectionRule?: string | null): number {
  const match = selectionRule?.match(/(\d+)/);
  return match ? Number(match[1]) : 3;
}

export function WhyDecisionPanel({ decision, candidateView, onCandidateViewChange, onInstrumentClick }: Props) {
  const candidates = decision.decision.candidates;
  const limit = decision.decision.strategy.top_n ?? topN(decision.decision.strategy.selection_rule);
  return (
    <section className="space-y-4 rounded border p-3 lg:col-span-2">
      <h4 className="text-sm font-medium">② 为什么这样调仓？</h4>
      <StrategyPipeline strategy={decision.decision.strategy} />
      {candidates.length === 0 ? <div className="rounded border bg-muted/10 px-3 py-4 text-xs text-muted-foreground">该历史运行未保存完整横截面排名证据。仍保留组合变化和执行信息。</div> : <><div><h5 className="mb-2 text-xs font-medium">Cluster Representatives</h5><ClusterRepresentativeMap candidates={candidates} snapshot={decision.decision.cluster_snapshot} /></div><div><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h5 className="text-xs font-medium">Strategy Score Ranking</h5><div className="flex gap-1">{(["changed", "top", "all"] as const).map((view) => <button key={view} type="button" onClick={() => onCandidateViewChange(view)} className={`rounded px-2 py-1 text-[11px] ${candidateView === view ? "bg-blue-100 text-blue-700" : "border text-muted-foreground"}`}>{view === "changed" ? "关键变化" : view === "top" ? "Top Candidates" : "全部"}</button>)}</div></div><RankingLane candidates={candidates} topN={limit} primaryMetric="Strategy Score" view={candidateView} onInstrumentClick={onInstrumentClick} /></div></>}
    </section>
  );
}
