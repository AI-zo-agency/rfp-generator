"use client";

import Link from "next/link";
import { FadeInItem, FadeInStagger } from "./ui/FadeIn";
import { knowledgeCategories, kbStats } from "@/lib/knowledge-base";

export function KnowledgeBasePreview() {
  const preview = knowledgeCategories.slice(0, 4);

  return (
    <section className="zo-card overflow-hidden">
      <div className="flex flex-col gap-4 border-b border-zo-border px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6 sm:py-6 lg:px-8">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.34em] text-[#ef5018]">
            Step 1 · RFP Process
          </p>
          <h2 className="font-heading mt-1 text-xl font-bold text-foreground">
            Knowledge Base
          </h2>
          <p className="mt-1 text-sm text-zo-text-muted">
            {kbStats.totalFiles} documents across {knowledgeCategories.length}{" "}
            categories
          </p>
        </div>
        <Link href="/knowledge-base" className="zo-btn !w-full !text-xs sm:!w-auto">
          Open Knowledge Base
        </Link>
      </div>

      <FadeInStagger className="grid gap-px bg-zo-border sm:grid-cols-2 lg:grid-cols-4">
        {preview.map((cat) => (
          <FadeInItem key={cat.prefix}>
            <Link
              href="/knowledge-base"
              className="zo-surface-panel block h-full p-5 transition-all duration-300 hover:bg-[var(--zo-hover-bg)] hover:pl-6 sm:p-6"
            >
              <span className="font-mono text-sm font-bold text-[#ef5018]">
                {cat.prefix}
              </span>
              <p className="mt-2 font-semibold text-foreground">{cat.title}</p>
              <p className="mt-1 text-xs text-zo-text-muted">
                {cat.fileCount} files
              </p>
            </Link>
          </FadeInItem>
        ))}
      </FadeInStagger>
    </section>
  );
}
