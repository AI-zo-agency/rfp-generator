import type { OutlineSection } from "@/types/proposal";

/** Name after "2.1 — " / em dash in a section title. */
export function sectionPersonName(title: string): string {
  const raw = (title || "").trim();
  if (!raw) return "";
  const parts = raw.split(/\s*[—–-]\s*/);
  if (parts.length < 2) return "";
  return parts.slice(1).join(" — ").trim();
}

/** Resolve which section the user means from their message (no dropdown). */
export function resolveSectionFromMention(
  sections: OutlineSection[],
  message: string,
  fallbackId: string | null
): OutlineSection | null {
  const text = message.trim();
  if (!text || sections.length === 0) {
    return sections.find((s) => s.id === fallbackId) ?? sections[0] ?? null;
  }
  const lower = text.toLowerCase();
  const fallback =
    sections.find((s) => s.id === fallbackId) ?? sections[0] ?? null;

  // Prefer longer title matches first
  const byTitle = [...sections].sort(
    (a, b) => (b.title?.length ?? 0) - (a.title?.length ?? 0)
  );
  for (const section of byTitle) {
    const title = (section.title || "").trim();
    if (title.length >= 4 && lower.includes(title.toLowerCase())) {
      return section;
    }
  }

  // Label after em dash for ANY section (bios, case studies, forms…)
  const namedHits = byTitle.filter((section) => {
    const name = sectionPersonName(section.title || "");
    return name.length >= 4 && lower.includes(name.toLowerCase());
  });
  if (namedHits.length === 1) return namedHits[0];
  if (namedHits.length > 1) {
    const instead = lower.match(
      /\b(?:instead\s+of|replace|remove|swap\s+out)\s+([^,.]+?)(?:\s+bio|\s+resume|\s+case|\s+section|\s+with|\s+for|$)/i
    );
    if (instead?.[1]) {
      const needle = instead[1].trim().toLowerCase();
      const hit = namedHits.find((s) =>
        sectionPersonName(s.title || "").toLowerCase().includes(needle)
      );
      if (hit) return hit;
    }
    return namedHits[0];
  }

  // "1.1", "section 3", "§ 2.1" — require minor so bare "2" doesn't steal focus
  const numMatch = lower.match(
    /\b(?:section\s*)?(\d+\.\d+)\b|\b(\d+\.\d+)\s*[—–-]/
  );
  const num = numMatch?.[1] || numMatch?.[2];
  if (num) {
    const hit = sections.find((s) => {
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
    if (hit) return hit;
  }

  // Bio/resume talk without a specific open tab → stay in Team Bios, not Insurance
  if (/\b(bio|bios|resume|resumes|team\s*bios?|team\s*member)/i.test(text)) {
    const bios = sections.filter(
      (s) =>
        s.id.startsWith("section-2-bio-") &&
        s.id !== "section-2-bio-placeholder"
    );
    if (bios.length) {
      if (fallback && bios.some((b) => b.id === fallback.id)) return fallback;
      return bios[bios.length - 1] ?? bios[0];
    }
  }

  return fallback;
}

export function messageLooksStructural(message: string): boolean {
  return /\b(add|delete|remove|instead\s+of|replace|swap|change\s+.+\s+to|more\s+\d*\s*bio|new\s+section|team\s*bios?|case\s*stud)\b/i.test(
    message
  );
}
