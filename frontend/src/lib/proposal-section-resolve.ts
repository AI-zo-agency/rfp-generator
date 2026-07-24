import type { OutlineSection } from "@/types/proposal";

/** Name after "2.1 — " / em dash in a section title. */
export function sectionPersonName(title: string): string {
  const raw = (title || "").trim();
  if (!raw) return "";
  const parts = raw.split(/\s*[—–-]\s*/);
  if (parts.length < 2) return "";
  return parts.slice(1).join(" — ").trim();
}

export function isBioSection(section: OutlineSection | null | undefined): boolean {
  if (!section) return false;
  return (
    section.id.startsWith("section-2-bio-") &&
    section.id !== "section-2-bio-placeholder"
  );
}

export function isOurWorkSection(section: OutlineSection | null | undefined): boolean {
  if (!section) return false;
  return (
    section.id.startsWith("section-3-work-") &&
    section.id !== "section-3-work-placeholder"
  );
}

/** User is talking about team bios as a group. */
export function messageTargetsBios(message: string): boolean {
  return /\b(bio|bios|resume|resumes|team\s*bios?|team\s*member)\b/i.test(message);
}

/**
 * Query mentions case studies / replacing existing pieces but did not name a
 * specific sidebar title. Used only to avoid silently editing the open tab —
 * we ask instead of guessing.
 */
export function messageNeedsCaseStudyClarify(message: string): boolean {
  return (
    /\bcase\s*stud(?:y|ies)\b/i.test(message) ||
    /\breplace\b.{0,80}\b(existing|current|these|those)\b.{0,40}\b(case|work|stud)/i.test(
      message
    ) ||
    /\b(existing|current|these|those)\s+\d*\s*case\s*stud/i.test(message)
  );
}

/**
 * True when a pinned section must NOT receive this ask because the message
 * clearly names a different group (bios only — case studies use clarify).
 */
export function pinnedSectionConflictsWithMessage(
  message: string,
  pinnedSectionId: string | null | undefined
): boolean {
  if (!pinnedSectionId) return false;
  const isBio = pinnedSectionId.startsWith("section-2-bio-");
  if (messageTargetsBios(message) && !isBio) return true;
  return false;
}

export type ChatTargetResolution =
  | {
      kind: "resolved";
      section: OutlineSection;
      /** high = named in query or explicit pin; medium = group / viewing fallback */
      confidence: "high" | "medium";
      reason: string;
    }
  | {
      kind: "clarify";
      candidates: OutlineSection[];
      question: string;
    };

function formatClarifyQuestion(
  message: string,
  candidates: OutlineSection[],
  hint?: string
): string {
  const lines = candidates.slice(0, 8).map((s, i) => `${i + 1}. **${s.title}**`);
  const groupHint =
    hint ||
    (messageTargetsBios(message)
      ? "This sounds like a Team Bios change."
      : "I found more than one matching section.");
  return (
    `${groupHint} Which one should I use?\n\n` +
    `${lines.join("\n")}\n\n` +
    `Reply with the section number or title (e.g. \`3.1\` or the full sidebar name). ` +
    `Or use **Revise content** / **Improve full section** to pin the tab yourself.`
  );
}

/**
 * Read the query, find the best proposal section, or ask the user to confirm.
 *
 * Explicit pins (Revise excerpt / Improve full section) are handled by the caller —
 * pass `pinnedSection` when the pin is valid for this message.
 */
export function resolveChatTarget(
  sections: OutlineSection[],
  message: string,
  options?: {
    viewingSectionId?: string | null;
    /** Valid pin for this ask (already conflict-checked by caller). */
    pinnedSection?: OutlineSection | null;
  }
): ChatTargetResolution | null {
  if (sections.length === 0) return null;
  const text = message.trim();
  const viewingId = options?.viewingSectionId ?? null;
  const pinned = options?.pinnedSection ?? null;

  // Explicit user pin always wins when present and valid.
  if (pinned) {
    return {
      kind: "resolved",
      section: pinned,
      confidence: "high",
      reason: "pinned",
    };
  }

  if (!text) {
    const fallback =
      sections.find((s) => s.id === viewingId) ?? sections[0] ?? null;
    return fallback
      ? {
          kind: "resolved",
          section: fallback,
          confidence: "medium",
          reason: "viewing",
        }
      : null;
  }

  const lower = text.toLowerCase();
  const safeViewingId = pinnedSectionConflictsWithMessage(text, viewingId)
    ? null
    : viewingId;
  const viewing = sections.find((s) => s.id === safeViewingId) ?? null;

  const byTitle = [...sections].sort(
    (a, b) => (b.title?.length ?? 0) - (a.title?.length ?? 0)
  );

  // Exact / contained sidebar title
  const titleHits = byTitle.filter((section) => {
    const title = (section.title || "").trim();
    return title.length >= 4 && lower.includes(title.toLowerCase());
  });
  if (titleHits.length === 1) {
    return {
      kind: "resolved",
      section: titleHits[0],
      confidence: "high",
      reason: "title",
    };
  }
  if (titleHits.length > 1) {
    return {
      kind: "clarify",
      candidates: titleHits,
      question: formatClarifyQuestion(text, titleHits),
    };
  }

  // Label after em dash
  const namedHits = byTitle.filter((section) => {
    const name = sectionPersonName(section.title || "");
    return name.length >= 4 && lower.includes(name.toLowerCase());
  });
  if (namedHits.length === 1) {
    return {
      kind: "resolved",
      section: namedHits[0],
      confidence: "high",
      reason: "name",
    };
  }
  if (namedHits.length > 1) {
    const instead = lower.match(
      /\b(?:instead\s+of|replace|remove|swap\s+out)\s+([^,.]+?)(?:\s+bio|\s+resume|\s+case|\s+section|\s+with|\s+for|$)/i
    );
    if (instead?.[1]) {
      const needle = instead[1].trim().toLowerCase();
      const hit = namedHits.find((s) =>
        sectionPersonName(s.title || "").toLowerCase().includes(needle)
      );
      if (hit) {
        return {
          kind: "resolved",
          section: hit,
          confidence: "high",
          reason: "replace-name",
        };
      }
    }
    return {
      kind: "clarify",
      candidates: namedHits,
      question: formatClarifyQuestion(text, namedHits),
    };
  }

  // Section number e.g. 1.1 / 3.4
  const numMatch = lower.match(
    /\b(?:section\s*)?(\d+\.\d+)\b|\b(\d+\.\d+)\s*[—–-]/
  );
  const num = numMatch?.[1] || numMatch?.[2];
  if (num) {
    const hits = sections.filter((s) => {
      const t = (s.title || "").toLowerCase();
      return (
        t.startsWith(`${num} `) ||
        t.startsWith(`${num}—`) ||
        t.startsWith(`${num}–`) ||
        t.startsWith(`${num} -`) ||
        t.includes(` ${num} `) ||
        t.startsWith(num)
      );
    });
    if (hits.length === 1) {
      return {
        kind: "resolved",
        section: hits[0],
        confidence: "high",
        reason: "number",
      };
    }
    if (hits.length > 1) {
      return {
        kind: "clarify",
        candidates: hits,
        question: formatClarifyQuestion(text, hits),
      };
    }
  }

  if (messageTargetsBios(text)) {
    const bios = sections.filter(isBioSection);
    if (bios.length === 0) {
      return viewing
        ? { kind: "resolved", section: viewing, confidence: "medium", reason: "viewing" }
        : null;
    }
    const wholeGroup =
      /\b(all|every|another|more|add)\b.{0,40}\b(bio|team)/i.test(text) ||
      /\bteam\s*bios?\b/i.test(text);
    if (wholeGroup || bios.length === 1) {
      const section =
        viewing && isBioSection(viewing)
          ? viewing
          : (bios[bios.length - 1] ?? bios[0]);
      return {
        kind: "resolved",
        section,
        confidence: "medium",
        reason: "bio-group",
      };
    }
    return {
      kind: "clarify",
      candidates: bios,
      question: formatClarifyQuestion(text, bios),
    };
  }

  // Case studies mentioned but no specific name → ASK. Never silently use the
  // open tab (that rewrote Who We Are when Section 3 was meant).
  if (messageNeedsCaseStudyClarify(text)) {
    const cases = sections.filter(isOurWorkSection);
    if (cases.length === 1) {
      return {
        kind: "resolved",
        section: cases[0],
        confidence: "medium",
        reason: "sole-case-study",
      };
    }
    if (cases.length > 1) {
      const candidates =
        viewing && !isOurWorkSection(viewing) ? [...cases, viewing] : cases;
      return {
        kind: "clarify",
        candidates,
        question: formatClarifyQuestion(
          text,
          candidates,
          "You mentioned case studies, but I won't guess from the open tab. " +
            "Pick an Our Work piece" +
            (viewing && !isOurWorkSection(viewing)
              ? `, or say you meant the open section (**${viewing.title}**)`
              : "") +
            "."
        ),
      };
    }
  }

  // Generic improve with no name → use viewing tab (user is looking at it)
  if (viewing) {
    return {
      kind: "resolved",
      section: viewing,
      confidence: "medium",
      reason: "viewing",
    };
  }

  // No viewing context and no clear match → ask
  const top = sections.filter((s) => s.content?.trim()).slice(0, 6);
  const candidates = top.length > 0 ? top : sections.slice(0, 6);
  return {
    kind: "clarify",
    candidates,
    question:
      "Which section should I work on?\n\n" +
      candidates.map((s, i) => `${i + 1}. **${s.title}**`).join("\n") +
      "\n\nReply with the title, or pin it with **Revise content** / **Improve full section**.",
  };
}

/** Resolve which section the user means from their message (no dropdown). */
export function resolveSectionFromMention(
  sections: OutlineSection[],
  message: string,
  fallbackId: string | null
): OutlineSection | null {
  const result = resolveChatTarget(sections, message, {
    viewingSectionId: fallbackId,
  });
  if (!result) return null;
  if (result.kind === "clarify") {
    // Prefer first Our Work candidate for case-study clarifies (not open Who We Are).
    const work = result.candidates.find(isOurWorkSection);
    if (work && messageNeedsCaseStudyClarify(message)) {
      return work;
    }
    const viewing = sections.find((s) => s.id === fallbackId);
    if (viewing && result.candidates.some((c) => c.id === viewing.id)) {
      return viewing;
    }
    return result.candidates[0] ?? null;
  }
  return result.section;
}

export function messageLooksStructural(message: string): boolean {
  return /\b(add|delete|remove|instead\s+of|replace|swap|change\s+.+\s+to|more\s+\d*\s*bio|new\s+section|team\s*bios?|case\s*stud)\b/i.test(
    message
  );
}
