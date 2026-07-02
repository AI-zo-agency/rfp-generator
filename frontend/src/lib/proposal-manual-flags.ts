import { findBudgetSection } from "@/lib/proposal-budget-content";
import type { ProposalBudget, ProposalOutline, RfpSectionMap } from "@/types/proposal";

/**
 * Bracket tags that must be filled or confirmed before submission.
 * Mirrors backend proposal_presubmit_review._PLACEHOLDER_RE — no AI.
 */
export const MANUAL_FILL_TAG_RE =
  /\[(?:VERIFY|FLAG|DESIGNER NOTE|TBD|INSERT|PLACEHOLDER|MANUAL FILL)[^\]]*\]/gi;

const DEFER_RE =
  /\b(?:upon request|on request|will be provided|provided in attachment|supplemental attachment|on file with|attachment\s*\d+|available upon|furnished upon|provided separately|contact on request)\b/i;

const PLACEHOLDER_TAG_RE =
  /\[(?:PLACEHOLDER|VERIFY|TBD|INSERT)[^\]]*\]|\bTBD\b|_{3,}/i;

const FEIN_RE = /\b\d{2}-\d{7}\b/;

const INSURANCE_RFP_RE =
  /\b(?:ACORD|certificate of insurance|COI|general liability|professional liability|umbrella|cyber)\b/i;

const INSURANCE_LIMIT_RE =
  /\$\s*[\d,]+(?:\.\d+)?\s*(?:million|m\b)?|\b\d+\s*million\b/i;

const NJ_REFERENCE_RFP_RE =
  /\bnew\s+jersey\b.{0,80}(?:reference|prior work|college|university|public)|\bNJ\b.{0,40}(?:reference|college|community college|public entity)/i;

const PRICING_FLAG_MANUSCRIPT_RE = /\[PRICING\s+FLAG|##\s*Pricing\s+Flags/i;

const PM_LINE_RE =
  /\bproject\s+management\b|\baccount\s+management\b|\bprogram\s+management\b/i;

const REQ_STOPWORDS = new Set([
  "the",
  "and",
  "for",
  "with",
  "that",
  "this",
  "from",
  "shall",
  "must",
  "will",
  "have",
  "been",
  "provide",
  "include",
  "submit",
  "proposal",
  "offeror",
  "vendor",
  "contractor",
  "services",
  "required",
  "agency",
]);

const PHONE_RE =
  /(?:\(\d{3}\)\s*\d{3}[-.\s]?\d{4}|\d{3}[-.\s]\d{3}[-.\s]\d{4}|phone[:\s]+\(?\d{3}\)?[-.\s]?\d{3}[-.\s]\d{4})/i;

const EMAIL_RE = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/;

const HOURS_RE =
  /\b\d+(?:\.\d+)?\s*(?:hours|hrs)\b|\bhours\s*(?:per|by|×|x|\*)\b|staff\s+hours/i;

const HOURS_TABLE_RE =
  /\|\s*(?:hours|hrs|hourly)\s*\||\bhours\s*(?:×|x|\*|per)\b|staff\s+hours\s+table/i;

const FEMALE_PCT_RE =
  /(?:female|women).{0,50}?(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*(?:female|women)/gi;

const PSA_ACK_TERMS: Array<{ label: string; pattern: RegExp }> = [
  { label: "MacBride Principles", pattern: /\bmacbride\b/i },
  {
    label: "Workers' Compensation",
    pattern: /\bworkers['\u2019\s]*compensation\b|\bdisability\s+benefit/i,
  },
  { label: "Living Wage", pattern: /\bliving\s+wage\b/i },
  { label: "Title VI / Civil Rights", pattern: /\btitle\s*vi\b|\bcivil\s+rights\b/i },
  {
    label: "Chapter 63 / criminal history",
    pattern: /\bchapter\s*63\b|criminal\s+history/i,
  },
  { label: "Independent contractor", pattern: /\bindependent\s+contractor\b/i },
  { label: "Audit rights", pattern: /\baudit\s+rights?\b|\b(?:three|3)[\s-]*year.{0,20}audit\b/i },
  {
    label: "NY-admitted insurance",
    pattern: /\bnew\s+york[\s-]*admitted\b|\bny[\s-]*admitted\b/i,
  },
  { label: "General liability insurance", pattern: /\bgeneral\s+liability\b/i },
];

export type ManualFillFlagKind =
  | "verify"
  | "placeholder"
  | "manual_fill"
  | "other"
  | "compliance"
  | "budget"
  | "consistency";

export interface ManualFillFlag {
  sectionId: string;
  sectionTitle: string;
  kind: ManualFillFlagKind;
  /** Bracket tag or human-readable gap description */
  tag: string;
  /** Substring in section content to scroll to and highlight when jumping from the panel */
  highlightText?: string;
  owner?: string | null;
  finalized?: boolean;
  kbSearched?: boolean;
}

export interface FlagHighlightRange {
  start: number;
  end: number;
  text: string;
}

export interface SubmissionFlagScanOptions {
  budget?: ProposalBudget | null;
  rfpTitle?: string;
  rfpClient?: string;
  rfpSections?: RfpSectionMap[];
}

function classifyManualFillTag(tag: string): ManualFillFlagKind {
  const upper = tag.toUpperCase();
  if (upper.startsWith("[MANUAL FILL")) return "manual_fill";
  if (upper.startsWith("[VERIFY")) return "verify";
  if (
    upper.startsWith("[PLACEHOLDER") ||
    upper.startsWith("[INSERT") ||
    upper.startsWith("[TBD")
  ) {
    return "placeholder";
  }
  return "other";
}

function sectionByTitlePatterns(
  outline: ProposalOutline,
  ...patterns: string[]
): (typeof outline.sections)[number] | undefined {
  for (const section of outline.sections) {
    const title = (section.title ?? "").toLowerCase();
    if (patterns.some((p) => title.includes(p))) {
      return section;
    }
  }
  return undefined;
}

function flag(
  section: { id: string; title: string },
  kind: ManualFillFlagKind,
  tag: string,
  highlightText?: string
): ManualFillFlag {
  return {
    sectionId: section.id,
    sectionTitle: section.title,
    kind,
    tag,
    highlightText,
  };
}

function lineContaining(text: string, pattern: RegExp): string | undefined {
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed && pattern.test(trimmed)) return trimmed;
  }
  return undefined;
}

function firstRegexMatch(text: string, pattern: RegExp): string | undefined {
  const re = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`);
  const match = re.exec(text);
  return match?.[0];
}

function extractFemalePercents(text: string): number[] {
  const values: number[] = [];
  const re = new RegExp(FEMALE_PCT_RE.source, FEMALE_PCT_RE.flags);
  for (const match of text.matchAll(re)) {
    const raw = match[1] || match[2];
    if (!raw) continue;
    const n = Number.parseFloat(raw);
    if (!Number.isNaN(n)) values.push(n);
  }
  return values;
}

function isCommissionStyleBudget(budget: ProposalBudget): boolean {
  if (budget.commissionModel?.trim()) return true;
  if (budget.commissionRate != null && budget.commissionRate > 0) return true;
  if ((budget.clientMediaPassthrough ?? 0) > 0) return true;
  return false;
}

function deriveCommissionRevenue(budget: ProposalBudget): number | null {
  const rate = budget.commissionRate;
  const passthrough = budget.clientMediaPassthrough;
  if (rate == null || !passthrough || passthrough <= 0) return null;
  const r = rate > 1 ? rate / 100 : rate;
  if (r <= 0) return null;
  return Math.round(passthrough * r * 100) / 100;
}

function requirementLikelyCovered(req: string, manuscript: string): boolean {
  const tokens = req
    .toLowerCase()
    .match(/[a-z]{5,}/g)
    ?.filter((t) => !REQ_STOPWORDS.has(t))
    .slice(0, 8);
  if (!tokens?.length) return true;
  const blob = manuscript.toLowerCase();
  const hits = tokens.filter((t) => blob.includes(t)).length;
  return hits >= Math.max(2, Math.ceil(tokens.length / 2));
}

function rfpBlob(options?: SubmissionFlagScanOptions): string {
  const parts = [options?.rfpTitle ?? "", options?.rfpClient ?? ""];
  for (const section of options?.rfpSections ?? []) {
    parts.push(section.title ?? "");
    parts.push(...(section.requirements ?? []));
  }
  return parts.join(" ");
}

function psaScanRequired(options?: SubmissionFlagScanOptions): boolean {
  const client = (options?.rfpClient ?? "").toLowerCase();
  const title = (options?.rfpTitle ?? "").toLowerCase();
  if (client.includes("rochester") || title.includes("rochester")) return true;
  return /\bprofessional\s+services\s+agreement\b|\bpsa\b/i.test(
    `${options?.rfpTitle ?? ""} ${options?.rfpClient ?? ""}`
  );
}

/** Scan one section body for manual fill-in tags. */
export function scanManualFillFlagsInText(
  text: string,
  section: { id: string; title: string }
): ManualFillFlag[] {
  if (!text?.trim()) return [];

  const flags: ManualFillFlag[] = [];
  const re = new RegExp(MANUAL_FILL_TAG_RE.source, MANUAL_FILL_TAG_RE.flags);

  for (const match of text.matchAll(re)) {
    const tag = match[0];
    if (!tag) continue;
    flags.push({
      sectionId: section.id,
      sectionTitle: section.title,
      kind: classifyManualFillTag(tag),
      tag,
      highlightText: tag,
    });
  }

  return flags;
}

/** Bracket tags only — legacy helper. */
export function scanManualFillFlags(outline: ProposalOutline): ManualFillFlag[] {
  return outline.sections.flatMap((section) =>
    scanManualFillFlagsInText(section.content ?? "", section)
  );
}

function rfpRequiresStaffHoursTable(options?: SubmissionFlagScanOptions): boolean {
  const blob = rfpBlob(options).toLowerCase();
  return /\b(?:staff\s+hours|hours\s+per\s+task|itemized\s+hours|hours\s+by\s+task|billing\s+rates?\s+by\s+role|hours\s+and\s+rates|labor\s+hours)\b/.test(
    blob
  );
}

/** Fee section already documents compensation — no extra hours table required. */
function feeStructureHasBillingDetail(content: string): boolean {
  if (HOURS_RE.test(content) || HOURS_TABLE_RE.test(content)) return true;
  if (
    /\b(?:hourly|blended)\s+rate|\brate\s+card\b|\bfee\s+schedule\b|\bbilling\s+rates?\b/i.test(
      content
    )
  ) {
    return true;
  }
  if (/\|[^\n]*(?:hour|hrs|rate|role)[^\n]*\|/i.test(content)) return true;
  // Commission-model transparency (rate split + %) satisfies many RFPs without an hours grid.
  if (/\bcommission\b/i.test(content) && /\d+\s*%/i.test(content)) return true;
  return false;
}

function feeSectionShowsAgencyCompensation(content: string): boolean {
  if (feeStructureHasBillingDetail(content)) return true;
  return (
    /\$\s*[\d,]+(?:\.\d{2})?/.test(content) &&
    /\b(?:agency|commission|fee|management|revenue)\b/i.test(content)
  );
}

function manuscriptShowsZeroAgencyRevenue(content: string): boolean {
  if (/\bAgency revenue estimate\b[^\n]*\$0\b/i.test(content)) return true;
  if (/##\s*Budget Summary[\s\S]{0,500}\$0\b/i.test(content)) return true;
  return false;
}

function scanReferenceContactsInSection(
  section: { id: string; title: string },
  content: string
): ManualFillFlag[] {
  const placeholder = firstRegexMatch(content, PLACEHOLDER_TAG_RE);
  if (placeholder) {
    return [
      flag(
        section,
        "placeholder",
        "References: [PLACEHOLDER] or [VERIFY] tags must be replaced with full contact details",
        placeholder
      ),
    ];
  }

  if (MANUAL_FILL_TAG_RE.test(content)) return [];

  const hasPhone = PHONE_RE.test(content);
  const hasEmail = EMAIL_RE.test(content);
  const defers = DEFER_RE.test(content);
  const refMentions = (content.match(/\breference\b/gi) ?? []).length;

  if (hasPhone && hasEmail && !defers) return [];
  if (refMentions >= 2 && hasPhone && !defers) return [];

  const parts: string[] = [];
  if (defers) parts.push("'contact on request' / defer language");
  if (!hasPhone) parts.push("missing phone number(s)");
  if (!hasEmail) parts.push("missing email address(es)");

  const deferLine =
    lineContaining(content, DEFER_RE) ??
    lineContaining(content, /\breference\b/i) ??
    lineContaining(content, /contact on request/i);

  return [
    flag(
      section,
      "compliance",
      `References: RFP requires name, title, phone, and email in proposal — ${parts.join("; ")}`,
      deferLine
    ),
  ];
}

function scanReferenceContactFlags(outline: ProposalOutline): ManualFillFlag[] {
  const dedicated = sectionByTitlePatterns(outline, "reference");
  if (dedicated?.content?.trim()) {
    return scanReferenceContactsInSection(dedicated, dedicated.content);
  }

  const fallback = sectionByTitlePatterns(
    outline,
    "qualification",
    "past performance",
    "experience"
  );
  if (!fallback?.content?.trim()) return [];
  return scanReferenceContactsInSection(fallback, fallback.content);
}

function scanBudgetHoursFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  if (!rfpRequiresStaffHoursTable(options)) return [];

  const section = findBudgetSection(outline.sections);
  if (!section?.content?.trim()) return [];

  const content = section.content;
  if (feeStructureHasBillingDetail(content)) return [];

  const highlight =
    lineContaining(content, /##\s*Budget Summary/i) ??
    lineContaining(content, /##\s*(?:Staff|Hours|Billing|Labor|Rate|Compensation|Fee)/i) ??
    lineContaining(content, /\[DESIGNER NOTE/i) ??
    content.split("\n").find((line) => /^##\s/.test(line.trim()))?.trim();

  return [
    flag(
      section,
      "compliance",
      "Budget: missing staff hours / billing rates table (RFP requires itemized hours by task)",
      highlight
    ),
  ];
}

function scanBudgetRevenueFlags(
  outline: ProposalOutline,
  budget?: ProposalBudget | null
): ManualFillFlag[] {
  const section = findBudgetSection(outline.sections);
  if (!section) return [];

  const sectionContent = section.content ?? "";

  // Manuscript already states agency compensation — don't flag internal refinery field drift.
  if (feeSectionShowsAgencyCompensation(sectionContent)) return [];

  const revenue = budget?.agencyRevenueEstimate ?? 0;
  const derived = budget ? deriveCommissionRevenue(budget) : null;
  if (revenue > 0 || (derived != null && derived > 0 && Math.abs(revenue - derived) < 1)) {
    return [];
  }

  const commission = budget ? isCommissionStyleBudget(budget) : false;
  const lump = budget?.lumpSumTotal ?? 0;
  if (!commission && lump <= 0) return [];

  // Only flag when the manuscript budget summary itself shows $0 agency revenue.
  if (!manuscriptShowsZeroAgencyRevenue(sectionContent)) return [];

  const highlight =
    lineContaining(sectionContent, /##\s*Budget Summary/i) ??
    lineContaining(sectionContent, /Agency revenue estimate/i) ??
    lineContaining(sectionContent, /\$0/);

  const message = commission
    ? "Budget Summary shows $0 agency revenue for commission model — set agencyRevenueEstimate from rate × pass-through"
    : "Budget Summary shows $0 agency revenue — reconcile agencyRevenueEstimate with budget line items";

  return [flag(section, "budget", message, highlight)];
}

function scanWorkforceConsistencyFlags(outline: ProposalOutline): ManualFillFlag[] {
  const mwbe = sectionByTitlePatterns(outline, "mwbe", "diversity", "workforce");
  const personnel = sectionByTitlePatterns(
    outline,
    "personnel",
    "team",
    "staff",
    "project personnel",
    "section 2"
  );
  if (!mwbe?.content?.trim() || !personnel?.content?.trim()) return [];

  const mwbePcts = extractFemalePercents(mwbe.content);
  const personnelPcts = extractFemalePercents(personnel.content);
  if (!mwbePcts.length || !personnelPcts.length) return [];

  for (const mwbePct of mwbePcts) {
    for (const personnelPct of personnelPcts) {
      if (Math.abs(mwbePct - personnelPct) > 0.5) {
        const highlight =
          firstRegexMatch(mwbe.content, FEMALE_PCT_RE) ??
          `${mwbePct}%`;
        return [
          flag(
            mwbe,
            "consistency",
            `Female % mismatch: ${mwbe.title} says ${mwbePct}% but ${personnel.title} says ${personnelPct}% — use one precise figure (e.g. ${personnelPct}% from headcount)`,
            highlight
          ),
        ];
      }
    }
  }
  return [];
}

function scanPsaAckFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  if (!psaScanRequired(options)) return [];

  const target =
    sectionByTitlePatterns(outline, "qualification") ??
    sectionByTitlePatterns(outline, "project statement", "company overview") ??
    outline.sections.find((s) => s.content?.trim());

  if (!target) return [];

  const manuscript = outline.sections.map((s) => s.content ?? "").join("\n\n");
  const missing = PSA_ACK_TERMS.filter((term) => !term.pattern.test(manuscript)).map(
    (term) => term.label
  );

  if (missing.length < 3) return [];

  const content = target.content ?? "";
  const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
  const highlight =
    lineContaining(content, /##\s*qualification/i) ??
    lines[lines.length - 1];

  const missingSummary = missing.slice(0, 4).join(", ");
  const extraCount = missing.length > 4 ? ` (+${missing.length - 4} more)` : "";

  return [
    flag(
      target,
      "compliance",
      `PSA compliance: missing acknowledgments for ${missingSummary}${extraCount} — add consolidated PSA paragraph to Qualifications`,
      highlight
    ),
  ];
}

function scanInsuranceFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  if (!INSURANCE_RFP_RE.test(rfpBlob(options))) return [];

  const section =
    sectionByTitlePatterns(outline, "insurance", "certificate", "bonding", "liability") ??
    outline.sections.find((s) => INSURANCE_RFP_RE.test(s.content ?? ""));

  const content =
    section?.content?.trim() ||
    outline.sections.map((s) => s.content ?? "").join("\n\n");
  if (!content.trim()) return [];

  const hasLimits = INSURANCE_LIMIT_RE.test(content);
  const hasPlaceholder = PLACEHOLDER_TAG_RE.test(content);
  if (hasLimits && !hasPlaceholder) return [];

  const target =
    section ?? outline.sections.find((s) => s.content?.trim()) ?? outline.sections[0];
  if (!target) return [];

  const highlight =
    lineContaining(content, /insurance/i) ??
    lineContaining(content, /liability/i) ??
    firstRegexMatch(content, PLACEHOLDER_TAG_RE);

  return [
    flag(
      target,
      "compliance",
      "Insurance: include RFP limits table (requires | current policy | gap | bind action) — no placeholders alone",
      highlight
    ),
  ];
}

function scanQuestionnaireFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  const section = sectionByTitlePatterns(
    outline,
    "questionnaire",
    "vendor",
    "contractor",
    "offeror information",
    "business entity"
  );
  const blob = rfpBlob(options);
  const rfpRequires = /\b(?:questionnaire|vendor information|contractor\/vendor)\b/i.test(blob);

  if (!section?.content?.trim()) {
    if (!rfpRequires) return [];
    const target = outline.sections[0];
    if (!target) return [];
    return [
      flag(
        target,
        "compliance",
        "Vendor/contractor questionnaire section missing — RFP requires completed form",
        undefined
      ),
    ];
  }

  const content = section.content;
  const flags: ManualFillFlag[] = [];

  const placeholder = firstRegexMatch(content, PLACEHOLDER_TAG_RE);
  if (placeholder) {
    flags.push(
      flag(
        section,
        "verify",
        "Questionnaire: replace [VERIFY]/TBD/blank fields with FEIN, phones, email, DUNS/CAGE",
        placeholder
      )
    );
  }

  if (rfpRequires && !FEIN_RE.test(content)) {
    flags.push(
      flag(
        section,
        "verify",
        "Questionnaire: missing FEIN/EIN — confirm with Sonja",
        lineContaining(content, /FEIN|EIN|tax/i)
      )
    );
  }

  if (rfpRequires && !EMAIL_RE.test(content)) {
    flags.push(
      flag(
        section,
        "verify",
        "Questionnaire: missing primary business email",
        lineContaining(content, /email/i)
      )
    );
  }

  return flags;
}

function scanNjReferenceFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  if (!NJ_REFERENCE_RFP_RE.test(rfpBlob(options))) return [];

  const section = sectionByTitlePatterns(
    outline,
    "qualification",
    "reference",
    "past performance",
    "experience"
  );
  if (!section?.content?.trim()) return [];

  const content = section.content;
  const hasNj = /\bnew\s+jersey\b|hudson\s+county|NJ\s+(?:public|college)/i.test(content);
  const placeholders = PLACEHOLDER_TAG_RE.test(content);
  const defers = DEFER_RE.test(content);

  if (hasNj && !placeholders && !defers) return [];

  const highlight =
    firstRegexMatch(content, PLACEHOLDER_TAG_RE) ??
    lineContaining(content, DEFER_RE) ??
    lineContaining(content, /\breference\b/i);

  return [
    flag(
      section,
      "compliance",
      "NJ references: RFP requires NJ public-entity/college contacts — use verified KB refs with honest geography disclosure",
      highlight
    ),
  ];
}

function scanUncoveredRequirementFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  const mapped = options?.rfpSections ?? [];
  if (!mapped.length) return [];

  const manuscript = outline.sections.map((s) => s.content ?? "").join("\n\n");
  const sectionByTitle = new Map(
    outline.sections.map((s) => [(s.title ?? "").trim().toLowerCase(), s])
  );
  const flags: ManualFillFlag[] = [];

  for (const mappedSection of mapped) {
    const uncovered = mappedSection.uncoveredRequirements ?? [];
    if (!uncovered.length) continue;

    const titleKey = (mappedSection.title ?? "").trim().toLowerCase();
    const section = sectionByTitle.get(titleKey);
    if (!section) continue;

    const content = section.content ?? "";
    if (MANUAL_FILL_TAG_RE.test(content)) continue;

    for (const req of uncovered.slice(0, 3)) {
      if (requirementLikelyCovered(req, content) || requirementLikelyCovered(req, manuscript)) {
        continue;
      }
      flags.push(
        flag(
          section,
          "compliance",
          `Uncovered RFP requirement may still be missing: ${req.slice(0, 100)}`,
          lineContaining(content, new RegExp(req.slice(0, 24).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"))
        )
      );
    }
  }

  return flags.slice(0, 8);
}

function scanPricingFlagManuscript(
  outline: ProposalOutline,
  budget?: ProposalBudget | null
): ManualFillFlag[] {
  const section = findBudgetSection(outline.sections);
  if (!section?.content?.trim()) return [];

  const flags: ManualFillFlag[] = [];
  const content = section.content;

  if (PRICING_FLAG_MANUSCRIPT_RE.test(content)) {
    flags.push(
      flag(
        section,
        "budget",
        "Budget section contains internal Pricing Flags — regenerate budget after resolving with Sonja",
        lineContaining(content, PRICING_FLAG_MANUSCRIPT_RE)
      )
    );
  }

  if (budget?.pricingFlags?.length) {
    for (const pf of budget.pricingFlags.slice(0, 2)) {
      flags.push(
        flag(
          section,
          "budget",
          `Unresolved pricing flag: ${pf.slice(0, 100)}`,
          undefined
        )
      );
    }
  }

  return flags;
}

function scanPmRatioFlags(
  outline: ProposalOutline,
  budget?: ProposalBudget | null
): ManualFillFlag[] {
  if (!budget?.lineItems?.length) return [];

  const section = findBudgetSection(outline.sections);
  if (!section) return [];

  const agencyBase = budget.agencyFeeSubtotal ?? 0;
  if (agencyBase <= 0) return [];

  let pmTotal = 0;
  for (const item of budget.lineItems) {
    const blob = `${item.category ?? ""} ${item.description ?? ""} ${item.roleTitle ?? ""}`;
    if (PM_LINE_RE.test(blob)) {
      pmTotal += item.extended ?? 0;
    }
  }
  if (pmTotal <= 0) return [];

  const ratio = pmTotal / agencyBase;
  if (ratio >= 0.05 && ratio <= 0.08) return [];

  return [
    flag(
      section,
      "budget",
      `PM ratio ${(ratio * 100).toFixed(1)}% of agency fees — guide targets 5–8%; adjust L01/L12 with Sonja`,
      undefined
    ),
  ];
}

function dedupeFlags(flags: ManualFillFlag[]): ManualFillFlag[] {
  const seen = new Set<string>();
  const out: ManualFillFlag[] = [];
  for (const f of flags) {
    const key = `${f.sectionId}::${f.kind}::${f.tag}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(f);
  }
  return out;
}

/** Merge finalized flags from pre-submit review with live manuscript scan. */
export function mergeSubmissionFlags(
  scanned: ManualFillFlag[],
  fromReview?: ManualFillFlag[] | null
): ManualFillFlag[] {
  const reviewFlags = (fromReview ?? []).map((f) => ({
    ...f,
    finalized: f.finalized ?? true,
    kbSearched: f.kbSearched ?? true,
  }));

  if (!reviewFlags.length) {
    return dedupeFlags(scanned);
  }

  // Live scan is always source of truth — stale Phase 4 flags drop when content is fixed.
  const reviewByKey = new Map(
    reviewFlags.map((f) => [`${f.sectionId}::${f.kind}::${f.tag}`, f])
  );

  return dedupeFlags(
    scanned.map((sf) => {
      const rev = reviewByKey.get(`${sf.sectionId}::${sf.kind}::${sf.tag}`);
      if (!rev) return sf;
      return {
        ...sf,
        owner: rev.owner ?? sf.owner,
        finalized: rev.finalized,
        kbSearched: rev.kbSearched,
        highlightText: rev.highlightText ?? sf.highlightText,
      };
    })
  );
}

/**
 * Full submission-readiness scan: bracket tags + compliance gaps (regex only, no AI).
 * Mirrors backend proposal_rfp_compliance scanners for the issues auditors flag.
 */
export function scanSubmissionFlags(
  outline: ProposalOutline,
  options?: SubmissionFlagScanOptions
): ManualFillFlag[] {
  return mergeSubmissionFlags(
    dedupeFlags([
      ...scanManualFillFlags(outline),
      ...scanReferenceContactFlags(outline),
      ...scanNjReferenceFlags(outline, options),
      ...scanInsuranceFlags(outline, options),
      ...scanQuestionnaireFlags(outline, options),
      ...scanUncoveredRequirementFlags(outline, options),
      ...scanBudgetHoursFlags(outline, options),
      ...scanBudgetRevenueFlags(outline, options?.budget),
      ...scanPricingFlagManuscript(outline, options?.budget),
      ...scanPmRatioFlags(outline, options?.budget),
      ...scanWorkforceConsistencyFlags(outline),
      ...scanPsaAckFlags(outline, options),
    ]),
    null
  );
}

/** Locate the exact character range to highlight when jumping to a flag. */
export function resolveFlagHighlight(
  flag: ManualFillFlag,
  sectionContent: string
): FlagHighlightRange | null {
  const content = sectionContent ?? "";
  if (!content.trim()) return null;

  const toRange = (text: string): FlagHighlightRange | null => {
    const idx = content.indexOf(text);
    if (idx < 0) return null;
    return { start: idx, end: idx + text.length, text };
  };

  if (flag.highlightText?.trim()) {
    const direct = toRange(flag.highlightText);
    if (direct) return direct;
    const trimmed = flag.highlightText.trim();
    if (trimmed !== flag.highlightText) {
      const trimmedRange = toRange(trimmed);
      if (trimmedRange) return trimmedRange;
    }
  }

  if (flag.tag.startsWith("[")) {
    const bracket = flag.tag.match(/\[[^\]]+\]/)?.[0];
    if (bracket) {
      const bracketRange = toRange(bracket);
      if (bracketRange) return bracketRange;
    }
  }

  if (flag.kind === "compliance" && /reference/i.test(flag.tag)) {
    const defer = firstRegexMatch(content, DEFER_RE);
    if (defer) return toRange(defer);
  }

  if (
    flag.kind === "compliance" &&
    /staff hours|billing rates/i.test(flag.tag)
  ) {
    const budgetSummary = lineContaining(content, /##\s*Budget Summary/i);
    if (budgetSummary) return toRange(budgetSummary);
    const hoursHeading = lineContaining(
      content,
      /##\s*(?:Staff|Hours|Billing|Labor|Rate|Compensation|Fee)/i
    );
    if (hoursHeading) return toRange(hoursHeading);
    const firstHeading = content
      .split("\n")
      .find((line) => /^##\s/.test(line.trim()))
      ?.trim();
    if (firstHeading) return toRange(firstHeading);
    return null;
  }

  if (flag.kind === "budget") {
    const zeroLine = lineContaining(content, /\$0|Agency revenue estimate/i);
    if (zeroLine) return toRange(zeroLine);
  }

  if (flag.kind === "consistency") {
    const pct = firstRegexMatch(content, FEMALE_PCT_RE);
    if (pct) return toRange(pct);
  }

  return null;
}

export function countManualFillFlags(outline: ProposalOutline): number {
  return scanManualFillFlags(outline).length;
}

export function manualFillFlagsBySection(
  flags: ManualFillFlag[]
): Map<string, ManualFillFlag[]> {
  const map = new Map<string, ManualFillFlag[]>();
  for (const flag of flags) {
    const list = map.get(flag.sectionId) ?? [];
    list.push(flag);
    map.set(flag.sectionId, list);
  }
  return map;
}

export function sectionManualFillCount(
  sectionId: string,
  flags: ManualFillFlag[]
): number {
  return flags.filter((f) => f.sectionId === sectionId).length;
}

export function summarizeManualFillFlags(flags: ManualFillFlag[]): string {
  if (flags.length === 0) {
    return "No submission gaps found — bracket tags, budget, references, hours, PSA, and consistency checks passed.";
  }
  const manual = flags.filter((f) => f.kind === "manual_fill").length;
  const verify = flags.filter((f) => f.kind === "verify").length;
  const placeholder = flags.filter((f) => f.kind === "placeholder").length;
  const compliance = flags.filter((f) => f.kind === "compliance").length;
  const budget = flags.filter((f) => f.kind === "budget").length;
  const consistency = flags.filter((f) => f.kind === "consistency").length;
  const finalized = flags.filter((f) => f.finalized).length;
  const other =
    flags.length - manual - verify - placeholder - compliance - budget - consistency;
  const parts: string[] = [];
  if (finalized > 0) parts.push(`${finalized} finalized for Sonja/Ella`);
  if (manual > 0) parts.push(`${manual} MANUAL FILL`);
  if (verify > 0) parts.push(`${verify} VERIFY`);
  if (placeholder > 0) parts.push(`${placeholder} PLACEHOLDER`);
  if (compliance > 0) parts.push(`${compliance} compliance`);
  if (budget > 0) parts.push(`${budget} budget`);
  if (consistency > 0) parts.push(`${consistency} consistency`);
  if (other > 0) parts.push(`${other} other`);
  return `${flags.length} submission gap(s): ${parts.join(", ")}. These are detected only — fix in section editor, budget refinery, or with Ella/Sonja data.`;
}
