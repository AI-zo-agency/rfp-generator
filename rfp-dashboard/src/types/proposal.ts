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

export interface ProposalResearch {
  rfpId: string;
  rfpSections: RfpSectionMap[];
  evidenceCorpus: EvidenceItem[];
  retrievalRounds: number;
  coverageThreshold: number;
  updatedAt: string;
  provider?: string | null;
  sectionQueries?: Record<string, string[]>;
}
