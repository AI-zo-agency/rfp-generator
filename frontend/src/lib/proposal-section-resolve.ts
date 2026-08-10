import type { OutlineSection } from "@/types/proposal";
import { getManuscriptSections } from "./proposal-outline-tree";

/** Name after "2.1 — " / em dash in a section title. */
export function sectionPersonName(title: string): string {
  const raw = (title || "").trim();
  if (!raw) return "";
  const parts = raw.split(/\s*[—–-]\s*/);
  if (parts.length < 2) return "";
  return parts.slice(1).join(" — ").trim();
}

const TITLE_STOPWORDS = new Set([
  "the",
  "and",
  "for",
  "with",
  "from",
  "section",
  "part",
  "our",
  "of",
  "to",
  "a",
  "an",
]);

/** True when the user explicitly scopes to the open/pinned tab. */
export function messagePointsAtOpenSection(message: string): boolean {
  return /\b(here|this\s+section|this\s+tab|this\s+part|open\s+(?:section|tab)|in\s+this\s+(?:section|tab|part)|for\s+this\s+(?:section|tab)|improve\s+this\s+section)\b/i.test(
    message || ""
  );
}

/**
 * "§21" / "sec 21" / "section 21" → match sidebar title starting with 21. / 21 —
 * (not the same as manuscript ordinal "section 21 of 38").
 */
export function resolveSectionByMarkNumber(
  sections: OutlineSection[],
  message: string
): OutlineSection | null {
  const text = message || "";
  const match =
    text.match(/(?:§|sec(?:tion)?\.?)\s*(\d+)\b(?!\s*\.\d)/i) ||
    text.match(/\b(?:fix|edit|rewrite|update|patch)\s+(?:§\s*)?(\d+)\b/i);
  if (!match) return null;
  const n = match[1];
  const hits = sections.filter((s) => {
    const t = (s.title || "").trim();
    return (
      new RegExp(`^\\s*${n}\\s*[.:—–\\-)]`, "i").test(t) ||
      new RegExp(`^\\s*${n}\\s+`, "i").test(t)
    );
  });
  if (hits.length === 1) return hits[0];
  if (hits.length > 1) {
    const lower = text.toLowerCase();
    const topical = hits.find((s) =>
      significantTitleTokens(s.title || "").some((tok) => lower.includes(tok))
    );
    return topical ?? hits[0];
  }
  return null;
}

/**
 * True when the topic word is the ask's target — not an incidental mention
 * ("I flagged this before the References fix" must NOT steal §11 Umatilla).
 */
export function messageTargetsUniqueTopic(
  message: string,
  topic: string
): boolean {
  const text = message || "";
  if (!new RegExp(`\\b${topic}\\b`, "i").test(text)) return false;

  // Stronger competing targets win — do not hijack to References/Pricing.
  if (
    /\b(?:umatilla|rock\s+the\s+locks|case\s+stud(?:y|ies)|cover\s+letter)\b/i.test(
      text
    )
  ) {
    const topicIsPrimary = new RegExp(
      `(?:fix|edit|rewrite|update|patch|fill|improve)\\s+(?:the\\s+)?${topic}\\b|` +
        `\\b${topic}\\s+(?:section|tab|contacts?|integrity)\\b|` +
        `(?:§|sec(?:tion)?\\.?)\\s*\\d+[^\\n]{0,40}\\b${topic}\\b`,
      "i"
    ).test(text);
    if (!topicIsPrimary) return false;
  }

  // Require intentional framing for "references" / "reference".
  if (topic === "references" || topic === "reference") {
    return new RegExp(
      `(?:fix|edit|rewrite|update|patch|fill|improve|scrub)\\s+(?:the\\s+|§\\s*\\d+\\s+)?${topic}\\b|` +
        `\\b${topic}\\s+(?:section|tab|contacts?|integrity|only)\\b|` +
        `(?:§|sec(?:tion)?\\.?)\\s*\\d+[^\\n]{0,60}\\b${topic}\\b|` +
        `\\b(?:upon\\s+request|pre-?cleared)\\b`,
      "i"
    ).test(text);
  }

  // "implement budget table here" must stay on the open tab — never steal to Cost.
  if (
    (topic === "budget" || topic === "pricing") &&
    messagePointsAtOpenSection(text)
  ) {
    return false;
  }

  return true;
}

/** Unique topical title headword (e.g. only one "References" tab). */
export function resolveSectionByUniqueTopic(
  sections: OutlineSection[],
  message: string
): OutlineSection | null {
  if (!(message || "").trim()) return null;
  const topics = [
    "references",
    "reference",
    "pricing",
    "budget",
    "insurance",
    "certifications",
    "subcontractor",
    "subcontractors",
    "acknowledgement",
    "addenda",
  ];
  for (const topic of topics) {
    if (!messageTargetsUniqueTopic(message, topic)) continue;
    const hits = sections.filter((s) => {
      const core = normalizeTitlePhrase(sectionTitleCore(s.title || ""));
      return (
        core.includes(topic) ||
        normalizeTitlePhrase(s.title || "").includes(topic)
      );
    });
    if (hits.length === 1) return hits[0];
  }
  return null;
}

/**
 * Pull a section target from prior chat turns (§21, References, titled asks).
 */
export function resolveSectionFromConversationHistory(
  sections: OutlineSection[],
  history: Array<{ role: string; content: string }> | null | undefined
): OutlineSection | null {
  if (!history?.length) return null;
  for (let i = history.length - 1; i >= 0; i--) {
    const turn = history[i];
    const content = (turn?.content || "").trim();
    if (!content || content.length < 8) continue;
    const byMark = resolveSectionByMarkNumber(sections, content);
    if (byMark) return byMark;
    const byTopic = resolveSectionByUniqueTopic(sections, content);
    if (byTopic) return byTopic;
    const titleHits = sections.filter((s) =>
      messageMentionsSectionTitle(content, s.title || "")
    );
    if (titleHits.length === 1) return titleHits[0];
  }
  return null;
}

/**
 * When the prior assistant turn listed numbered options, map the user's reply
 * to that candidate (e.g. pasted "Section 13 General Requirements…").
 */
export function resolveSectionFromClarifyReply(
  sections: OutlineSection[],
  message: string,
  history: Array<{ role: string; content: string }> | null | undefined
): OutlineSection | null {
  if (!history?.length || !(message || "").trim()) return null;
  let clarifyTurn: string | null = null;
  for (let i = history.length - 1; i >= 0; i--) {
    const turn = history[i];
    if (turn?.role !== "assistant") continue;
    const content = (turn.content || "").trim();
    if (
      /\bwhich\s+(?:section|one)\b/i.test(content) ||
      (/\b\d+\.\s+\*\*/.test(content) && content.split("\n").length >= 3)
    ) {
      clarifyTurn = content;
      break;
    }
  }
  if (!clarifyTurn) return null;

  const lower = normalizeTitlePhrase(message);
  const bareChoice = message.trim().match(/^\s*(?:\()?(\d+)(?:\))?[.\)]?\s*$/);
  const options: Array<{ index: number; label: string }> = [];
  for (const line of clarifyTurn.split("\n")) {
    const m =
      line.match(/^\s*(?:\()?(\d+)(?:\))?\.\s+\*\*([^*]+)\*\*/) ||
      line.match(/^\s*(?:\()?(\d+)(?:\))?\.\s+(.+?)\s*$/);
    if (!m) continue;
    const index = Number.parseInt(m[1], 10);
    const label = (m[2] || "")
      .replace(/\s*\(currently open[^)]*\)/i, "")
      .replace(/\*\*/g, "")
      .trim();
    if (!Number.isFinite(index) || label.length < 3) continue;
    options.push({ index, label });
  }
  if (options.length === 0) return null;

  const pickByLabel = (label: string): OutlineSection | null => {
    const hits = sections.filter((s) =>
      messageMentionsSectionTitle(label, s.title || "")
    );
    if (hits.length === 1) return hits[0];
    const soft = sections.filter((s) => {
      const core = normalizeTitlePhrase(sectionTitleCore(s.title || ""));
      const needle = normalizeTitlePhrase(label);
      return (
        core.length >= 8 &&
        needle.length >= 8 &&
        (core.includes(needle) || needle.includes(core.slice(0, 40)))
      );
    });
    return soft.length === 1 ? soft[0] : null;
  };

  if (bareChoice) {
    const n = Number.parseInt(bareChoice[1], 10);
    const opt = options.find((o) => o.index === n);
    if (opt) {
      const hit = pickByLabel(opt.label);
      if (hit) return hit;
    }
  }

  for (const opt of options) {
    const optNorm = normalizeTitlePhrase(opt.label);
    if (
      optNorm.length >= 8 &&
      (lower.includes(optNorm) || optNorm.includes(lower.slice(0, 48)))
    ) {
      const hit = pickByLabel(opt.label);
      if (hit) return hit;
    }
    // User pasted a long option line that shares the distinctive head.
    const optTokens = optNorm.split(" ").filter((t) => t.length >= 4);
    const head = optTokens.slice(0, 4).join(" ");
    if (head.length >= 12 && lower.includes(head)) {
      const hit = pickByLabel(opt.label);
      if (hit) return hit;
    }
  }
  return null;
}

/**
 * "section 15" / "section 15 of 18" → 1-based index in manuscript (sidebar) order.
 * Not the same as "3.1" dotted titles.
 */
export function resolveSectionByOrdinal(
  sections: OutlineSection[],
  message: string
): OutlineSection | null {
  const match = (message || "").match(
    /\bsection\s+(\d+)\b(?:\s+of\s+\d+)?(?!\s*\.\d)/i
  );
  if (!match) return null;
  const n = Number.parseInt(match[1], 10);
  if (!Number.isFinite(n) || n < 1) return null;
  const manuscript = getManuscriptSections(sections);
  const ordered = manuscript.length > 0 ? manuscript : sections;
  return ordered[n - 1] ?? null;
}

/** Strip leading "2.1 — " / "Section 2:" so we match the topical title. */
export function sectionTitleCore(title: string): string {
  return (title || "")
    .trim()
    .replace(/^\s*(?:section\s*)?\d+(?:\.\d+)*\s*[—\-–:.]?\s*/i, "")
    .trim();
}

function normalizeTitlePhrase(text: string): string {
  return text
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function significantTitleTokens(title: string): string[] {
  return normalizeTitlePhrase(sectionTitleCore(title) || title)
    .split(" ")
    .filter((t) => t.length >= 4 && !TITLE_STOPWORDS.has(t));
}

/**
 * True when the user message clearly refers to this section by title
 * (full, core after number, contiguous title head, or enough title tokens).
 */
export function messageMentionsSectionTitle(
  message: string,
  title: string
): boolean {
  const lower = normalizeTitlePhrase(message);
  if (!lower || !title.trim()) return false;
  const full = normalizeTitlePhrase(title);
  if (full.length >= 4 && lower.includes(full)) return true;
  const core = normalizeTitlePhrase(sectionTitleCore(title));
  if (core.length >= 4 && lower.includes(core)) return true;
  const tokens = significantTitleTokens(title);
  if (tokens.length === 0) return false;
  if (tokens.length === 1 && tokens[0].length >= 8 && lower.includes(tokens[0])) {
    return true;
  }
  // Long titles (Compliance — SOW, Timelines, Budgets…) must not require EVERY
  // token — pasted clarify replies usually only repeat the head phrase.
  const head = tokens.slice(0, Math.min(4, tokens.length)).join(" ");
  if (head.length >= 12 && lower.includes(head)) return true;
  const hits = tokens.filter((t) => lower.includes(t));
  if (hits.length >= 2 && tokens.every((t) => lower.includes(t))) return true;
  if (hits.length >= 3) return true;
  if (
    hits.length >= 2 &&
    hits.length >= Math.ceil(tokens.length * 0.5)
  ) {
    return true;
  }
  return false;
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

/** User is asking a question / for an explanation — not requesting a rewrite. */
export function messageLooksChatQuestion(message: string): boolean {
  const text = (message || "").trim();
  if (!text) return false;
  // Explicit mutate verbs win unless the sentence is a clear question opener.
  const hasEditVerb =
    /\b(?:change|fix|update|rewrite|revise|edit|improve|shorten|lengthen|remove|replace|fill|patch|insert|delete|correct|tighten|trim|reword|rephrase|polish|expand|condense)\b/i.test(
      text
    );
  const questionOpener =
    /^(?:what|whats|what's|who|why|when|where|how|explain|summarize|describe|tell\s+me|is|are|does|do|can\s+you\s+(?:tell|explain|confirm|verify|check))\b/i.test(
      text
    );
  if (hasEditVerb && !questionOpener) return false;
  return (
    questionOpener ||
    /\bwhat\s+(?:is|are|'s|does|this|the|about)\b/i.test(text) ||
    /\b(?:this\s+)?section\s+about\b/i.test(text) ||
    /\b(?:verify|confirm|cross[\s-]?check|cross[\s-]?verify|fact[\s-]?check|fabricat)/i.test(
      text
    ) ||
    /\?\s*$/.test(text)
  );
}

/**
 * Status copy while a chat turn is in flight. Questions must never say
 * "Improving…" just because a section pin is active.
 */
export function chatBusyStatusLabel(
  message: string,
  sectionTitle: string,
  options?: {
    proposalWide?: boolean;
    referenceMode?: "selection" | "section" | null;
    sameSectionPinned?: boolean;
  }
): string {
  const title = sectionTitle.trim() || "section";
  const trimmed = message.trim();
  if (options?.proposalWide) return "Reviewing the full proposal…";
  if (/apply these fixes|patch-wise across|across the proposal/i.test(trimmed)) {
    return "Applying patch-wise fixes across the proposal…";
  }
  if (messageLooksChatQuestion(trimmed)) {
    return `Answering about ${title}…`;
  }
  if (options?.sameSectionPinned && options.referenceMode === "selection") {
    return `Editing excerpt in ${title}…`;
  }
  if (options?.sameSectionPinned && options.referenceMode === "section") {
    return `Improving ${title}…`;
  }
  if (
    messageLooksOutlineStructure(trimmed) ||
    messageLooksStructural(trimmed) ||
    messageTargetsBios(trimmed)
  ) {
    return "Updating proposal outline…";
  }
  return `Working on ${title}…`;
}

/** User is asking about the whole draft — not the open tab alone. */
export function messageLooksProposalWide(message: string): boolean {
  const text = message.trim();
  if (!text) return false;
  const sectionMarks = text.match(/\b(?:section|sec|§)\s*\d+\b/gi) || [];
  const hasMultiSectionRefs = sectionMarks.length >= 2;
  // "apply these fixes to §21" is a single-section ask — not proposal-wide.
  const singleSectionApply =
    sectionMarks.length === 1 &&
    /\b(?:fix|apply|patch|rewrite|update)\b/i.test(text);
  if (singleSectionApply) return false;
  const hasCrossSectionBudgetContradiction =
    /\b(contradict(?:ory|s|ion)|inconsisten(?:t|cy)|not\s+the\s+same|mismatch)\b/i.test(
      text
    ) &&
    /\b(budget|investment|pass-?through|invoicing|agency\s+fee|media\s+spend)\b/i.test(
      text
    );
  return (
    hasMultiSectionRefs ||
    hasCrossSectionBudgetContradiction ||
    /\b(?:whole|entire|full)\s+(?:proposal|document|draft|manuscript)\b/i.test(text) ||
    /\bacross\s+(?:the\s+)?(?:proposal|draft|manuscript)\b/i.test(text) ||
    /\ball\s+sections\b/i.test(text) ||
    /\bproposal[- ]wide\b|\bmanuscript[- ]wide\b/i.test(text) ||
    /\bgap(?:s)?(?:\s+analysis)?\b/i.test(text) ||
    /\bwhat'?s\s+missing\b|\bmissing\s+from\s+(?:the\s+)?proposal\b/i.test(text) ||
    /\breview\s+(?:the\s+)?(?:entire\s+|whole\s+|full\s+)?proposal\b/i.test(text) ||
    /\b(?:trade[- ]secret|terms\s+and\s+conditions|submission\s+compliance)\b/i.test(
      text
    ) ||
    (/\bapply these fixes\b|\bpatch-wise\b/i.test(text) && sectionMarks.length === 0)
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
 *
 * Does NOT apply when the user is clearly adding/creating new outline tabs —
 * those go to the structure planner.
 */
export function messageNeedsCaseStudyClarify(message: string): boolean {
  if (messageLooksOutlineStructure(message)) return false;
  return (
    /\bcase\s*stud(?:y|ies)\b/i.test(message) ||
    /\breplace\b.{0,80}\b(existing|current|these|those)\b.{0,40}\b(case|work|stud)/i.test(
      message
    ) ||
    /\b(existing|current|these|those)\s+\d*\s*case\s*stud/i.test(message)
  );
}

/**
 * Add / create / delete sidebar sections — proposal-wide outline ops.
 * Must not bind to the open tab or stop for case-study clarify.
 */
export function messageLooksOutlineStructure(message: string): boolean {
  const text = message.trim();
  if (!text) return false;
  return (
    // "make this section tighter" is an edit — not outline structure.
    /\b(?:add|create|insert)\b.{0,60}\b(?:new\s+)?(?:sidebar\s+)?(?:section|tab|h2)\b/i.test(
      text
    ) ||
    /\bmake\b.{0,40}\b(?:a\s+)?new\s+(?:sidebar\s+)?(?:section|tab|h2)\b/i.test(
      text
    ) ||
    /\b(?:add|create)\b.{0,40}\b(?:another|new|more)\b.{0,40}\b(?:bio|resume|case\s*stud)/i.test(
      text
    ) ||
    /\b(?:delete|remove)\b.{0,40}\b(?:section|tab|bio|case\s*stud)/i.test(text) ||
    /\binstead\s+of\b.{0,80}\b(?:add|use|put)\b/i.test(text) ||
    /\bmore\s+\d*\s*(?:team\s*)?bios?\b/i.test(text) ||
    /\bnew\s+section\b/i.test(text)
  );
}

export function messageLooksStructural(message: string): boolean {
  return (
    messageLooksOutlineStructure(message) ||
    /\b(add|delete|remove|instead\s+of|replace|swap|change\s+.+\s+to|more\s+\d*\s*bio|new\s+section|team\s*bios?|case\s*stud)\b/i.test(
      message
    )
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
    /** Prior chat turns — used when the latest message is a short follow-up. */
    conversationHistory?: Array<{ role: string; content: string }> | null;
  }
): ChatTargetResolution | null {
  if (sections.length === 0) return null;
  const text = message.trim();
  const viewingId = options?.viewingSectionId ?? null;
  const pinned = options?.pinnedSection ?? null;

  if (!text) {
    const fallback =
      pinned ?? sections.find((s) => s.id === viewingId) ?? sections[0] ?? null;
    return fallback
      ? {
          kind: "resolved",
          section: fallback,
          confidence: pinned ? "high" : "medium",
          reason: pinned ? "pinned" : "viewing",
        }
      : null;
  }

  const lower = text.toLowerCase();
  const safeViewingId = pinnedSectionConflictsWithMessage(text, viewingId)
    ? null
    : viewingId;
  const viewing = sections.find((s) => s.id === safeViewingId) ?? null;

  // Pasted clarify option / "currently open in the UI" — before stale Improve pins.
  const fromClarify = resolveSectionFromClarifyReply(
    sections,
    text,
    options?.conversationHistory
  );
  if (fromClarify) {
    return {
      kind: "resolved",
      section: fromClarify,
      confidence: "high",
      reason: "clarify-reply",
    };
  }
  if (viewing && /\bcurrently\s+open(?:\s+in\s+the\s+ui)?\b/i.test(text)) {
    // Clarify options often say "(currently open in the UI)" — that is the open tab.
    return {
      kind: "resolved",
      section: viewing,
      confidence: "high",
      reason: "currently-open",
    };
  }

  const byTitle = [...sections].sort(
    (a, b) => (b.title?.length ?? 0) - (a.title?.length ?? 0)
  );

  // Named section ALWAYS beats pin/open tab — user said which part to patch.
  const titleHits = byTitle.filter((section) =>
    messageMentionsSectionTitle(text, section.title || "")
  );
  if (titleHits.length === 1) {
    return {
      kind: "resolved",
      section: titleHits[0],
      confidence: "high",
      reason: "title",
    };
  }
  if (titleHits.length > 1) {
    // Prefer unique topic headword when several long titles share tokens.
    const byTopic = resolveSectionByUniqueTopic(sections, text);
    if (byTopic && titleHits.some((s) => s.id === byTopic.id)) {
      return {
        kind: "resolved",
        section: byTopic,
        confidence: "high",
        reason: "title-topic",
      };
    }
    const ranked = [...titleHits].sort(
      (a, b) =>
        sectionTitleCore(b.title || "").length -
        sectionTitleCore(a.title || "").length
    );
    const top = ranked[0];
    const topLen = sectionTitleCore(top.title || "").length;
    const tied = ranked.filter(
      (s) => sectionTitleCore(s.title || "").length === topLen
    );
    if (tied.length === 1) {
      return {
        kind: "resolved",
        section: top,
        confidence: "high",
        reason: "title",
      };
    }
    return {
      kind: "clarify",
      candidates: titleHits,
      question: formatClarifyQuestion(text, titleHits),
    };
  }

  // §21 / "Fix 21" → title that starts with 21. (sidebar numbering)
  const byMark = resolveSectionByMarkNumber(sections, text);
  if (byMark) {
    return {
      kind: "resolved",
      section: byMark,
      confidence: "high",
      reason: "section-mark",
    };
  }

  // Client / case-study name (Umatilla, Rock the Locks) BEFORE unique-topic —
  // incidental "before the References fix" must not steal the case study.
  const clientNeedles = [
    ...new Set([
      ...(lower.match(
        /\b(?:umatilla|rock the locks|carbondale|maricopa|deschutes)\b/g
      ) || []),
      ...((text.match(
        /\bcity of [A-Za-z]+(?:\s+[A-Za-z]+){0,2}\b/gi
      ) || []) as string[]).map((n) => n.toLowerCase()),
    ]),
  ];
  if (clientNeedles.length > 0) {
    const clientHits = sections.filter((section) => {
      const core = sectionTitleCore(section.title || "").toLowerCase();
      const name = sectionPersonName(section.title || "").toLowerCase();
      const blob = `${core} ${name}`;
      return clientNeedles.some((n) => blob.includes(n));
    });
    const uniqueClient = [...new Map(clientHits.map((s) => [s.id, s])).values()];
    if (uniqueClient.length === 1) {
      return {
        kind: "resolved",
        section: uniqueClient[0],
        confidence: "high",
        reason: "client-name",
      };
    }
  }

  // "section 15" / "section 15 of 18" = manuscript ordinal — before topic/history.
  const byOrdinal = resolveSectionByOrdinal(sections, text);
  if (byOrdinal) {
    return {
      kind: "resolved",
      section: byOrdinal,
      confidence: "high",
      reason: "ordinal",
    };
  }

  // Unique "References" / "Pricing" tab — only when intentionally targeted.
  // Before pin so "fix References" isn't trapped on a stale Improve pin.
  const byTopic = resolveSectionByUniqueTopic(sections, text);
  if (byTopic) {
    return {
      kind: "resolved",
      section: byTopic,
      confidence: "high",
      reason: "unique-topic",
    };
  }

  // Active Improve / Revise pin — before chat history. A prior Client References
  // thread must not steal after the user pinned Monthly Schedules (or any tab).
  if (pinned && !messageLooksOutlineStructure(text)) {
    return {
      kind: "resolved",
      section: pinned,
      confidence: "high",
      reason: "pinned",
    };
  }

  // Short follow-up ("apply these") → prior chat named the section — but never
  // when the user said "this" while a different tab is open in the editor.
  const looksLikeShortFollowUp =
    text.length <= 120 ||
    /^(?:apply|do\s+it|yes|ok|please|go\s+ahead)\b/i.test(text);
  const latestNamesSection =
    /(?:§|sec(?:tion)?\.?)\s*\d+\b/i.test(text) ||
    /\b(?:umatilla|rock\s+the\s+locks|case\s+stud|references?)\b/i.test(text);
  const thisMeansOpenTab =
    Boolean(viewing) &&
    /\b(?:is\s+this|this\s+(?:accurate|correct|right|complete|enough)|cross[\s-]?verify\s+(?:this|it))\b/i.test(
      text
    );
  if (thisMeansOpenTab && viewing) {
    return {
      kind: "resolved",
      section: viewing,
      confidence: "high",
      reason: "viewing-this",
    };
  }
  const fromHistory =
    looksLikeShortFollowUp &&
    !latestNamesSection &&
    !messagePointsAtOpenSection(text)
      ? resolveSectionFromConversationHistory(
          sections,
          options?.conversationHistory
        )
      : null;
  if (fromHistory && !messageLooksOutlineStructure(text)) {
    return {
      kind: "resolved",
      section: fromHistory,
      confidence: "high",
      reason: "chat-history",
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

  // Add/create/delete sidebar sections (no specific name above): proposal-wide.
  // Bypass open-tab / case-study clarify — backend structure planner owns this.
  if (messageLooksOutlineStructure(text)) {
    const passthrough = viewing ?? sections[0] ?? null;
    if (!passthrough) return null;
    return {
      kind: "resolved",
      section: passthrough,
      confidence: "high",
      reason: "outline-structure",
    };
  }

  // Whole-proposal review / gaps / apply-fixes: do not treat as "open tab only".
  if (messageLooksProposalWide(text)) {
    const passthrough = viewing ?? sections[0] ?? null;
    if (!passthrough) return null;
    return {
      kind: "resolved",
      section: passthrough,
      confidence: "high",
      reason: "proposal-wide",
    };
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

  // Explicit "this section" / "here" → open tab. Random browsing does NOT count.
  if (viewing && messagePointsAtOpenSection(text)) {
    return {
      kind: "resolved",
      section: viewing,
      confidence: "high",
      reason: "viewing-explicit",
    };
  }

  // No name, no pin, no "this section" → ask. Do not silently bind the open tab.
  const top = sections.filter((s) => s.content?.trim()).slice(0, 6);
  const candidates = top.length > 0 ? top : sections.slice(0, 6);
  return {
    kind: "clarify",
    candidates,
    question:
      "Which section should I work on?\n\n" +
      candidates.map((s, i) => `${i + 1}. **${s.title}**`).join("\n") +
      "\n\nReply with the title, say **this section**, or pin with **Revise content** / **Improve full section**.",
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
