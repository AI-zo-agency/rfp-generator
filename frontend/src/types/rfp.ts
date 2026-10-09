export type RfpStage =
  | "intake"
  | "go_no_go"
  | "compliance"
  | "sections_1_3"
  | "sections_4_5"
  | "pricing"
  | "review"
  | "export"
  | "submitted"
  | "won"
  | "lost"
  | "passed";

export type RfpStatus =
  | "new"
  | "active"
  | "pending_approval"
  | "in_progress"
  | "review"
  | "submitted"
  | "won"
  | "lost"
  | "passed";

export type GoNoGoRecommendation = "go" | "no_go" | "review" | null;

export interface GoNoGoFlag {
  category: string;
  severity: "info" | "warning" | "critical";
  message: string;
}

export interface GoNoGoDimension {
  summary: string;
  scoreImpact: string;
  flags: GoNoGoFlag[];
}

export interface GoNoGoEvaluation {
  id: string;
  question: string;
  answer: string;
  impact: string;
}

export interface GoNoGoDecisionMatrixRow {
  dimension: string;
  score: number;
  notes: string;
}

export interface RfpBuyerDemand {
  action: string;
  contact?: string;
  whyEssential?: string;
  quote?: string;
}

export interface RfpPacketRead {
  documentKind:
    | "complete_rfp"
    | "solicitation_notice"
    | "incomplete_packet"
    | "rfi"
    | "rfq"
    | "other";
  blocksScoring: boolean;
  headline: string;
  whatItIs: string;
  missing?: string[];
  nextStep?: string;
  deadlineNote?: string;
  buyerDemands?: RfpBuyerDemand[];
}

export interface GoNoGoDeadlineInfo {
  today: string;
  dueDate: string | null;
  daysRemaining: number | null;
  isPast: boolean;
  isToday: boolean;
  lateSubmissionDisqualifies: boolean;
  note: string;
}

export interface GoNoGoAnalysis {
  fitScore: number | null;
  worthScore: number | null;
  recommendation: "go" | "no_go" | "review" | null;
  insufficientData?: boolean;
  summary: string;
  scopeMatch: GoNoGoDimension;
  sectorMatch: GoNoGoDimension;
  compliance: GoNoGoDimension;
  teamMatch: GoNoGoDimension;
  evaluations?: GoNoGoEvaluation[];
  criticalGaps: string[];
  conditions: string[];
  clarifyingQuestions?: string[];
  stageOneReport?: string;
  decisionMatrix?: GoNoGoDecisionMatrixRow[];
  deadline?: GoNoGoDeadlineInfo | null;
  actionFlags?: string[];
  packetRead?: RfpPacketRead | null;
  provider?: string;
  availableTracks?: string[];
  capabilityMatrix?: Array<{
    requirement: string;
    status?: string;
    track?: string;
    isCore?: boolean;
  }>;
}

export type RfpPriority = "critical" | "high" | "medium" | "low";

export interface RfpRecord {
  id: string;
  title: string;
  client: string;
  source: "justwin" | "manual";
  externalId?: string;
  sector: string;
  location: string;
  dueDate: string;
  receivedDate: string;
  stage: RfpStage;
  status: RfpStatus;
  priority: RfpPriority;
  fitScore: number | null;
  worthScore: number | null;
  goNoGo: GoNoGoRecommendation;
  assignedTo: string | null;
  estimatedValue: number | null;
  pageLimit?: number;
  lastActivity: string;
  lastActivityNote: string;
  contractRole: "prime" | "subconsultant";
  pdfUrl?: string;
  pdfPath?: string;
  description?: string;
  justwinTab?: "hot" | "warm" | "review";
  justwinDetailUrl?: string;
  syncedAt?: string;
  goNoGoAnalysis?: GoNoGoAnalysis | null;
  googleDocUrl?: string;
  selectedTracks?: string[];
  bidScopeLockedAt?: string | null;
}

export interface ActivityItem {
  id: string;
  rfpId: string;
  rfpTitle: string;
  action: string;
  actor: string;
  timestamp: string;
}

export interface CurrentProposalItem {
  rfpId: string;
  rfpTitle: string;
  client: string;
  updatedAt: string;
  filledCount: number;
  sectionCount: number;
  stage: RfpStage;
  lastActivityNote: string;
}

export interface DashboardStats {
  activeRfps: number;
  pendingGoNoGo: number;
  inProgress: number;
  dueThisWeek: number;
  submittedThisMonth: number;
  winRate: number;
  pipelineValue: number;
  avgFitScore: number;
}

export interface TeamMember {
  name: string;
  role: string;
  activeCount: number;
  capacity: number;
}
