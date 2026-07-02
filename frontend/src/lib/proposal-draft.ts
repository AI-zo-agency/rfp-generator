import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";

export const STATIC_SECTION_IDS = [
  "section-1-company-overview",
  "section-2-team-overview",
  "section-3-our-work",
] as const;

export function staticSections1to3Complete(
  draft: ProposalOutline | null
): boolean {
  if (!draft) return false;
  const byId = new Map(draft.sections.map((section) => [section.id, section]));
  return STATIC_SECTION_IDS.every((id) => Boolean(byId.get(id)?.content?.trim()));
}

const DEFAULT_SECTIONS: (Omit<
  OutlineSection,
  "content" | "status"
>)[] = [
  {
    id: "section-1-company-overview",
    title: "Section 1 — Company Overview",
    pageLimit: 3,
    wordTarget: 900,
    required: true,
    custom: false,
    source: "template",
    mode: "pull",
  },
  {
    id: "section-2-team-overview",
    title: "Section 2 — Team Overview",
    pageLimit: 4,
    wordTarget: 1200,
    required: true,
    custom: false,
    source: "template",
    mode: "select",
  },
  {
    id: "section-3-our-work",
    title: "Section 3 — Our Work (Case Studies)",
    pageLimit: 5,
    wordTarget: 1500,
    required: true,
    custom: false,
    source: "template",
    mode: "select",
  },
  {
    id: "section-4-project-approach",
    title: "Section 4 — Project Approach",
    pageLimit: 8,
    wordTarget: 1800,
    required: true,
    custom: false,
    source: "generated",
    mode: "write",
  },
  {
    id: "section-5-scope-of-work",
    title: "Section 5 — Scope of Work",
    pageLimit: 6,
    wordTarget: 1500,
    required: true,
    custom: false,
    source: "generated",
    mode: "write",
  },
];

function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

export function buildDefaultOutline(rfp: RfpRecord): ProposalOutline {
  const pageBudget = rfp.pageLimit ?? 30;
  const scale = pageBudget / 34;

  const sections: OutlineSection[] = DEFAULT_SECTIONS.map((section) => ({
    ...section,
    pageLimit: section.pageLimit
      ? Math.max(1, Math.round(section.pageLimit * scale))
      : undefined,
    content: "",
    status: "outline" as const,
  }));

  return {
    sections,
    updatedAt: new Date().toISOString(),
  };
}

export function countSectionsWithContent(outline: ProposalOutline): number {
  return outline.sections.filter((s) => s.content?.trim()).length;
}

/** Empty 5-section shell saved over a researched manuscript (autosave wipe). */
export function isLikelyWipedOutline(
  outline: ProposalOutline,
  research: ProposalResearch | null
): boolean {
  if (countSectionsWithContent(outline) > 0) return false;
  const mappedSections = research?.rfpSections?.length ?? 0;
  const evidence = research?.evidenceCorpus?.length ?? 0;
  const hadManuscriptWork = mappedSections > 5 || evidence > 20;
  return hadManuscriptWork && outline.sections.length <= 5;
}

/** Restore section list from Phase 2 research when draft text was cleared. */
export function rebuildOutlineFromResearch(
  rfp: RfpRecord,
  research: ProposalResearch,
  existingDraft?: ProposalOutline | null
): ProposalOutline {
  const defaults = buildDefaultOutline(rfp);
  const existingById = new Map(
    (existingDraft?.sections ?? []).map((section) => [section.id, section])
  );

  const staticSections: OutlineSection[] = STATIC_SECTION_IDS.map((id) => {
    const fromDraft = existingById.get(id);
    const fromDefault = defaults.sections.find((s) => s.id === id);
    const base = fromDraft ?? fromDefault;
    if (!base) {
      throw new Error(`Missing static section ${id}`);
    }
    return { ...base, content: fromDraft?.content ?? "", status: fromDraft?.content ? "generated" : "outline" };
  });

  const staticIds = new Set(STATIC_SECTION_IDS);
  const rfpSections: OutlineSection[] = (research.rfpSections ?? [])
    .filter((mapped) => !staticIds.has(mapped.id as (typeof STATIC_SECTION_IDS)[number]))
    .map((mapped) => {
      const fromDraft = existingById.get(mapped.id);
      const content = fromDraft?.content ?? "";
      return {
        id: mapped.id,
        title: mapped.title,
        pageLimit: mapped.pageLimit ?? undefined,
        wordTarget: mapped.pageLimit
          ? Math.max(300, mapped.pageLimit * 350)
          : fromDraft?.wordTarget ?? 800,
        required: true,
        custom: false,
        content,
        status: content ? "generated" : "outline",
        source: "rfp" as const,
        mode: mapped.zoMode ?? "write",
      };
    });

  const preservedIds = new Set([
    ...staticSections.map((s) => s.id),
    ...rfpSections.map((s) => s.id),
  ]);
  const customSections = (existingDraft?.sections ?? []).filter(
    (section) => !preservedIds.has(section.id)
  );

  return {
    sections: [...staticSections, ...rfpSections, ...customSections],
    updatedAt: new Date().toISOString(),
  };
}

function paragraph(...sentences: string[]): string {
  return sentences.join(" ");
}

function generateSectionContent(
  section: OutlineSection,
  rfp: RfpRecord
): string {
  const client = rfp.client;
  const title = rfp.title;
  const location = rfp.location;
  const sector = rfp.sector;

  switch (section.title) {
    case "Cover Letter & Executive Summary":
      return [
        paragraph(
          `zö agency is pleased to submit this proposal in response to ${client}'s request for ${title}.`,
          `Based in Bend, Oregon, we specialize in public-sector communications, brand strategy, and community engagement for municipalities and agencies across the Pacific Northwest.`,
          `Our team has reviewed the RFP requirements in detail and developed an approach tailored to ${client}'s goals in ${location}.`
        ),
        paragraph(
          `This executive summary outlines our understanding of the project, our recommended approach, and the team that will deliver results for ${client}.`,
          `We bring direct experience in ${sector.toLowerCase()} work, a proven methodology for stakeholder engagement, and a track record of on-time, on-budget delivery.`,
          `We are confident that zö is the right partner to help ${client} achieve measurable outcomes for this initiative.`
        ),
        paragraph(
          `[DESIGNER NOTE: Cover letter on letterhead. Executive summary as standalone page with client logo lockup and project title: "${title}".]`
        ),
      ].join("\n\n");

    case "Company Overview":
      return [
        paragraph(
          `zö agency is an award-winning creative and communications firm headquartered in Bend, Oregon.`,
          `Since our founding, we have partnered with cities, counties, special districts, and higher-education institutions to deliver campaigns that move communities to action.`,
          `Our work spans brand identity, digital communications, public outreach, and integrated marketing for organizations that serve the public good.`
        ),
        paragraph(
          `We are a women-owned small business with deep roots in the Pacific Northwest.`,
          `Our team combines strategic planning, creative execution, and project management under one roof, which means ${client} will work with a single accountable partner from kickoff through final deliverables.`,
          `We do not subcontract core strategy or writing work; when we bring in specialists, they are vetted partners with established relationships on our team.`
        ),
        paragraph(
          `zö's approach is grounded in verified facts, approved case studies, and pricing from our internal knowledge base.`,
          `Every claim in this proposal traces to a documented source. Where information requires confirmation, we have flagged it clearly for ${client}'s review.`,
          `[VERIFY: Insert current employee count and years in business from 01_companyfacts verified.docx]`
        ),
        paragraph(
          `Recent recognition includes Vega Digital Awards and NYX Awards honors for public-sector campaign work.`,
          `We maintain active relationships with municipal clients across Oregon and Idaho, giving us practical insight into procurement processes, FOIA considerations, and the pace of public decision-making.`
        ),
      ].join("\n\n");

    case "Understanding of the Project":
      return [
        paragraph(
          `${client} has issued this RFP to secure a qualified partner for ${title}.`,
          `The project sits within ${client}'s broader communications and outreach priorities in ${location}, and success will be measured by reach, engagement, and alignment with organizational goals.`,
          `We understand that ${sector} audiences expect clarity, accessibility, and authenticity. Messages must work across digital channels, print materials, public meetings, and partner networks.`
        ),
        paragraph(
          `Key challenges we anticipate include coordinating multiple stakeholder groups, maintaining message consistency across channels, and delivering materials on a timeline that supports ${client}'s internal approval cycles.`,
          `The RFP emphasizes quality, relevance, and demonstrated experience. zö's response addresses each evaluation criterion directly, with specific examples and a work plan that maps to ${client}'s stated deliverables.`
        ),
        paragraph(
          `Our read of the scope centers on strategic communications that serve ${client}'s residents and stakeholders, not generic agency output.`,
          `We will ground creative concepts in research, audience insight, and the unique context of ${location}.`,
          `Where the RFP references page limits (${rfp.pageLimit ?? "per RFP"} pages) or formatting requirements, we have structured this proposal to comply and flagged any items requiring ${client} clarification.`
        ),
        paragraph(
          `We have assigned a dedicated project lead and account strategist to this pursuit.`,
          `If awarded, our first action will be a discovery session with ${client}'s project team to validate assumptions, confirm success metrics, and finalize the communication plan before creative development begins.`
        ),
      ].join("\n\n");

    case "Approach & Methodology":
      return [
        paragraph(
          `zö's approach to ${title} follows four phases: Discover, Strategize, Create, and Activate.`,
          `Each phase has defined milestones, client touchpoints, and quality gates so ${client} always knows where the project stands and what decisions are needed.`
        ),
        paragraph(
          `**Phase 1 — Discover (Weeks 1–2):** We begin with stakeholder interviews, document review, and audience analysis.`,
          `This phase produces a creative brief, messaging framework, and channel plan approved by ${client} before design work starts.`,
          `No concepts move forward without your sign-off.`
        ),
        paragraph(
          `**Phase 2 — Strategize (Weeks 2–4):** We develop campaign architecture, key messages, and content themes aligned with ${client}'s brand standards.`,
          `For public-sector work, we map equity and accessibility considerations into every recommendation.`,
          `Spanish-language and plain-language requirements are integrated at the strategy stage, not added later.`
        ),
        paragraph(
          `**Phase 3 — Create (Weeks 4–10):** Our creative team produces campaign assets across agreed channels: digital, social, print, video, and event materials as scoped.`,
          `We present concepts in rounds with structured feedback.`,
          `Revisions are tracked in our project management system so ${client} has full visibility into status and version history.`
        ),
        paragraph(
          `**Phase 4 — Activate (Weeks 10–12+):** We support launch, media coordination, and performance monitoring.`,
          `Monthly reporting covers reach, engagement, and recommendations for optimization.`,
          `At project close, we deliver a final report with assets archived for ${client}'s future use.`
        ),
        paragraph(
          `Throughout, we apply zö's Writing Guide standards: direct voice, client-outcome focus, and zero filler.`,
          `All factual claims pull from verified knowledge-base files.`,
          `This methodology has supported successful outcomes for municipal clients with scopes comparable to ${title}.`
        ),
      ].join("\n\n");

    case "Work Plan & Timeline":
      return [
        paragraph(
          `The following timeline assumes a contract start within two weeks of award and aligns deliverables to ${client}'s due dates and review windows.`,
          `We build in buffer for ${client} feedback at each milestone.`
        ),
        paragraph(
          `**Month 1:** Kickoff, discovery interviews, creative brief approval, messaging framework.`,
          `**Month 2:** Concept development (Round 1), stakeholder review, revised concepts (Round 2).`,
          `**Month 3:** Final creative production, asset delivery, launch preparation.`,
          `**Month 4+:** Campaign activation, reporting, optimization, and closeout documentation.`
        ),
        paragraph(
          `Key client decision points are marked at the end of each phase.`,
          `We recommend a standing biweekly check-in with ${client}'s project lead and monthly status reports to leadership.`,
          `If the contract period extends beyond four months, we will provide an updated Gantt chart at kickoff.`
        ),
        paragraph(
          `[DESIGNER NOTE: Convert timeline to horizontal Gantt or milestone graphic. Use ${client} brand colors if provided.]`
        ),
      ].join("\n\n");

    case "Team Qualifications & Bios":
      return [
        paragraph(
          `zö will assign a core team with public-sector campaign experience for ${title}.`,
          `Roles include Account Strategy, Creative Direction, Project Management, and Content Development.`,
          `Bios below are pulled from approved files in our knowledge base.`
        ),
        paragraph(
          `**Sonja Anderson — Principal / Strategic Lead**`,
          `[INSERT: Full bio from 04_Bio_SonjaAnderson.docx — exact text, no paraphrasing.]`,
          `Sonja will provide executive oversight and strategic positioning for ${client}'s initiative.`
        ),
        paragraph(
          `**Project Manager — TBD at award**`,
          `A dedicated PM will own schedule, budget, and client communication.`,
          `Our PMs average 8+ years managing municipal and agency contracts in the Pacific Northwest.`
        ),
        paragraph(
          `**Creative Director — Curt**`,
          `[INSERT: Full bio from knowledge base.]`,
          `Curt ensures all deliverables meet zö's design standards and ${client}'s brand requirements.`
        ),
        paragraph(
          `**Content Strategist & Writer**`,
          `Our writing team applies zö's voice standards and verifies every claim against approved sources.`,
          `For ${sector} work, we assign writers with direct experience in public communications.`
        ),
      ].join("\n\n");

    case "Relevant Experience & Case Studies":
      return [
        paragraph(
          `The following case studies demonstrate zö's qualifications for ${title}.`,
          `Each example is drawn from our approved case study library (prefix 03_) and reflects confirmed outcomes only.`
        ),
        paragraph(
          `**Case Study 1 — City of Bend, Waterwise Public Outreach**`,
          `zö developed a multi-channel outreach campaign for municipal water conservation.`,
          `Deliverables included brand messaging, digital ads, community toolkit, and Spanish-language materials.`,
          `Results: [INSERT verified metrics from 03_CS file.]`
        ),
        paragraph(
          `**Case Study 2 — Deschutes County Digital Communications**`,
          `Scope included website content strategy, social media management, and crisis communications support.`,
          `The engagement demonstrates our ability to manage complex stakeholder environments similar to ${client}'s.`,
          `Results: [INSERT verified metrics from 03_CS file.]`
        ),
        paragraph(
          `**Case Study 3 — Sector-aligned reference**`,
          `[SELECT: Best-matching 03_CS file for ${sector} / ${location}. Insert exact case study text.]`,
          `This project is the closest parallel to ${title} in scope and audience.`
        ),
        paragraph(
          `**Reference contacts (required in proposal body)**`,
          `Include three references with name, title, organization, phone, and email from KB (06_WON, reference letters).`,
          `If the RFP requires geography-specific references not in KB, use the closest verified public-sector contacts with honest disclosure — never "upon request" or [PLACEHOLDER] rows.`
        ),
      ].join("\n\n");

    case "Community Engagement Strategy":
      return [
        paragraph(
          `Effective outreach for ${client} requires meeting people where they are.`,
          `Our engagement strategy for ${title} combines digital reach with in-person and partner-channel tactics to ensure equitable participation across ${location}.`
        ),
        paragraph(
          `**Audience mapping:** We segment audiences by demographics, language preference, and channel behavior.`,
          `Priority populations identified in the RFP receive tailored tactics and dedicated budget allocation.`
        ),
        paragraph(
          `**Tactics:** Social media, email, paid digital, community partner toolkits, print materials for libraries and community centers, and optional town-hall or listening-session support.`,
          `All materials follow plain-language and accessibility standards (WCAG 2.1 AA where digital).`
        ),
        paragraph(
          `**Measurement:** Engagement KPIs include impressions, click-through, event attendance, survey responses, and qualitative feedback from ${client}'s team.`,
          `We report monthly and recommend optimizations based on data, not assumptions.`
        ),
      ].join("\n\n");

    case "Budget & Pricing Approach":
      return [
        paragraph(
          `zö's pricing for ${title} is structured to align with ${client}'s budget expectations and the scope defined in this RFP.`,
          `All rates follow our approved Pricing Guide (prefix 05_).`,
          `Line items are available in the attached pricing spreadsheet.`
        ),
        paragraph(
          `**Fee structure:** We propose a fixed-fee arrangement for defined deliverables plus an optional hourly pool for out-of-scope requests approved in writing by ${client}.`,
          `This protects ${client} from open-ended billing while preserving flexibility for unforeseen needs.`
        ),
        paragraph(
          `**Not included unless specified:** Media buy costs, printing, postage, venue rental, and third-party vendor fees are passed through at cost with prior approval.`,
          `[PRICING FLAG: Final numbers require Sonja review against 05_PricingGuide_2026.docx before submission.]`
        ),
        paragraph(
          `We are happy to discuss phasing or scope adjustments if ${client} needs to align the project with fiscal-year constraints.`
        ),
      ].join("\n\n");

    case "References & Appendices":
      return [
        paragraph(
          `**Appendix A:** Resumes and full bios (from 04_ Bio files).`,
          `**Appendix B:** Complete case studies (from 03_ files).`,
          `**Appendix C:** Sample work product (subject to ${client}'s confidentiality requirements).`,
          `**Appendix D:** Required forms, certifications, and insurance certificates as listed in the RFP compliance matrix.`
        ),
        paragraph(
          `**References:**`,
          `1. [Municipal client name] — [Contact title] — [Phone/email — VERIFY before submit]`,
          `2. [Municipal client name] — [Contact title] — [Phone/email — VERIFY before submit]`,
          `3. [Municipal client name] — [Contact title] — [Phone/email — VERIFY before submit]`
        ),
        paragraph(
          `[DESIGNER NOTE: Appendices follow main body. Number per RFP instructions. Confirm page count against ${rfp.pageLimit ?? "RFP"} page limit.]`
        ),
      ].join("\n\n");

    default:
      return paragraph(
        `This section addresses ${section.title} as it relates to ${client}'s ${title} initiative.`,
        `Content will be developed in collaboration with ${client}'s project team during the discovery phase.`,
        `zö will ensure all materials meet the evaluation criteria and formatting requirements specified in the RFP.`,
        `Our team brings relevant ${sector.toLowerCase()} experience in ${location} and a proven track record of delivering communications that resonate with diverse audiences.`,
        `We welcome the opportunity to discuss this section in greater detail during an interview or presentation if ${client} includes that step in the selection process.`
      );
  }
}

export function generateProposalContent(
  outline: ProposalOutline,
  rfp: RfpRecord
): ProposalOutline {
  const sections = outline.sections.map((section) => {
    const content = generateSectionContent(section, rfp);
    return {
      ...section,
      content,
      status: "generated" as const,
    };
  });

  return {
    sections,
    updatedAt: new Date().toISOString(),
  };
}

export function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

export function estimatePages(wordCount: number): number {
  return Math.max(1, Math.ceil(wordCount / 300));
}

export function computeDraftStats(outline: ProposalOutline): {
  totalWords: number;
  totalPages: number;
  generatedSections: number;
} {
  const totalWords = outline.sections.reduce(
    (sum, s) => sum + countWords(s.content),
    0
  );
  const generatedSections = outline.sections.filter(
    (s) => s.status === "generated" || s.status === "reviewed"
  ).length;

  return {
    totalWords,
    totalPages: estimatePages(totalWords),
    generatedSections,
  };
}

export function createCustomSection(title: string): OutlineSection {
  return {
    id: `custom-${Date.now()}-${slugify(title)}`,
    title,
    wordTarget: 500,
    required: false,
    custom: true,
    source: "custom",
    content: "",
    status: "outline",
  };
}
