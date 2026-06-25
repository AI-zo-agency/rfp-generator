export interface KnowledgeCategory {
  prefix: string;
  title: string;
  description: string;
  fileCount: number;
  lastUpdated: string;
  status: "synced" | "stale" | "missing";
  examples: string[];
}

export const knowledgeCategories: KnowledgeCategory[] = [
  {
    prefix: "00_",
    title: "Guides & System",
    description: "Workflow rules, filing guide, writing guide, changelog",
    fileCount: 8,
    lastUpdated: "2026-06-10",
    status: "synced",
    examples: ["00_Guide_Filing.docx", "00_Guide_Writing.docx"],
  },
  {
    prefix: "01_",
    title: "Verified Facts",
    description: "Company facts, clients, certifications — only approved source",
    fileCount: 12,
    lastUpdated: "2026-06-08",
    status: "synced",
    examples: ["01_companyfacts verified.docx", "01_ClientList_Approved.md"],
  },
  {
    prefix: "02_",
    title: "Master Template",
    description: "Pre-built proposal sections — pull, don't rewrite",
    fileCount: 1,
    lastUpdated: "2026-05-20",
    status: "synced",
    examples: ["02_MasterTemplate_2026.docx"],
  },
  {
    prefix: "03_",
    title: "Case Studies",
    description: "Confirmed outcomes only — public-approved clients",
    fileCount: 24,
    lastUpdated: "2026-06-05",
    status: "synced",
    examples: ["03_CS_CityofBend_Brand_2024", "03_CS_HamptonLumber_2025"],
  },
  {
    prefix: "04_",
    title: "Team Bios",
    description: "Exact bio text — no paraphrasing",
    fileCount: 18,
    lastUpdated: "2026-06-01",
    status: "synced",
    examples: ["04_Bio_SonjaAnderson.docx", "04_Bio_Rachel.docx"],
  },
  {
    prefix: "05_",
    title: "Pricing",
    description: "Rate structures, floors, minimums from Pricing Guide",
    fileCount: 6,
    lastUpdated: "2026-06-09",
    status: "synced",
    examples: ["05_PricingGuide_2026.docx", "05_RateCard_Internal.xlsx"],
  },
  {
    prefix: "06_",
    title: "Won Proposals",
    description: "Voice and quality benchmark, paired with source RFP",
    fileCount: 31,
    lastUpdated: "2026-06-07",
    status: "synced",
    examples: ["06_WON_CityofBend_Proposal_2025.pdf"],
  },
  {
    prefix: "07_",
    title: "Finalist Proposals",
    description: "Competitive-but-not-winning; competitor analysis",
    fileCount: 14,
    lastUpdated: "2026-05-28",
    status: "synced",
    examples: ["07_FIN_CityofLakeOswego_Proposal_2026.pdf"],
  },
  {
    prefix: "08_",
    title: "Lost + FOIA",
    description: "Lost proposals with competitor winning submission",
    fileCount: 9,
    lastUpdated: "2026-05-15",
    status: "stale",
    examples: ["08_LOST_CityofExample_Proposal_2025.pdf"],
  },
  {
    prefix: "09_",
    title: "Scoring & Debriefs",
    description: "Evaluation rubrics, award notifications",
    fileCount: 22,
    lastUpdated: "2026-06-04",
    status: "synced",
    examples: ["09_SCORE_CityofBend_Waterwise_2024.pdf"],
  },
  {
    prefix: "10_",
    title: "Active RFPs",
    description: "In-progress bids and Claude outputs",
    fileCount: 9,
    lastUpdated: "2026-06-11",
    status: "synced",
    examples: ["10_Active_DeschutesCounty_2026/"],
  },
  {
    prefix: "11_",
    title: "Reference Archive",
    description: "Background docs, sector research, CaseStudyMaster",
    fileCount: 16,
    lastUpdated: "2026-05-30",
    status: "synced",
    examples: ["11_REF_CaseStudyMaster_2025.docx"],
  },
];

export const kbStats = {
  totalFiles: knowledgeCategories.reduce((s, c) => s + c.fileCount, 0),
  synced: knowledgeCategories.filter((c) => c.status === "synced").length,
  stale: knowledgeCategories.filter((c) => c.status === "stale").length,
  lastFullSync: "2026-06-11T08:00:00",
};
