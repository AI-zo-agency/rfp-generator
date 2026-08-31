import { describe, expect, it } from "vitest";
import {
  PROPOSAL_TOOL_CAPABILITIES,
  capabilityById,
  formatDoesDoesntBlock,
} from "./proposal-tool-guide";

describe("proposal-tool-guide", () => {
  it("covers hover-tip tools clients confuse", () => {
    const ids = PROPOSAL_TOOL_CAPABILITIES.map((c) => c.id);
    expect(ids).toContain("completeClean");
    expect(ids).toContain("assistant");
    expect(ids).toContain("improveSection");
    expect(ids).toContain("generateProposal");
    expect(ids).toContain("matchStudies");
    expect(ids).toContain("keyPersonas");
    expect(ids).toContain("restore");
  });

  it("names Ralph in plain language with a Does and Doesn’t", () => {
    const ralph = capabilityById("ralph");
    expect(ralph.name.toLowerCase()).toContain("page limit");
    expect(ralph.name.toLowerCase()).toContain("ralph");
    expect(ralph.does.length).toBeGreaterThan(20);
    expect(ralph.doesnt.toLowerCase()).toMatch(/invent|pad|fact/);
  });

  it("treats Build as the full first draft, not a prelude to Review & fix", () => {
    const generate = capabilityById("generateProposal");
    expect(generate.name).toBe("Build my proposal");
    expect(generate.does.toLowerCase()).toMatch(/rfp/);
    expect(generate.doesnt.toLowerCase()).toMatch(/review & fix/);
    const scan = capabilityById("completeClean");
    expect(scan.name).toBe("Review & fix");
    expect(scan.does.toLowerCase()).toMatch(/optional second pass/);
    expect(scan.doesnt.toLowerCase()).toMatch(/every build/);
  });

  it("formats confirm-dialog blocks without losing either tool", () => {
    const block = formatDoesDoesntBlock("completeClean", "ralph");
    expect(block).toContain("Review & fix");
    expect(block).toContain("Does:");
    expect(block).toContain("Doesn’t:");
    expect(block).toContain("Ralph");
  });
});
