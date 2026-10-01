import { describe, expect, it } from "vitest";
import {
  KB_ACCEPT_ALL,
  KB_DOCUMENT_TYPES,
  kbDocumentTypeLabel,
  kbUploadRules,
} from "./kb-document-types";

describe("kbUploadRules", () => {
  it("limits Pricing to Markdown, hides notes and takes the title from the file", () => {
    expect(kbUploadRules("pricing")).toEqual({
      isPricing: true,
      accept: ".md",
      titleRequired: false,
      showNotes: false,
    });
  });

  it("leaves every other type unchanged", () => {
    for (const type of KB_DOCUMENT_TYPES.filter((t) => t.value !== "pricing")) {
      expect(kbUploadRules(type.value)).toEqual({
        isPricing: false,
        accept: KB_ACCEPT_ALL,
        titleRequired: true,
        showNotes: true,
      });
    }
    expect(kbUploadRules("").isPricing).toBe(false);
  });
});

describe("kbDocumentTypeLabel", () => {
  it("labels the internal pricing category without offering it as a choice", () => {
    expect(kbDocumentTypeLabel("pricing_internal")).toBe("Pricing (internal)");
    expect(KB_DOCUMENT_TYPES.map((t) => t.value)).not.toContain("pricing_internal");
    expect(kbDocumentTypeLabel("pricing")).toBe("Pricing");
  });
});
