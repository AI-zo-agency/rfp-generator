import { describe, expect, it } from "vitest";
import {
  billablePct,
  budgetPortfolio,
  buildSignals,
  buildWorkload,
  daysUntil,
  describeDue,
  describeOverdueHeat,
  describeProjectDue,
  filterProjects,
  formatProjectDate,
  formatUsdFromCents,
  hoursChartRows,
  hoursChartAxisMax,
  hoursChartAxisTicks,
  hoursChartTotalLabel,
  hoursRowBillablePct,
  hoursLabel,
  hoursNumber,
  nameList,
  overdueByProject,
  projectIsComplete,
  projectShortLabel,
  projectUrl,
  shortPersonName,
  taskUrl,
  toUTCDay,
  workByProject,
} from "./teamwork-derive";
import type { TeamworkOverview } from "../types/teamwork";

const TODAY = "2026-08-18";

describe("toUTCDay", () => {
  it("parses a plain date at UTC midnight", () => {
    expect(toUTCDay("2026-08-18")).toBe(Date.UTC(2026, 7, 18));
  });

  it("ignores a time component", () => {
    expect(toUTCDay("2026-08-18T22:30:00+05:30")).toBe(Date.UTC(2026, 7, 18));
  });

  it("returns null for empty or malformed input", () => {
    expect(toUTCDay(null)).toBeNull();
    expect(toUTCDay("")).toBeNull();
    expect(toUTCDay("not a date")).toBeNull();
  });
});

describe("daysUntil", () => {
  it("counts whole days forward", () => {
    expect(daysUntil("2026-08-21", TODAY)).toBe(3);
  });

  it("returns a negative count for past dates", () => {
    expect(daysUntil("2026-08-11", TODAY)).toBe(-7);
  });

  it("returns zero on the due date itself", () => {
    expect(daysUntil(TODAY, TODAY)).toBe(0);
  });

  it("crosses a month boundary correctly", () => {
    expect(daysUntil("2026-09-01", TODAY)).toBe(14);
  });

  it("returns null when either side is missing", () => {
    expect(daysUntil(null, TODAY)).toBeNull();
    expect(daysUntil("2026-08-21", "")).toBeNull();
  });
});

describe("describeDue", () => {
  it("labels a late date by how late it is", () => {
    expect(describeDue("2026-08-06", TODAY)).toEqual({
      label: "12d late",
      days: -12,
      tone: "late",
    });
  });

  it("labels today as Today", () => {
    expect(describeDue(TODAY, TODAY)).toEqual({ label: "Today", days: 0, tone: "soon" });
  });

  it("treats three days out as soon and four as later", () => {
    expect(describeDue("2026-08-21", TODAY).tone).toBe("soon");
    expect(describeDue("2026-08-22", TODAY).tone).toBe("later");
  });

  it("falls back to an em dash with no date", () => {
    expect(describeDue(null, TODAY)).toEqual({ label: "—", days: null, tone: "none" });
  });
});

describe("formatProjectDate", () => {
  it("formats a calendar date in UTC", () => {
    expect(formatProjectDate("2026-08-17")).toBe("Mon, Aug 17");
  });

  it("returns an em dash when the date is missing", () => {
    expect(formatProjectDate(null)).toBe("—");
  });
});

describe("describeProjectDue", () => {
  it("does not label a completed project as late", () => {
    expect(
      describeProjectDue(project({ due_date: "2026-08-17", status: "completed", progress_pct: 0 }), TODAY),
    ).toEqual({ label: "Complete", days: -1, tone: "none" });
  });

  it("still labels an open late project as late", () => {
    expect(describeProjectDue(project({ due_date: "2026-08-14", status: "late" }), TODAY).tone).toBe("late");
  });
});

describe("projectIsComplete", () => {
  it("treats Teamwork subStatus completed as complete", () => {
    expect(projectIsComplete(project({ status: "completed", progress_pct: 0 }))).toBe(true);
    expect(projectIsComplete(project({ status: "active", progress_pct: 0 }))).toBe(false);
  });
});

describe("hoursLabel", () => {
  it("shows one decimal below ten hours", () => {
    expect(hoursLabel(90)).toBe("1.5h");
  });

  it("drops the decimal at ten hours and above", () => {
    expect(hoursLabel(600)).toBe("10h");
  });

  it("renders zero without a decimal", () => {
    expect(hoursLabel(0)).toBe("0h");
  });
});

describe("hoursNumber", () => {
  it("matches hoursLabel rounding", () => {
    expect(hoursNumber(90)).toBe(1.5);
    expect(hoursNumber(600)).toBe(10);
    expect(hoursNumber(0)).toBe(0);
  });
});

describe("shortPersonName", () => {
  it("keeps a single token", () => {
    expect(shortPersonName("Sonja")).toBe("Sonja");
  });

  it("shortens a full name to first plus last initial", () => {
    expect(shortPersonName("Sonja Anderson")).toBe("Sonja A.");
  });

  it("ignores extra whitespace", () => {
    expect(shortPersonName("  Ray   Patel  ")).toBe("Ray P.");
  });
});

describe("hoursChartRows", () => {
  it("orders people by hours and caps the list", () => {
    const { rows, split } = hoursChartRows(
      [
        { id: "1", name: "Ada Lovelace", minutes: 60, billable_minutes: 60 },
        { id: "2", name: "Grace Hopper", minutes: 180, billable_minutes: 120 },
        { id: "3", name: "Skip Me", minutes: 0, billable_minutes: 0 },
      ],
      8,
    );
    expect(split).toBe(true);
    expect(rows.map((row) => row.name)).toEqual(["Grace H.", "Ada L."]);
    expect(rows[0]).toMatchObject({ hours: 3, billable: 2, nonBillable: 1 });
  });

  it("can return full names without a row cap", () => {
    const { rows } = hoursChartRows(
      [
        { id: "1", name: "Ada Lovelace", minutes: 60, billable_minutes: 60 },
        { id: "2", name: "Grace Hopper", minutes: 180, billable_minutes: 120 },
      ],
      { limit: null, fullNames: true },
    );
    expect(rows.map((row) => row.name)).toEqual(["Grace Hopper", "Ada Lovelace"]);
  });

  it("does not invent a billable split when the cache omits it", () => {
    const { rows, split } = hoursChartRows([{ id: "1", name: "Ada", minutes: 120 }]);
    expect(split).toBe(false);
    expect(rows[0]).toMatchObject({ name: "Ada", hours: 2, billable: 0, nonBillable: 2 });
  });
});

describe("hours chart helpers", () => {
  it("formats totals with one decimal", () => {
    expect(hoursChartTotalLabel(27.5)).toBe("27.5h");
  });

  it("computes row billable percent safely", () => {
    expect(hoursRowBillablePct(27, 27.5)).toBe(98);
    expect(hoursRowBillablePct(0, 0)).toBe(0);
  });

  it("builds a readable horizontal axis", () => {
    expect(hoursChartAxisMax(27.5)).toBe(30);
    expect(hoursChartAxisTicks(30)).toEqual([0, 10, 20, 30]);
  });
});

describe("billablePct", () => {
  it("rounds to a whole percent", () => {
    expect(billablePct(90, 120)).toBe(75);
    expect(billablePct(1, 3)).toBe(33);
  });

  it("returns zero rather than dividing by zero", () => {
    expect(billablePct(0, 0)).toBe(0);
  });
});

describe("deep links", () => {
  it("builds a project url", () => {
    expect(projectUrl("https://zo.teamwork.com", "10")).toBe(
      "https://zo.teamwork.com/app/projects/10/tasks",
    );
  });

  it("builds a task url", () => {
    expect(taskUrl("https://zo.teamwork.com", "50")).toBe("https://zo.teamwork.com/app/tasks/50");
  });

  it("tolerates a trailing slash on the base url", () => {
    expect(taskUrl("https://zo.teamwork.com/", "50")).toBe("https://zo.teamwork.com/app/tasks/50");
  });

  it("returns null when the base url or id is missing", () => {
    expect(projectUrl(null, "10")).toBeNull();
    expect(taskUrl("https://zo.teamwork.com", "")).toBeNull();
  });
});

function overview(patch: Partial<TeamworkOverview> = {}): TeamworkOverview {
  return {
    connected: true,
    base_url: "https://zo.teamwork.com",
    cache_ttl_seconds: 0,
    errors: {},
    summary: {
      project_count: 0,
      overdue_task_count: 0,
      upcoming_task_count: 0,
      late_milestone_count: 0,
      hours_this_month: 0,
      people_count: 0,
    },
    projects: [],
    overdue_tasks: [],
    upcoming_tasks: [],
    milestones: [],
    people: [],
    time: {
      period_start: "2026-08-01",
      period_end: "2026-08-18",
      total_minutes: 0,
      billable_minutes: 0,
      by_person: [],
      by_project: [],
    },
    ...patch,
  };
}

function project(patch: Partial<TeamworkOverview["projects"][number]> = {}) {
  return {
    id: "10",
    name: "Oakdale",
    status: "active",
    health: "ok" as const,
    company_name: "City of Oakdale",
    due_date: "2026-09-30",
    tasks_open: 4,
    tasks_completed: 1,
    tasks_overdue: 0,
    progress_pct: 20,
    // Budgeted by default so a healthy project does not trip unbudgeted signals.
    budget_capacity: 100_000,
    budget_used: 0,
    ...patch,
  };
}

function task(patch: Partial<TeamworkOverview["overdue_tasks"][number]> = {}) {
  return {
    id: "50",
    name: "Homepage copy",
    status: "new",
    due_date: "2026-08-11",
    project_id: "10",
    project_name: "Oakdale",
    assignees: ["Sonja Anderson"],
    ...patch,
  };
}

describe("nameList", () => {
  it("joins up to three names", () => {
    expect(nameList(["A", "B", "C"])).toBe("A, B, C");
  });

  it("summarizes the overflow", () => {
    expect(nameList(["A", "B", "C", "D", "E"])).toBe("A, B, C +2 more");
  });
});

describe("overdueByProject", () => {
  it("groups and sorts by overdue count", () => {
    const ranked = overdueByProject([
      task({ project_id: "1", project_name: "EKL 26140 Holiday" }),
      task({ id: "51", project_id: "1", project_name: "EKL 26140 Holiday" }),
      task({ id: "52", project_id: "2", project_name: "MPR 26148 Toolkit" }),
    ]);
    expect(ranked.map((row) => [row.project_name, row.count])).toEqual([
      ["EKL 26140 Holiday", 2],
      ["MPR 26148 Toolkit", 1],
    ]);
  });
});

describe("budgetPortfolio", () => {
  it("counts budgeted, unbudgeted, and near-budget at 75%", () => {
    const portfolio = budgetPortfolio([
      project({ budget_capacity: 10_000_00, budget_used: 8_000_00 }),
      project({ id: "11", name: "Open", budget_capacity: 0, budget_used: 0 }),
      project({ id: "12", name: "Calm", budget_capacity: 5_000_00, budget_used: 1_000_00 }),
    ]);
    expect(portfolio.budgetedCount).toBe(2);
    expect(portfolio.unbudgetedCount).toBe(1);
    expect(portfolio.capacity).toBe(15_000_00);
    expect(portfolio.used).toBe(9_000_00);
    expect(portfolio.nearBudget.map((p) => p.name)).toEqual(["Oakdale"]);
  });

  it("does not mark near-budget when usage is low", () => {
    const portfolio = budgetPortfolio([
      project({ budget_capacity: 10_000_00, budget_used: 2_000_00 }),
    ]);
    expect(portfolio.nearBudget).toEqual([]);
  });
});

describe("describeOverdueHeat", () => {
  it("leads with the hottest project then oldest late", () => {
    expect(
      describeOverdueHeat(
        [
          task({ project_name: "EKL 26140 Holiday" }),
          task({ id: "51", project_name: "EKL 26140 Holiday" }),
        ],
        14,
      ),
    ).toBe("2 on EKL · oldest 14d late");
  });

  it("returns the empty line when nothing is overdue", () => {
    expect(describeOverdueHeat([], 0)).toBe("Nothing overdue");
  });
});

describe("formatUsdFromCents", () => {
  it("rounds cents to whole dollars", () => {
    expect(formatUsdFromCents(1_155_375)).toBe("$11,554");
  });
});

describe("projectShortLabel", () => {
  it("keeps the leading job code", () => {
    expect(projectShortLabel("EKL 26140 Eklektik Holiday")).toBe("EKL");
  });
});

describe("buildSignals", () => {
  it("returns nothing when everything is healthy", () => {
    expect(buildSignals(overview({ projects: [project()] }), TODAY)).toEqual([]);
  });

  it("flags projects marked at risk", () => {
    const signals = buildSignals(
      overview({ projects: [project({ health: "bad", name: "Riverside" })] }),
      TODAY,
    );
    const risk = signals.find((s) => s.id === "projects-at-risk");
    expect(risk?.severity).toBe("critical");
    expect(risk?.headline).toBe("1 project flagged at risk");
    expect(risk?.detail).toBe("Riverside");
  });

  it("flags overdue tasks with no assignee", () => {
    const signals = buildSignals(
      overview({ overdue_tasks: [task({ assignees: [] }), task({ id: "51", assignees: ["Ray"] })] }),
      TODAY,
    );
    const unassigned = signals.find((s) => s.id === "overdue-unassigned");
    expect(unassigned?.severity).toBe("critical");
    expect(unassigned?.figure).toBe("1");
  });

  it("reports the oldest overdue task and escalates past two weeks", () => {
    const signals = buildSignals(
      overview({
        overdue_tasks: [task({ due_date: "2026-08-16" }), task({ id: "51", due_date: "2026-07-20" })],
      }),
      TODAY,
    );
    const oldest = signals.find((s) => s.id === "oldest-overdue");
    expect(oldest?.headline).toBe("Oldest overdue task is 29 days late");
    expect(oldest?.severity).toBe("critical");
  });

  it("flags overdue concentration on a hot project as critical", () => {
    const overdue_tasks = Array.from({ length: 11 }, (_, i) =>
      task({
        id: String(50 + i),
        project_id: "99",
        project_name: "EKL 26140 Holiday",
        due_date: "2026-08-10",
      }),
    );
    const signals = buildSignals(overview({ overdue_tasks }), TODAY);
    const heat = signals.find((s) => s.id === "overdue-concentration");
    expect(heat?.severity).toBe("critical");
    expect(heat?.headline).toBe("EKL 26140 Holiday holds 11 overdue tasks");
  });

  it("flags unbudgeted projects", () => {
    const signals = buildSignals(
      overview({
        projects: [
          project(),
          project({ id: "11", name: "No Cap", budget_capacity: 0, budget_used: 0 }),
        ],
      }),
      TODAY,
    );
    const unbudgeted = signals.find((s) => s.id === "unbudgeted-projects");
    expect(unbudgeted?.severity).toBe("warn");
    expect(unbudgeted?.figure).toBe("1");
    expect(unbudgeted?.detail).toBe("No Cap");
  });

  it("flags near-budget projects and skips them when usage is low", () => {
    expect(
      buildSignals(
        overview({
          projects: [project({ budget_capacity: 10_000_00, budget_used: 2_000_00 })],
        }),
        TODAY,
      ).some((s) => s.id === "near-budget"),
    ).toBe(false);

    const signals = buildSignals(
      overview({
        projects: [project({ name: "Hot", budget_capacity: 10_000_00, budget_used: 8_000_00 })],
      }),
      TODAY,
    );
    const near = signals.find((s) => s.id === "near-budget");
    expect(near?.headline).toBe("1 project at or over 75% of budget");
    expect(near?.detail).toBe("Hot");
  });

  it("flags projects past their due date that are not complete", () => {
    const signals = buildSignals(
      overview({ projects: [project({ due_date: "2026-08-01", progress_pct: 60 })] }),
      TODAY,
    );
    expect(signals.some((s) => s.id === "projects-past-due")).toBe(true);
  });

  it("does not flag a completed project that is past its due date", () => {
    const signals = buildSignals(
      overview({ projects: [project({ due_date: "2026-08-01", progress_pct: 100 })] }),
      TODAY,
    );
    expect(signals.some((s) => s.id === "projects-past-due")).toBe(false);
  });

  it("does not flag a Teamwork-completed project with 0% task progress", () => {
    const signals = buildSignals(
      overview({
        projects: [project({ due_date: "2026-08-17", status: "completed", progress_pct: 0 })],
      }),
      TODAY,
    );
    expect(signals.some((s) => s.id === "projects-past-due")).toBe(false);
  });

  it("sorts critical ahead of warn", () => {
    const signals = buildSignals(
      overview({
        projects: [project({ health: "bad" }), project({ id: "11", due_date: "2026-08-01", progress_pct: 10 })],
        milestones: [
          { id: "3", name: "Launch", status: "late", project_id: "10", project_name: "Oakdale", due_date: "2026-08-10", progress_pct: 40 },
        ],
      }),
      TODAY,
    );
    expect(signals[0].severity).toBe("critical");
    expect(signals.at(-1)?.severity).toBe("warn");
  });
});

describe("workByProject", () => {
  it("groups tasks and milestones under their project id", () => {
    const grouped = workByProject(
      overview({
        overdue_tasks: [task()],
        upcoming_tasks: [task({ id: "51", due_date: "2026-08-25" })],
        milestones: [
          { id: "3", name: "Launch", status: "late", project_id: "10", project_name: "Oakdale", due_date: "2026-08-10", progress_pct: 40 },
        ],
      }),
    );
    expect(grouped.get("10")?.overdue).toHaveLength(1);
    expect(grouped.get("10")?.upcoming).toHaveLength(1);
    expect(grouped.get("10")?.milestones).toHaveLength(1);
  });

  it("has no entry for a project with nothing in either bucket", () => {
    expect(workByProject(overview({ projects: [project()] })).get("10")).toBeUndefined();
  });
});

describe("filterProjects", () => {
  const projects = [
    project({ id: "1", health: "bad" }),
    project({ id: "2", tasks_overdue: 3 }),
    project({ id: "3", due_date: "2026-08-25" }),
    project({ id: "4", due_date: "2026-12-01" }),
  ];

  it("returns everything for all", () => {
    expect(filterProjects(projects, "all", TODAY)).toHaveLength(4);
  });

  it("filters to at-risk health", () => {
    expect(filterProjects(projects, "risk", TODAY).map((p) => p.id)).toEqual(["1"]);
  });

  it("filters to projects carrying overdue tasks", () => {
    expect(filterProjects(projects, "overdue", TODAY).map((p) => p.id)).toEqual(["2"]);
  });

  it("filters to projects due within fourteen days", () => {
    expect(filterProjects(projects, "soon", TODAY).map((p) => p.id)).toEqual(["3"]);
  });
});

describe("buildWorkload", () => {
  it("joins people, task counts, and logged time by name", () => {
    const rows = buildWorkload(
      overview({
        people: [{ id: "7", name: "Sonja Anderson", email: "sonja@zo.com", title: "PM", company_name: "zö" }],
        overdue_tasks: [task({ assignees: ["Sonja Anderson"] })],
        upcoming_tasks: [task({ id: "51", assignees: ["Sonja Anderson"] })],
        time: {
          period_start: "2026-08-01",
          period_end: "2026-08-18",
          total_minutes: 120,
          billable_minutes: 90,
          by_person: [{ id: "7", name: "sonja anderson", minutes: 120, billable_minutes: 90 }],
          by_project: [],
        },
      }),
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      name: "Sonja Anderson",
      title: "PM",
      overdue: 1,
      upcoming: 1,
      minutes: 120,
      billableMinutes: 90,
    });
  });

  it("includes an assignee who is not in the people directory", () => {
    const rows = buildWorkload(overview({ overdue_tasks: [task({ assignees: ["Contractor Ray"] })] }));
    expect(rows.map((r) => r.name)).toEqual(["Contractor Ray"]);
  });

  it("defaults billable minutes to zero when the cache predates the split", () => {
    const rows = buildWorkload(
      overview({
        time: {
          period_start: "2026-08-01",
          period_end: "2026-08-18",
          total_minutes: 60,
          billable_minutes: 0,
          by_person: [{ id: "7", name: "Sonja Anderson", minutes: 60 }],
          by_project: [],
        },
      }),
    );
    expect(rows[0].billableMinutes).toBe(0);
  });

  it("sorts the most overdue person first", () => {
    const rows = buildWorkload(
      overview({
        overdue_tasks: [
          task({ assignees: ["Ray"] }),
          task({ id: "51", assignees: ["Ray"] }),
          task({ id: "52", assignees: ["Sonja"] }),
        ],
      }),
    );
    expect(rows.map((r) => r.name)).toEqual(["Ray", "Sonja"]);
  });
});
