import { describe, expect, it } from "vitest";
import {
  billablePct,
  daysUntil,
  describeDue,
  hoursLabel,
  projectUrl,
  taskUrl,
  toUTCDay,
} from "./teamwork-derive";

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
