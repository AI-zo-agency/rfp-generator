export type TeamworkHealth = "unset" | "bad" | "ok" | "good";

export interface TeamworkProject {
  id: string;
  name: string;
  status: string;
  health: TeamworkHealth;
  company_name: string;
  start_date?: string | null;
  due_date?: string | null;
  tasks_open: number;
  tasks_completed: number;
  tasks_overdue: number;
  progress_pct: number;
  budget_capacity: number;
  budget_used: number;
}

export interface TeamworkTask {
  id: string;
  name: string;
  status: string;
  priority?: string | null;
  due_date?: string | null;
  project_id: string;
  project_name: string;
  assignees: string[];
}

export interface TeamworkMilestone {
  id: string;
  name: string;
  status: string;
  due_date?: string | null;
  project_id: string;
  project_name: string;
  progress_pct?: number | null;
}

export interface TeamworkPerson {
  id: string;
  name: string;
  email: string;
  title?: string | null;
  company_name: string;
}

export interface TeamworkTimeBucket {
  id: string;
  name: string;
  minutes: number;
  /** Optional: cache rows written before the per-bucket split shipped omit this. */
  billable_minutes?: number;
}

export interface TeamworkOverview {
  connected: boolean;
  base_url?: string | null;
  generated_at?: string | null;
  as_of?: string | null;
  synced_at?: string | null;
  sync_status?: "ok" | "failed" | "backfill_pending" | "missing";
  cache_ttl_seconds: number;
  errors: Record<string, string>;
  summary: {
    project_count: number;
    overdue_task_count: number;
    upcoming_task_count: number;
    late_milestone_count: number;
    hours_this_month: number;
    people_count: number;
  };
  projects: TeamworkProject[];
  overdue_tasks: TeamworkTask[];
  upcoming_tasks: TeamworkTask[];
  milestones: TeamworkMilestone[];
  people: TeamworkPerson[];
  time: {
    period_start: string;
    period_end: string;
    total_minutes: number;
    billable_minutes: number;
    by_person: TeamworkTimeBucket[];
    by_project: TeamworkTimeBucket[];
  };
}
