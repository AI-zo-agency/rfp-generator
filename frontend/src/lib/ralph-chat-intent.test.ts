import { describe, expect, it } from "vitest";
import {
  ralphVerifyPrompt,
  ralphVoicePrompt,
  selectionPayloadForPin,
} from "./ralph-chat-intent";

describe("ralph-chat-intent", () => {
  it("builds selection-aware verify/voice button prompts", () => {
    const pin = {
      mode: "selection" as const,
      text: "We leverage synergistic solutions.",
      selection: {
        start: 10,
        end: 40,
        text: "We leverage synergistic solutions.",
      },
    };
    expect(ralphVerifyPrompt(pin)).toContain("We leverage synergistic");
    expect(ralphVoicePrompt(pin)).toContain("brand voice");
    expect(ralphVoicePrompt(pin)).toContain("We leverage synergistic");
  });

  it("sends selection text even without offsets", () => {
    const payload = selectionPayloadForPin(
      {
        mode: "selection",
        sectionTitle: "Cost",
        text: "Hourly rate $275",
      },
      "sec-1",
    );
    expect(payload?.text).toBe("Hourly rate $275");
  });
});
