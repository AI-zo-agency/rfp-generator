import { describe, expect, it } from "vitest";
import { createMarkdownSourceMap, findSourceRange, projectMarkdown } from "./markdown-source-map";

// Shape of a real Section 1.3 draft: bold labels, an evidence citation, a
// wrapped paragraph. The preview renders this with the markup stripped, so
// every assertion below is "what the user actually selected on screen".
const SECTION = `## Business Information

**Legal Name:** Z'Onion Creative Group LLC

**Doing Business As:** zö agency

**Principal Owner:** Sonja Anderson

**Federal Employer Identification Number (EIN):** 47-4333943 [E12]

zö agency has delivered brand and digital work for public agencies
across the Pacific Northwest since 2013.

- Bend, OR headquarters
- 35 full-time employees
`;

/** The range must round-trip: slicing the source by it returns valid markdown. */
function slice(selected: string): string | null {
  const range = findSourceRange(SECTION, selected);
  return range ? SECTION.slice(range.start, range.end) : null;
}

describe("findSourceRange", () => {
  it("resolves a selection that spans bold markers", () => {
    // The exact case from the bug report: DOM says "Legal Name: Z'Onion…",
    // source says "**Legal Name:** Z'Onion…".
    expect(slice("Legal Name: Z'Onion Creative Group LLC")).toBe(
      "**Legal Name:** Z'Onion Creative Group LLC"
    );
  });

  it("resolves a bold label on its own and keeps the emphasis balanced", () => {
    expect(slice("Legal Name:")).toBe("**Legal Name:**");
  });

  it("drops a dangling emphasis marker rather than returning half-open bold", () => {
    expect(slice("Legal Name")).toBe("Legal Name");
  });

  it("resolves plain text with no markup", () => {
    expect(slice("Z'Onion Creative Group LLC")).toBe("Z'Onion Creative Group LLC");
  });

  it("resolves a selection whose evidence citation was stripped from the preview", () => {
    const range = findSourceRange(
      SECTION,
      "Federal Employer Identification Number (EIN): 47-4333943"
    );
    expect(range).not.toBeNull();
    expect(SECTION.slice(range!.start, range!.end)).toContain("47-4333943");
  });

  it("resolves a selection that crosses a block boundary", () => {
    // getSelection() joins block elements with newlines; the source has a
    // blank line plus bold markers in between.
    const range = findSourceRange(
      SECTION,
      "Doing Business As: zö agency\nPrincipal Owner: Sonja Anderson"
    );
    expect(range).not.toBeNull();
    expect(SECTION.slice(range!.start, range!.end)).toBe(
      "**Doing Business As:** zö agency\n\n**Principal Owner:** Sonja Anderson"
    );
  });

  it("resolves heading text without its # prefix", () => {
    expect(slice("Business Information")).toBe("Business Information");
  });

  it("resolves list item text without its bullet marker", () => {
    expect(slice("35 full-time employees")).toBe("35 full-time employees");
  });

  it("resolves a paragraph the renderer rewrapped onto one line", () => {
    // MarkdownReportBody joins wrapped paragraph lines with a space, so the
    // DOM has a space where the source has a newline.
    const range = findSourceRange(
      SECTION,
      "delivered brand and digital work for public agencies across the Pacific Northwest"
    );
    expect(range).not.toBeNull();
    expect(SECTION.slice(range!.start, range!.end)).toBe(
      "delivered brand and digital work for public agencies\nacross the Pacific Northwest"
    );
  });

  it("returns null for text that is not in the section", () => {
    expect(findSourceRange(SECTION, "Acme Widgets Incorporated")).toBeNull();
  });

  it("returns null for a selection under 3 characters", () => {
    expect(findSourceRange(SECTION, "zö")).toBeNull();
  });

  it("returns null for a whitespace-only selection", () => {
    expect(findSourceRange(SECTION, "   \n  ")).toBeNull();
  });
});

describe("projectMarkdown", () => {
  it("maps every projected character back to its source index", () => {
    const { text, indices } = projectMarkdown(SECTION);
    expect(indices).toHaveLength(text.length);
    for (let i = 0; i < text.length; i += 1) {
      if (text[i] === " ") continue; // collapsed whitespace maps to the run start
      expect(SECTION[indices[i]!]).toBe(text[i]);
    }
  });

  it("strips markup the preview does not render", () => {
    const { text } = projectMarkdown(SECTION);
    expect(text).not.toContain("**");
    expect(text).not.toContain("##");
    expect(text).not.toContain("[E12]");
  });
});

describe("createMarkdownSourceMap", () => {
  it("reuses one projection across lookups", () => {
    const map = createMarkdownSourceMap(SECTION);
    expect(map.find("Legal Name: Z'Onion Creative Group LLC")).toEqual(
      findSourceRange(SECTION, "Legal Name: Z'Onion Creative Group LLC")
    );
    expect(map.find("Sonja Anderson")).not.toBeNull();
  });

  it("handles an empty section", () => {
    expect(createMarkdownSourceMap("").find("anything")).toBeNull();
  });
});
