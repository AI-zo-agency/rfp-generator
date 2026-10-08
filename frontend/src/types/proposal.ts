import type { ProposalPipelineCheckpoint } from "@/lib/proposal-pipeline-checkpoint";

export type { ProposalPipelineCheckpoint };

export type OutlineSectionStatus =
  | "empty"
  | "outline"
  | "generated"
  | "reviewed";

export interface CitationGrounding {
  text: string;
  evidenceIds: string[];
  method?: "verbatim" | "overlap" | "inline_provenance";
}

export interface OutlineSection {
  id: string;
  title: string;
  pageLimit?: number;
  wordTarget: number;
  required: boolean;
  custom: boolean;
  content: string;
  status: OutlineSectionStatus;
  source: "template" | "rfp" | "custom" | "generated";
  mode?: "pull" | "select" | "write";
  designerNote?: string;
  kbRefs?: string[];
  /** Post-hoc claim→evidence ids for review citation badges. */
  citationMap?: CitationGrounding[];
}

export interface ProposalDraftSnapshot {
  savedAt: string;
  label: string;
  sections: OutlineSection[];
  /** Present when API returns slim snapshots (sections loaded on demand). */
  sectionCount?: number;
  scanSummary?: Record<string, unknown>;
}

export interface KeyPersona {
  id: string;
  name: string;
  title: string;
  hasResume: boolean;
  sourceFile: string;
  bioSnippet?: string;
  retired?: boolean;
}

export interface ProposalOutline {
  sections: OutlineSection[];
  updatedAt: string;
  googleDocUrl?: string | null;
  googleDocId?: string | null;
  googleDocExportedAt?: string | null;
  snapshots?: ProposalDraftSnapshot[];
  lastFulfillReport?: Record<string, unknown>;
  selectedKeyPersonas?: string[];
}

export interface ProposalDraftMeta {
  rfpId: string;
  generatedAt: string | null;
  totalWords: number;
  totalPages: number;
}

export interface RfpSectionMap {
  id: string;
  title: string;
  pageLimit?: number | null;
  requirements?: string[];
  retrievalFocus?: string[];
  zoMode?: "pull" | "select" | "write";
  evaluationWeight?: number | null;
  coveragePercent?: number | null;
  uncoveredRequirements?: string[];
}

export interface EvidenceItem {
  id: string;
  source: string;
  excerpt: string;
  sectionIds?: string[];
  chunkKey?: string;
}

export interface LossLesson {
  pattern: string;
  avoid: string;
  reason?: string;
  source?: string;
  relevance?: string;
}

export interface ProofPoint {
  requirement: string;
  caseStudy: string;
  kbSource?: string;
  narrativeHook?: string;
  relevance?: string;
  sectionIds?: string[];
  evaluationWeight?: number | null;
}

export interface PreSubmitIssue {
  severity: "critical" | "warning" | "info";
  category: string;
  message: string;
  sectionId?: string | null;
  sectionTitle?: string | null;
  excerpt?: string | null;
}

export interface ManualFillFlag {
  sectionId: string;
  sectionTitle: string;
  kind:
    | "verify"
    | "placeholder"
    | "manual_fill"
    | "compliance"
    | "budget"
    | "consistency"
    | "other";
  tag: string;
  highlightText?: string;
  owner?: string | null;
  finalized?: boolean;
  kbSearched?: boolean;
}

export interface ComplianceCheckItem {
  item: string;
  status: "pass" | "fail" | "manual";
  notes: string;
}

export interface PreSubmitReview {
  rfpId: string;
  issues: PreSubmitIssue[];
  complianceChecklist: ComplianceCheckItem[];
  manualFillFlags?: ManualFillFlag[];
  summary: string;
  issuesMarkdown?: string;
  readyToSubmit: boolean;
  scannedAt: string;
  provider?: string | null;
}

export interface SectionAutoFixLog {
  sectionId: string;
  sectionTitle: string;
  iteration: number;
  methods: string[];
  issuesTargeted: number;
}

export interface PreSubmitAutoFixReport {
  iterationsRun: number;
  issuesBefore: number;
  issuesAfter: number;
  sectionsPatched: number;
  sectionsTargeted: number;
  stoppedReason: string;
  sectionLogs: SectionAutoFixLog[];
}

export interface ProposalExecutionPlanSummary {
  validation?: {
    readinessStatus?: "ready" | "blocked" | "partial";
    blockers?: string[];
    warnings?: string[];
    lowConfidenceArtifacts?: string[];
  };
  metadata?: {
    planVersion?: string;
    planConfidence?: number;
  };
}

export type OutlineMode = "zo_template" | "strict_rfp";

export interface ProposalResearch {
  rfpId: string;
  rfpSections: RfpSectionMap[];
  /** zo_template = Zo Sections 1–3 + RFP tabs; strict_rfp = RFP union only. */
  outlineMode?: OutlineMode;
  evidenceCorpus: EvidenceItem[];
  retrievalRounds: number;
  coverageThreshold: number;
  budget?: ProposalBudget | null;
  /** Agency-set budget anchor used when the RFP states no ceiling. */
  targetBudgetUsd?: number | null;
  lossLessons?: LossLesson[];
  writingAvoidances?: string[];
  proofPoints?: ProofPoint[];
  presubmitReview?: PreSubmitReview | null;
  /** Close-out brief after Budget + Review — requirement coverage + next actions. */
  endingReport?: {
    rfpId: string;
    rfpTitle: string;
    rfpClient: string;
    endsWith?: string;
    pipelineOrder?: string[];
    requirementsTotal?: number;
    requirementsCovered?: number;
    requirementsUncovered?: number;
    hasBudget?: boolean;
    budgetTier?: string | null;
    readyToSubmit?: boolean;
    summaryMarkdown?: string;
    nextActions?: string[];
    complianceGaps?: number;
    presubmitIssues?: number;
    draftedSectionsCount?: number;
    rfpMappedSectionsCount?: number;
    totalWords?: number;
    requirementStatuses?: Array<{
      sectionId: string;
      sectionTitle: string;
      requirement: string;
      covered: boolean;
      evaluationWeight?: number | null;
    }>;
    requirementsMethodology?: {
      summary?: string;
      steps?: string[];
      coverageRule?: string;
      sourceNote?: string;
      notIncluded?: string[];
      sectionRollups?: Array<{
        sectionId: string;
        sectionTitle: string;
        requirementsTotal: number;
        requirementsCovered: number;
      }>;
    } | null;
    closingPackage?: {
      detected?: string[];
      added?: string[];
      humanDecisionGaps?: string[];
      submissionChecklistExpected?: string[];
      submissionNarrativesAdded?: string[];
    } | null;
  } | null;
  pipelineCheckpoint?: ProposalPipelineCheckpoint | null;
  proposalExecutionPlan?: ProposalExecutionPlanSummary | null;
  updatedAt: string;
  provider?: string | null;
  sectionQueries?: Record<string, string[]>;
}

export interface BudgetLineItem {
  id: string;
  category: string;
  description: string;
  namedPerson?: string | null;
  roleTitle?: string | null;
  unit: string;
  quantity?: number | null;
  rate?: number | null;
  rateSource?: string;
  extended?: number | null;
  notes?: string | null;
}

export interface ProposalBudget {
  rfpId: string;
  rfpBudgetNotes: string;
  lineItems: BudgetLineItem[];
  agencyRevenueEstimate?: number | null;
  lineItemSum?: number | null;
  agencyFeeSubtotal?: number | null;
  clientMediaPassthrough?: number | null;
  totalClientInvoicing?: number | null;
  commissionRate?: number | null;
  lumpSumTotal?: number | null;
  commissionModel?: string | null;
  pricingFlags: string[];
  updatedAt: string;
  provider?: string | null;
}
