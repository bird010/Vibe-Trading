import type {
  CandidatePoolResponse,
  EconomicRoleCandidate,
  EconomicRoleSnapshot,
} from "./types";

export interface RoleHistoryArtifact {
  signal_date?: string;
  role_id?: string;
  role_name?: string;
  members?: unknown;
  members_as_of?: string | null;
  representative?: string | null;
  representative_as_of?: string | null;
  selection_mode?: string;
}

export interface RoleRepresentativeArtifact {
  signal_date?: string;
  role_id?: string;
  representative?: string | null;
  selection_mode?: string;
  previous_representative?: string | null;
}

function text(value: unknown): string {
  return String(value ?? "").trim();
}

function date(value: unknown): string {
  const raw = text(value);
  const digits = raw.replace(/\D/g, "");
  return digits.length >= 8 ? digits.slice(0, 8) : raw;
}

function codes(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(value.map(text).filter(Boolean))).sort();
}

function roleKey(signalDate: string, roleId: string): string {
  return `${signalDate}\u0000${roleId}`;
}

export function buildEconomicRoleCandidatePool(
  runId: string,
  history: RoleHistoryArtifact[],
  representatives: RoleRepresentativeArtifact[] = [],
): CandidatePoolResponse {
  const representativeByKey = new Map(
    representatives.map((row) => [
      roleKey(date(row.signal_date), text(row.role_id)),
      row,
    ]),
  );
  const byDate = new Map<string, EconomicRoleCandidate[]>();

  for (const row of history) {
    const signalDate = date(row.signal_date);
    const roleId = text(row.role_id);
    if (!signalDate || !roleId) continue;
    const representative = representativeByKey.get(roleKey(signalDate, roleId));
    const candidate: EconomicRoleCandidate = {
      role_id: roleId,
      role_name: text(row.role_name) || roleId,
      members: codes(row.members),
      members_as_of: date(row.members_as_of) || signalDate,
      representative: text(row.representative) || null,
      representative_as_of: date(row.representative_as_of) || null,
      selection_mode: text(row.selection_mode) || text(representative?.selection_mode),
      previous_representative: representative?.previous_representative ?? null,
    };
    const rows = byDate.get(signalDate) ?? [];
    rows.push(candidate);
    byDate.set(signalDate, rows);
  }

  const snapshots = Array.from(byDate.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([signalDate, roles]) => ({
      signal_date: signalDate,
      is_refresh: roles.some((role) => role.selection_mode === "REGULAR_REFRESH"),
      roles: roles.sort((left, right) => left.role_id.localeCompare(right.role_id)),
    } satisfies EconomicRoleSnapshot));
  const refreshSnapshots = snapshots.filter((snapshot) => snapshot.is_refresh);

  return {
    run_id: runId,
    kind: "ECONOMIC_ROLE",
    reclusters: [],
    role_snapshots: refreshSnapshots.length > 0 ? refreshSnapshots : snapshots.slice(0, 1),
  };
}
