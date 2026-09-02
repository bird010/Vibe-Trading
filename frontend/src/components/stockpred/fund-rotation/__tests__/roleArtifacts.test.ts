import { describe, expect, it } from "vitest";
import {
  buildEconomicRoleCandidatePool,
  type RoleHistoryArtifact,
  type RoleRepresentativeArtifact,
} from "../roleArtifacts";

describe("buildEconomicRoleCandidatePool", () => {
  it("builds refresh snapshots from Role history without inventing clusters", () => {
    const history: RoleHistoryArtifact[] = [
      {
        signal_date: "20240105",
        role_id: "CN_DEFENSIVE_EQUITY",
        role_name: "中国防守权益",
        members: ["B.SZ", "A.SH"],
        members_as_of: "20240105",
        representative: "A.SH",
        representative_as_of: "20240105",
        selection_mode: "REGULAR_REFRESH",
      },
      {
        signal_date: "20240105",
        role_id: "CN_GROWTH_EQUITY",
        role_name: "中国成长权益",
        members: ["C.SH"],
        members_as_of: "20240105",
        representative: "C.SH",
        representative_as_of: "20240105",
        selection_mode: "REGULAR_REFRESH",
      },
      {
        signal_date: "20240112",
        role_id: "CN_DEFENSIVE_EQUITY",
        role_name: "中国防守权益",
        members: ["B.SZ", "A.SH"],
        members_as_of: "20240105",
        representative: "A.SH",
        representative_as_of: "20240105",
        selection_mode: "LOCK_MAINTENANCE",
      },
      {
        signal_date: "20240112",
        role_id: "CN_GROWTH_EQUITY",
        role_name: "中国成长权益",
        members: ["C.SH"],
        members_as_of: "20240105",
        representative: "C.SH",
        representative_as_of: "20240105",
        selection_mode: "LOCK_MAINTENANCE",
      },
    ];
    const representatives: RoleRepresentativeArtifact[] = [
      {
        signal_date: "20240105",
        role_id: "CN_DEFENSIVE_EQUITY",
        representative: "A.SH",
        selection_mode: "REGULAR_REFRESH",
        previous_representative: null,
      },
    ];

    const result = buildEconomicRoleCandidatePool(
      "run-role",
      history,
      representatives,
    );

    expect(result.kind).toBe("ECONOMIC_ROLE");
    expect(result.reclusters).toEqual([]);
    expect(result.role_snapshots).toHaveLength(1);
    expect(result.role_snapshots?.[0]).toMatchObject({
      signal_date: "20240105",
      is_refresh: true,
      roles: [
        expect.objectContaining({
          role_id: "CN_DEFENSIVE_EQUITY",
          members: ["A.SH", "B.SZ"],
          representative: "A.SH",
          selection_mode: "REGULAR_REFRESH",
          previous_representative: null,
        }),
        expect.objectContaining({ role_id: "CN_GROWTH_EQUITY", representative: "C.SH" }),
      ],
    });
  });
});
