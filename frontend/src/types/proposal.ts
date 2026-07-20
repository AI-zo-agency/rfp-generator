import type { ProposalPipelineCheckpoint } from "@/lib/proposal-pipeline-checkpoint";

export type { ProposalPipelineCheckpoint };

export type OutlineSectionStatus =
  | "empty"
  | "outline"
  | "generated"
  | "reviewed";

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
}

export interface ProposalOutline {
  sections: OutlineSection[];
  updatedAt: string;
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

export interface FeeJustificationMemo {
  markdown: string;
  pricingPosture: string;
  targetVsCap: string;
  roleHoursSummary: string[];
  internalNotes: string[];
  generatedAt: string;
  provider?: string | null;
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

export interface ProposalResearch {
  rfpId: string;
  rfpSections: RfpSectionMap[];
  evidenceCorpus: EvidenceItem[];
  retrievalRounds: number;
  coverageThreshold: number;
  budget?: ProposalBudget | null;
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

export interface VerifiedRate {
  personName: string;
  role: string;
  hourlyRate?: number | null;
  source: string;
}

export interface PricingTier {
  id: string;
  name: string;
  total?: number | null;
  lineItemIds: string[];
  rationale: string;
}

export interface ProposalBudget {
  rfpId: string;
  rfpBudgetCap?: number | null;
  rfpBudgetNotes: string;
  feeStructure: string;
  pricingTier?: string | null;
  budgetFormat?: string | null;
  lineItems: BudgetLineItem[];
  tiers: PricingTier[];
  recommendedTierId?: string | null;
  agencyRevenueEstimate?: number | null;
  lineItemSum?: number | null;
  agencyFeeSubtotal?: number | null;
  clientMediaPassthrough?: number | null;
  totalClientInvoicing?: number | null;
  commissionRate?: number | null;
  lumpSumTotal?: number | null;
  directExpensesTotal?: number | null;
  commissionModel?: string | null;
  pricingFlags: string[];
  qualifyingLanguage: string;
  scopeAdjustments: string[];
  scopeSummary: string;
  designBrief: string;
  optionTermNotes: string;
  mediaSpendNotes: string;
  verifiedRates: VerifiedRate[];
  kbSources: string[];
  kbBucketsUsed: string[];
  confidence: number;
  feeJustificationMemo?: FeeJustificationMemo | null;
  updatedAt: string;
  provider?: string | null;
}
