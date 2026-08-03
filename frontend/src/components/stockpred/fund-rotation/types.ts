/** Phase 5 Task 1 — fund rotation batch domain types (§18/§21). */

// ── Catalog types (§16) ──

export interface StrategySummary {
  strategy_id: string;
  name: string;
  description: string;
  interface_version: string;
  implementation_hash: string;
  supported_universe: string[];
  warmup_trade_days: number;
  required_datasets: string[];
  required_fields: string[];
  frequency: string;
}

export interface StrategyDetail extends StrategySummary {
  config_schema: Record<string, unknown>;
  config_schema_version: string;
  config_schema_hash: string;
  default_config: Record<string, unknown>;
  parameter_descriptions: Record<string, string>;
  artifact_roles: string[];
}

export interface CatalogListResponse {
  catalog_version: string;
  strategies: StrategySummary[];
  mode: "RESEARCH_ONLY";
}

// ── Batch request (§21) ──

export interface BatchVariantRequest {
  strategy_id: string;
  label?: string;
  params: Record<string, unknown>;
}

export interface StrategyBatchRequest {
  schema_version: string;
  idempotency_key: string;
  mode: "RESEARCH_ONLY";
  evaluation_start_date: string;
  evaluation_end_date: string;
  execution: {
    initial_capital?: number;
    commission_rate?: number;
    commission_min?: number;
    other_fee_rate?: number;
    max_participation_rate?: number;
    adv_lookback?: number;
    adv_min_observations?: number;
    base_slippage_bps?: number;
    max_slippage_bps?: number;
  };
  variants: BatchVariantRequest[];
}

export interface BatchSubmitResponse {
  batch_id: string;
  status: "QUEUED" | "EXISTING";
}

// ── Batch / child states (§26/§30) ──

export type BatchStage =
  | "QUEUED"
  | "VALIDATING"
  | "SNAPSHOTTING_DATA"
  | "RUNNING_STRATEGIES"
  | "COMPARING"
  | "WRITING_RESULTS"
  | "SUCCEEDED"
  | "PARTIAL_SUCCEEDED"
  | "FAILED"
  | "CANCELED"
  | "FAILED_INTERRUPTED";

export type ChildStage =
  | "QUEUED"
  | "PREPARING_DATA"
  | "GENERATING_SIGNALS"
  | "EXECUTING"
  | "COMPUTING_METRICS"
  | "WRITING_RESULTS"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELED"
  | "FAILED_INTERRUPTED";

// ── Event envelope (§30.1) ──

export type EventScope = "BATCH" | "VARIANT";

export type EventType =
  | "BATCH_STAGE"
  | "VARIANT_STAGE"
  | "VARIANT_PROGRESS"
  | "TERMINAL"
  | "ERROR";

export interface EventEnvelope {
  schema_version: string;
  seq: number;
  ts: string;
  event_type: EventType;
  scope: EventScope;
  batch_id: string;
  run_id?: string;
  variant_key?: string;
  strategy_id?: string;
  stage?: string;
  strategy_substage?: string;
  progress?: {
    completed: number;
    total: number;
    unit: string;
    ratio: number;
  };
  message?: string;
  error?: string;
}

// ── Batch resolved identity ──

export interface VariantIdentity {
  variant_key: string;
  strategy_id: string;
  label?: string;
  implementation_hash: string;
  resolved_config_hash: string;
  status?: string;
  run_id?: string | null;
  snapshot_fingerprint?: string;
}

export interface BatchPlan {
  data_start: string;
  anchor_decision_date: string;
  simulation_start: string;
  evaluation_start_date: string;
  evaluation_end_date: string;
  variants: Array<{
    variant_key: string;
    anchor_decision_date: string;
    simulation_start: string;
  }>;
}

export interface ResolvedBatch {
  batch_id: string;
  schema_version: string;
  mode: string;
  catalog_version: string;
  framework_implementation_hash: string;
  variants: VariantIdentity[];
  plan: BatchPlan;
  executed_order: Array<{ variant_key: string }>;
}

// ── Batch detail response ──

export interface ChildRunState {
  schema_version: string;
  stage: string;
  batch_id: string;
  run_id: string;
  variant_key: string;
  strategy_id: string;
  mode: string;
}

export interface BatchDetail {
  batch_id: string;
  state: {
    schema_version: string;
    stage: string;
    batch_id: string;
    mode: string;
    failed_stage?: string;
    error?: string;
  };
  resolved: ResolvedBatch;
  child_runs: ChildRunState[];
  mode: "RESEARCH_ONLY";
}

// ── Batch list ──

export interface BatchListItem {
  batch_id: string;
  status: string;
  mode: string;
  variant_count: number;
  created_at: string;
}

// ── Comparison (§27) ──

export interface ComparisonRankingEntry {
  rank: number;
  variant_key: string;
  strategy_id: string;
  run_id: string;
  quality_status: string;
  annual_return: number;
}

export interface ComparisonContract {
  fingerprint: string;
  components: Record<string, string>;
}

export interface ComparisonReports {
  contract: ComparisonContract;
  ranking: ComparisonRankingEntry[];
  excluded: Array<{ variant_key: string; reason: string }>;
  quality_warnings: Array<{ variant_key: string; reason: string; message: string }>;
}

// ── Artifact manifest ──

export interface FileDetail {
  checksum: string;
}

export interface ArtifactManifest {
  batch_id: string;
  status: string;
  mode: string;
  catalog_version: string;
  framework_implementation_hash: string;
  data_snapshot_fingerprint: string;
  variants: Array<{
    variant_key: string;
    strategy_id: string;
    run_id: string;
    status: string;
  }>;
  files: string[];
  file_details: Record<string, FileDetail>;
  created_at: string;
}
