"use client";

import { useMemo, useState } from "react";
import type {
  DashboardStats,
  RfpRecord,
} from "@/types/rfp";
import { HeroBanner } from "./HeroBanner";
import { RecentRfpsTable } from "./RecentRfpsTable";
import { RfpTable } from "./RfpTable";
import { SummaryCards } from "./SummaryCards";
import { KnowledgeBasePreview } from "./KnowledgeBasePreview";
import { OutlineTabs, TabPanel } from "./ui/OutlineTabs";
import { FadeIn } from "./ui/FadeIn";

interface DashboardContentProps {
  rfps: RfpRecord[];
  allRfps: RfpRecord[];
  stats: DashboardStats;
}

const sectionTabs = [
  { id: "recent", label: "Recent" },
  { id: "all", label: "All RFPs" },
];

export function DashboardContent({
  rfps,
  allRfps,
  stats,
}: DashboardContentProps) {
  const [activeTab, setActiveTab] = useState("recent");

  const publicSector = useMemo(
    () => allRfps.filter((r) => r.sector === "Public Sector").length,
    [allRfps]
  );

  const subconsultant = useMemo(
    () => allRfps.filter((r) => r.contractRole === "subconsultant").length,
    [allRfps]
  );

  const tabsWithCounts = sectionTabs.map((tab) => ({
    ...tab,
    count:
      tab.id === "recent"
        ? Math.min(rfps.length, 6)
        : tab.id === "all"
          ? rfps.length
          : undefined,
  }));

  return (
    <div className="space-y-8 sm:space-y-10">
      <FadeIn>
        <HeroBanner />
      </FadeIn>

      <SummaryCards
        stats={stats}
        totalRfps={allRfps.length}
        publicSector={publicSector}
        subconsultant={subconsultant}
      />

      <FadeIn delay={0.08}>
        <KnowledgeBasePreview />
      </FadeIn>

      <FadeIn delay={0.1}>
        <OutlineTabs
          tabs={tabsWithCounts}
          activeTab={activeTab}
          onChange={setActiveTab}
        />
      </FadeIn>

      <TabPanel id="recent" activeTab={activeTab}>
        <FadeIn>
          <RecentRfpsTable rfps={rfps} />
        </FadeIn>
      </TabPanel>

      <TabPanel id="all" activeTab={activeTab}>
        <FadeIn>
          <RfpTable rfps={rfps} />
        </FadeIn>
      </TabPanel>

      <footer className="border-t border-zo-border pt-8 text-center">
        <p className="text-sm text-zo-text-muted">
          zö agency · Bend, Oregon · Women-owned · WBENC & WOSB Certified
        </p>
      </footer>
    </div>
  );
}
