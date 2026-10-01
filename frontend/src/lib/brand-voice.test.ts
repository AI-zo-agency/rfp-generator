import { describe, expect, test } from "vitest";
import {
  addedMessage,
  canSetDefault,
  errorMessage,
  pinSelection,
  shortHash,
  showNoDefaultNote,
  suggestRevisionLabel,
  type BrandVoiceRevision,
  type BrandVoiceRevisionList,
} from "./brand-voice";

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

function rev(over: Partial<BrandVoiceRevision> = {}): BrandVoiceRevision {
  return {
    id: "r7",
    label: "rev 7",
    sha256: "abcdef0123",
    notes: "",
    createdBy: "x",
    createdAt: "2026-09-30T00:00:00Z",
    size: 1024,
    isActive: false,
    ...over,
  };
}

function list(over: Partial<BrandVoiceRevisionList> = {}): BrandVoiceRevisionList {
  return { activeId: "r7", enabled: true, revisions: [rev({ isActive: true })], ...over };
}

describe("showNoDefaultNote", () => {
  test("shows while the default is the repo copy", () => {
    expect(showNoDefaultNote(list({ activeId: "builtin", revisions: [] }))).toBe(true);
    expect(showNoDefaultNote(list({ activeId: "builtin", revisions: [rev({ id: "builtin", isActive: true })] }))).toBe(true);
  });

  test("shows when no listed row is the default", () => {
    expect(showNoDefaultNote(list({ revisions: [rev()] }))).toBe(true);
  });

  test("hides once a stored revision is the default", () => {
    expect(showNoDefaultNote(list())).toBe(false);
  });

  test("hides when revisions cannot be added anyway", () => {
    expect(showNoDefaultNote(list({ enabled: false, activeId: "builtin", revisions: [] }))).toBe(false);
  });
});

describe("canSetDefault", () => {
  test("only stored, non-default revisions on an enabled environment", () => {
    expect(canSetDefault(rev(), true)).toBe(true);
    expect(canSetDefault(rev({ isActive: true }), true)).toBe(false);
    expect(canSetDefault(rev({ id: "builtin" }), true)).toBe(false);
    expect(canSetDefault(rev(), false)).toBe(false);
  });
});

describe("addedMessage", () => {
  test("names the new revision and points at the next step", () => {
    expect(addedMessage("rev 7")).toBe(
      "Added rev 7. Set it as the default below if you want new proposals to use it."
    );
  });
});
