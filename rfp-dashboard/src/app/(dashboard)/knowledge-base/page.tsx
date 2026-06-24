import { KnowledgeBaseGrid } from "@/components/KnowledgeBaseGrid";

export default function KnowledgeBasePage() {
  return (
    <div className="space-y-10">
      <header className="border-l-[5px] border-zo-black pl-8">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-zo-teal">
          Step 1 · RFP Process
        </p>
        <h1 className="font-heading mt-3 text-3xl font-bold text-foreground md:text-4xl">
          Knowledge Base
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-zo-text-secondary">
          Upload verified agency documents directly to Supermemory — facts, case
          studies, bios, pricing, and won proposals. Nothing is stored locally.
        </p>
      </header>
      <KnowledgeBaseGrid />
    </div>
  );
}
