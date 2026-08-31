import { describe, expect, it } from "vitest";
import type { OutlineSection } from "@/types/proposal";
import { reorderSectionsById } from "@/components/ProposalSectionTree";

function sec(id: string, title: string): OutlineSection {
  return {
    id,
    title,
    wordTarget: 400,
    required: true,
    custom: false,
    content: "",
    status: "outline",
    source: "rfp",
  };
}

describe("reorderSectionsById", () => {
  it("moves a section to another section's index", () => {
    const sections = [sec("a", "A"), sec("b", "B"), sec("c", "C")];
    const next = reorderSectionsById(sections, "c", "a");
    expect(next.map((s) => s.id)).toEqual(["c", "a", "b"]);
  });

  it("no-ops when ids match or are missing", () => {
    const sections = [sec("a", "A"), sec("b", "B")];
    expect(reorderSectionsById(sections, "a", "a")).toBe(sections);
    expect(reorderSectionsById(sections, "x", "a")).toBe(sections);
  });
});
