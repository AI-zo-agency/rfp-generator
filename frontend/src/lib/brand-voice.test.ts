import { describe, expect, test } from "vitest";
import { errorMessage, pinSelection, shortHash, suggestRevisionLabel } from "./brand-voice";

describe("suggestRevisionLabel", () => {
  test("reads the revision number out of the client's file names", () => {
    expect(suggestRevisionLabel("2026-09_zo_Brand_and_Writing_Standards_rev7.md")).toBe("rev 7");
    expect(suggestRevisionLabel("ZO_BRAND_AND_WRITING_STANDARDS_REV6.md")).toBe("rev 6");
    expect(suggestRevisionLabel("standards rev 12 final.md")).toBe("rev 12");
  });

  test("returns nothing when the name has no revision", () => {
    expect(suggestRevisionLabel("notes.md")).toBe("");
    expect(suggestRevisionLabel("")).toBe("");
  });
});

describe("shortHash", () => {
  test("keeps the first eight characters", () => {
    expect(shortHash("0123456789abcdef")).toBe("01234567");
    expect(shortHash("abc")).toBe("abc");
  });
});

describe("pinSelection", () => {
  test("shows the pinned revision when there is one", () => {
    expect(
      pinSelection({ pinned: { id: "p", label: "rev 6" }, active: { id: "a", label: "rev 7" } })
    ).toBe("p");
  });

  test("follows the default when nothing is pinned", () => {
    expect(pinSelection({ pinned: null, active: { id: "a", label: "rev 7" } })).toBe("a");
  });
});

describe("errorMessage", () => {
  test("uses a string detail", () => {
    expect(errorMessage({ detail: "Revision not found" }, 404)).toBe("Revision not found");
  });

  test("uses the message of an object detail", () => {
    expect(errorMessage({ detail: { message: "Label already used", code: "dup" } }, 409)).toBe(
      "Label already used"
    );
  });

  test("builds a message from the first FastAPI validation error", () => {
    expect(
      errorMessage({ detail: [{ loc: ["body", "label"], msg: "Field required", type: "missing" }] }, 422)
    ).toBe("label: Field required");
  });

  test("uses just the msg when the validation error has no loc", () => {
    expect(errorMessage({ detail: [{ msg: "Field required" }] }, 422)).toBe("Field required");
    expect(errorMessage({ detail: [{ loc: [], msg: "Field required" }] }, 422)).toBe("Field required");
  });

  test("falls through when detail is unusable", () => {
    expect(errorMessage({ detail: "", error: "Backend unreachable" }, 503)).toBe("Backend unreachable");
    expect(errorMessage({ detail: [], error: "boom" }, 500)).toBe("boom");
    expect(errorMessage({ detail: {} }, 500)).toBe("Request failed (500)");
    expect(errorMessage({ detail: [{}] }, 422)).toBe("Request failed (422)");
  });

  test("uses the proxy's error string", () => {
    expect(errorMessage({ error: "Backend unreachable" }, 503)).toBe("Backend unreachable");
  });

  test("falls back to the status for anything else", () => {
    expect(errorMessage({}, 500)).toBe("Request failed (500)");
    expect(errorMessage({ error: "" }, 502)).toBe("Request failed (502)");
    expect(errorMessage(null, 500)).toBe("Request failed (500)");
    expect(errorMessage("oops", 400)).toBe("Request failed (400)");
    expect(errorMessage(undefined, 400)).toBe("Request failed (400)");
  });
});
