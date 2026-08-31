/** Group Agency job rows under a client/project parent for the expandable table. */

import type { AgencyJobRow, AgencyJoinStatus } from "../types/agency";

const JOIN_RANK: Record<AgencyJoinStatus, number> = {
  "needs mapping": 0,
  ambiguous: 1,
  suggested: 2,
  "job override": 3,
  confirmed: 4,
  internal: 5,
};

export interface AgencyProjectGroup {
  id: string;
  clientName: string;
  jobCount: number;
  hoursMtdMinutes: number;
  billedYtd: number | null;
  openAr: number | null;
  join: AgencyJoinStatus;
  status: string;
  jobs: AgencyJobRow[];
}

function groupKey(job: AgencyJobRow): string {
  if (job.client_map_id) return `map:${job.client_map_id}`;
  return `name:${(job.client_name || job.company_name || "unmapped").toLowerCase()}`;
}

function pickJoin(jobs: AgencyJobRow[]): AgencyJoinStatus {
  return jobs.reduce<AgencyJoinStatus>(
    (worst, job) => (JOIN_RANK[job.join] < JOIN_RANK[worst] ? job.join : worst),
    jobs[0]?.join ?? "needs mapping",
  );
}

function pickMoney(jobs: AgencyJobRow[]): { billedYtd: number | null; openAr: number | null } {
  // Same QB customers repeat on every job for a client — take one, never sum.
  for (const job of jobs) {
    if (job.billed_ytd != null || job.open_ar != null) {
      return { billedYtd: job.billed_ytd, openAr: job.open_ar };
    }
  }
  return { billedYtd: null, openAr: null };
}

function pickStatus(jobs: AgencyJobRow[]): string {
  if (jobs.some((job) => (job.status || "").toLowerCase() === "late")) return "late";
  return jobs[0]?.status || "—";
}

export function groupJobsByProject(jobs: AgencyJobRow[]): AgencyProjectGroup[] {
  const buckets = new Map<string, AgencyJobRow[]>();
  for (const job of jobs) {
    const key = groupKey(job);
    const list = buckets.get(key);
    if (list) list.push(job);
    else buckets.set(key, [job]);
  }

  const groups: AgencyProjectGroup[] = [];
  for (const [id, groupJobs] of buckets) {
    const sorted = [...groupJobs].sort((a, b) => a.job_label.localeCompare(b.job_label));
    const money = pickMoney(sorted);
    groups.push({
      id,
      clientName: sorted[0]?.client_name || sorted[0]?.company_name || "Unmapped",
      jobCount: sorted.length,
      hoursMtdMinutes: sorted.reduce((sum, job) => sum + (job.hours_mtd_minutes || 0), 0),
      billedYtd: money.billedYtd,
      openAr: money.openAr,
      join: pickJoin(sorted),
      status: pickStatus(sorted),
      jobs: sorted,
    });
  }

  return groups.sort((a, b) => a.clientName.localeCompare(b.clientName));
}
