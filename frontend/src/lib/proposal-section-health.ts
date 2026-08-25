/**
 * Decide whether a proposal section is genuinely drafted or a dead placeholder.
 *
 * Mirror of `backend/app/services/proposal_section_health.py`. Both read the same
 * fixture (`backend/tests/fixtures/section_health_cases.json`) in their test
 * suites, so they cannot drift apart silently.
 *
 * Phase 3 writes a short `[VERIFY: ...]` stub when a section cannot be drafted.
 * The stub is non-empty, so a bare `content.trim()` check reports it as drafted —
 * which is why the sidebar ticked failed sections and the header read "16/16
 * drafted" while three sections held no content at all.
 */

export type SectionHealth =
  | "draft_failed"
  | "no_evidence"
  | "placeholder_only"
  | "empty";

// Separator between the two halves of the failure sentinel. Stored drafts contain
// both an em dash (current constant) and a comma (older writer), so accept any run
// of punctuation or whitespace. The trailing "-" is literal inside the class.
const DRAFT_FAILED_RE =
  /^\[\s*VERIFY:\s*section\s+drafting\s+failed[\s,;:—–-]+needs\s+manual\s+regeneration\s*\]$/i;

const NO_EVIDENCE_RE =
  /^\[\s*VERIFY:\s*draft\s+content\s+for\s+[\s\S]+?(?:insufficient\s+evidence\s+in\s+corpus|writer\s+returned\s+empty\s+prose)/i;

/**
 * Return `text` when it is exactly one bracketed tag and nothing else.
 *
 * This is the rule that protects real work. Drafted sections routinely contain
 * inline `[VERIFY: ...]` chips; only a section whose *entire* body is a single tag
 * is a placeholder. Anything followed by prose is a real draft.
 *
 * A tag body containing its own "]" fails this check and is reported as drafted —
 * deliberately the safe direction, since missing a dead section costs a refusal
 * while a false positive would overwrite finished content.
 */
function wholeBodyTag(text: string): string | null {
  if (!text.startsWith("[")) return null;
  if (text.indexOf("]") !== text.length - 1) return null;
  return text;
}

const PLACEHOLDER_TAG_RE =
  /\[(?:MANUAL\s+FILL|VERIFY|PLACEHOLDER|INSERT|TBD)\b[^\]]*\]/i;
const HEADING_RE = /^\s{0,3}#{1,6}\s/;
/** A line that is entirely bold ("**Challenge**") is a label, not prose. */
const BOLD_LABEL_RE = /^\*\*[^*]+\*\*[:.]?$/;

/** Words a line needs before it counts as prose rather than a label. */
const MIN_PROSE_WORDS_PER_LINE = 4;

/**
 * True when the body carries a markdown heading or a bold label line.
 *
 * A dead section still has its skeleton ("### Title", "**Challenge**"). A terse
 * but real line such as "Role: [MANUAL FILL: Title]" has neither, and must not be
 * regenerated — it is legitimately awaiting a value.
 */
function hasSectionLabel(text: string): boolean {
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (HEADING_RE.test(line) || BOLD_LABEL_RE.test(line)) return true;
  }
  return false;
}

/**
 * True when at least one line carries real prose.
 *
 * Structural rather than length-based: counting words across the whole body
 * misclassified short but genuine sections — an 11-word acknowledgment with two
 * inline [VERIFY] chips looked identical to a stub — and a false positive here
 * means regenerating over finished work.
 */
function hasProseLine(text: string): boolean {
  for (const rawLine of text.split("\n")) {
    const line = rawLine.replace(new RegExp(PLACEHOLDER_TAG_RE, "gi"), " ").trim();
    if (!line) continue;
    if (HEADING_RE.test(line) || BOLD_LABEL_RE.test(line)) continue;
    const words = line
      .replace(/[*_`>#|~-]/g, " ")
      .split(/\s+/)
      .filter((w) => /[A-Za-z0-9]/.test(w));
    if (words.length >= MIN_PROSE_WORDS_PER_LINE) return true;
  }
  return false;
}

/** Classify a section body. `null` means the section holds a real draft. */
export function classifySectionHealth(
  content: string | null | undefined
): SectionHealth | null {
  const text = (content ?? "").trim();
  if (!text) return "empty";

  const tag = wholeBodyTag(text);
  if (tag !== null) {
    if (DRAFT_FAILED_RE.test(tag)) return "draft_failed";
    if (NO_EVIDENCE_RE.test(tag)) return "no_evidence";
    return null;
  }

  // A skeleton: section headings, placeholder tags, and no prose between them.
  // All three conditions are required — dropping the heading check made
  // "Role: [MANUAL FILL: Title]" look dead when it is a real line awaiting a value.
  if (
    hasSectionLabel(text) &&
    PLACEHOLDER_TAG_RE.test(text) &&
    !hasProseLine(text)
  ) {
    return "placeholder_only";
  }
  return null;
}

/** True when the section holds no usable draft and should be regenerated. */
export function isDeadSection(content: string | null | undefined): boolean {
  return classifySectionHealth(content) !== null;
}

/** True when the section holds real drafted content. */
export function isSectionDrafted(content: string | null | undefined): boolean {
  return classifySectionHealth(content) === null;
}

const DRAFT_STUB_MARKER = "draft this rfp-required section";

function isStubChromeLine(line: string): boolean {
  const cf = line.toLowerCase().trim();
  return (
    cf.startsWith("rfp-required outline") ||
    cf.startsWith("rfp required outline") ||
    cf.startsWith("rfp instructions") ||
    cf.startsWith("evaluation weight") ||
    cf.includes(DRAFT_STUB_MARKER)
  );
}

function normalizeTitleEcho(text: string): string {
  let plain = text.trim();
  while (plain.startsWith("#")) {
    plain = plain.slice(1).trimStart();
  }
  let i = 0;
  while (i < plain.length && "0123456789.".includes(plain[i] ?? "")) {
    i += 1;
  }
  plain = plain.slice(i).trim();
  // Drop a leading separator left between the number and the label (em/en dash,
  // hyphen, comma, colon, mid-dot) so "1.1 — Who We Are" and "# 1.1, Who We Are"
  // normalize identically — otherwise the redundant title-echo heading is not
  // recognized and shows as literal "#" markdown in the editor.
  let j = 0;
  while (j < plain.length && "—–-,:·  ".includes(plain[j] ?? "")) {
    j += 1;
  }
  plain = plain
    .slice(j)
    .toLowerCase()
    .replaceAll("&", "and");
  return plain.split(" ").filter(Boolean).join(" ");
}

/**
 * True when a SHORT section is nonetheless complete — it carries real data, not
 * an unfilled heading stub. A contact block ("**Name**", "CEO | agency",
 * "Phone: …", "Email: …") or a checkbox attestation has few prose words but is
 * finished. The signal is structural (it never looks at the section title):
 *  - no unfilled placeholder tag anywhere, AND
 *  - at least two non-heading lines that carry real (alphanumeric) content.
 * A genuine stub is a bare heading (0–1 content lines) or holds a placeholder,
 * so it still reads as thin.
 */
function isFilledShortSection(lines: string[], body: string): boolean {
  if (PLACEHOLDER_TAG_RE.test(body)) return false;
  let contentLines = 0;
  for (const line of lines) {
    if (HEADING_RE.test(line)) continue;
    if (/[A-Za-z0-9]/.test(line.replace(/[*_`>#|~-]/g, " "))) contentLines += 1;
  }
  return contentLines >= 2;
}

/** Heading-only / outline-stub RFP tabs that look "drafted" because they are non-empty. */
export function isThinUnfilledShell(
  content: string | null | undefined,
  title = "",
): boolean {
  const body = (content ?? "").trim();
  if (!body) return true;
  if (body.toLowerCase().includes(DRAFT_STUB_MARKER)) return true;
  const titleEcho = normalizeTitleEcho(title);
  const keep: string[] = [];
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (isStubChromeLine(line)) continue;
    if (titleEcho && normalizeTitleEcho(line) === titleEcho) continue;
    keep.push(line);
  }
  const words = keep.join(" ").split(" ").filter((w) => {
    if (!w) return false;
    for (const ch of w) {
      if (
        (ch >= "0" && ch <= "9") ||
        (ch >= "a" && ch <= "z") ||
        (ch >= "A" && ch <= "Z")
      ) {
        return true;
      }
    }
    return false;
  });
  if (words.length >= 12) return false;
  // Short but COMPLETE: a contact block / checkbox attestation has few prose
  // words but real data and no unfilled placeholders — count it as drafted,
  // not an unfilled heading stub.
  if (isFilledShortSection(keep, body)) return false;
  return true;
}

/**
 * Drop a leading body line that only echoes the section's own title (e.g. body
 * starting with "## 2. Foo" when the section title is "2. Foo"). Generation
 * sometimes repeats the heading inside the body, which is redundant next to the
 * article's own title and, in a raw-markdown editor, shows as literal "##" text.
 */
export function stripLeadingTitleEcho(content: string, title: string): string {
  const titleEcho = normalizeTitleEcho(title);
  if (!titleEcho) return content;
  const lines = content.split("\n");
  let start = 0;
  while (start < lines.length && (lines[start] ?? "").trim() === "") start += 1;
  const firstLine = (lines[start] ?? "").trim();
  if (!firstLine.startsWith("#")) return content;
  if (normalizeTitleEcho(firstLine) !== titleEcho) return content;
  let end = start + 1;
  while (end < lines.length && (lines[end] ?? "").trim() === "") end += 1;
  return lines.slice(end).join("\n");
}

/** Sidebar / progress: RFP tabs that are only a title or draft-stub are not drafted. */
export function isManuscriptSectionDrafted(section: {
  id?: string;
  title?: string;
  content?: string | null;
}): boolean {
  if (!isSectionDrafted(section.content)) return false;
  const id = section.id ?? "";
  if (!id.startsWith("rfp-")) return true;
  return !isThinUnfilledShell(section.content, section.title ?? "");
}

/** Short, human-readable reason a section is not drafted. For tooltips. */
export function deadSectionLabel(health: SectionHealth): string {
  switch (health) {
    case "draft_failed":
      return "Drafting failed — ask the assistant to regenerate";
    case "no_evidence":
      return "No evidence found — ask the assistant to regenerate";
    case "placeholder_only":
      return "Only placeholders — ask the assistant to write this section";
    case "empty":
      return "Not drafted yet";
  }
}
