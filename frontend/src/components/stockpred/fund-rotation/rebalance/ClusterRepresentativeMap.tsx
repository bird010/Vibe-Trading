import type { CandidateDecisionRow, ClusterSnapshot } from "../types";
import { useState } from "react";

export function ClusterRepresentativeMap({ candidates, snapshot }: { candidates: CandidateDecisionRow[]; snapshot?: ClusterSnapshot | null }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const clusters = Array.from(new Set(candidates.map((candidate) => candidate.stages.cluster_id).filter((value): value is number => value != null))).sort((left, right) => left - right);
  return (
    <div className="space-y-2">
      <div className="grid gap-2 md:grid-cols-3">
        {clusters.map((clusterId) => {
          const members = candidates.filter((candidate) => candidate.stages.cluster_id === clusterId);
          const representative = members.find((candidate) => candidate.stages.cluster_representative);
          const otherMembers = members.filter((candidate) => candidate !== representative);
          const shownMembers = expanded.has(clusterId) ? otherMembers : otherMembers.slice(0, 2);
          const hiddenCount = otherMembers.length - shownMembers.length;
          return <div key={clusterId} className="rounded border p-2 text-xs"><div className="mb-1 font-medium">Cluster {clusterId}</div><div className="text-amber-700">{representative ? `★ ${representative.ts_code}` : "全成员等权"}</div><div className="mt-1 text-muted-foreground">成员 {members.length} 个</div>{shownMembers.map((candidate) => <div key={candidate.ts_code} className="mt-1 truncate text-muted-foreground">{candidate.ts_code}</div>)}{hiddenCount > 0 && <button type="button" className="mt-1 text-muted-foreground underline" onClick={() => setExpanded((current) => new Set(current).add(clusterId))}>+{hiddenCount} more</button>}{expanded.has(clusterId) && otherMembers.length > 2 && <button type="button" className="mt-1 ml-2 text-muted-foreground underline" onClick={() => setExpanded((current) => { const next = new Set(current); next.delete(clusterId); return next; })}>收起</button>}</div>;
        })}
      </div>
      {snapshot && <div className={`rounded border px-3 py-2 text-xs ${snapshot.overall === "REJECTED" || snapshot.overall === "REJECT" ? "border-amber-300 bg-amber-50 text-amber-800" : "bg-muted/10"}`}><span className="font-medium">Cluster Quality</span><span className="ml-3">{snapshot.overall || "—"}</span><span className="ml-3">Max Cluster Share：{snapshot.max_cluster_share == null ? "—" : `${(snapshot.max_cluster_share * 100).toFixed(1)}%`}{snapshot.max_cluster_share_reject_threshold == null ? "" : ` > reject ${(snapshot.max_cluster_share_reject_threshold * 100).toFixed(0)}%`}</span><span className="ml-3">Effective Clusters：{snapshot.effective_cluster_count == null ? "—" : snapshot.effective_cluster_count.toFixed(2)}{snapshot.effective_cluster_count_reject_threshold == null ? "" : ` < reject ${snapshot.effective_cluster_count_reject_threshold.toFixed(2)}`}</span></div>}
    </div>
  );
}
