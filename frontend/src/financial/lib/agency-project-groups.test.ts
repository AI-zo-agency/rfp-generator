import { describe, expect, it } from "vitest";
import { groupJobsByProject } from "./agency-project-groups";
import type { AgencyJobRow } from "../types/agency";

function job(patch: Partial<AgencyJobRow> & Pick<AgencyJobRow, "project_id" | "job_label">): AgencyJobRow {
  return {
    project_name: patch.job_label,
    company_name: "",
    client_name: "Torrent Labs",
    status: "current",
    health: "ok",
    hours_mtd_minutes: 60,
    billed_ytd: 1000,
    open_ar: 200,
    join: "confirmed",
    client_map_id: "tl",
    link_confidence: "confirmed",
    via: "tag",
    ...patch,
  };
}

describe("groupJobsByProject", () => {
  it("nests jobs under one client and does not double money", () => {
    const groups = groupJobsByProject([
      job({ project_id: "1", job_label: "TOR 26143", hours_mtd_minutes: 60 }),
      job({ project_id: "2", job_label: "TOR 26144", hours_mtd_minutes: 120 }),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].clientName).toBe("Torrent Labs");
    expect(groups[0].jobCount).toBe(2);
    expect(groups[0].hoursMtdMinutes).toBe(180);
    expect(groups[0].billedYtd).toBe(1000);
    expect(groups[0].openAr).toBe(200);
    expect(groups[0].jobs.map((row) => row.job_label)).toEqual(["TOR 26143", "TOR 26144"]);
  });

  it("surfaces the weakest join on the parent", () => {
    const groups = groupJobsByProject([
      job({ project_id: "1", job_label: "A 11111", join: "confirmed" }),
      job({ project_id: "2", job_label: "A 22222", join: "needs mapping", billed_ytd: null, open_ar: null }),
    ]);
    expect(groups[0].join).toBe("needs mapping");
  });
});
