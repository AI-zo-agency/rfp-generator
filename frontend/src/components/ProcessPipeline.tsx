"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { smoothTransition } from "@/lib/motion";
import { RFP_PROCESS_STEPS, type ProcessStep } from "@/lib/rfp-process";
import type { RfpRecord } from "@/types/rfp";

interface ProcessPipelineProps {
  rfps?: RfpRecord[];
}

function getStepCounts(rfps: RfpRecord[]) {
  const counts: Record<number, number> = {};
  const stageToStep: Record<string, number> = {
    intake: 2,
    go_no_go: 3,
    compliance: 4,
    sections_1_3: 5,
    sections_4_5: 6,
    pricing: 7,
    review: 8,
    export: 9,
  };

  for (const rfp of rfps) {
    const step = stageToStep[rfp.stage];
    if (step) counts[step] = (counts[step] ?? 0) + 1;
  }

  if (rfps.length > 0) counts[1] = Math.max(counts[1] ?? 0, 1);

  return counts;
}

function StepCard({
  step,
  count,
  className = "",
}: {
  step: ProcessStep;
  count: number;
  className?: string;
}) {
  const hasActivity = count > 0;
  const isKnowledgeBase = step.step === 1;

  const inner = (
    <article
      className={`zo-card flex h-full flex-col p-5 transition-smooth hover:border-zo-orange/40 ${className}`}
    >
      <div className="flex items-center justify-between">
        <span
          className={`flex h-9 w-9 items-center justify-center rounded-xl text-sm font-bold ${
            hasActivity
              ? "bg-[#ef5018] text-white shadow-[0_6px_20px_rgba(239,80,24,0.25)]"
              : "bg-[var(--zo-surface)] text-zo-text-muted"
          }`}
        >
          {step.step}
        </span>
        {count > 0 && (
          <span className="zo-tag border-transparent bg-[#ef5018]/20 text-[#ef5018]">
            {count}
          </span>
        )}
      </div>

      <h3 className="font-heading mt-4 text-[15px] leading-snug text-foreground">
        {step.title}
      </h3>
      <p className="mt-2 flex-1 text-xs leading-relaxed text-zo-text-muted">
        {step.description}
      </p>

      {isKnowledgeBase && (
        <span className="mt-4 text-xs font-semibold text-zo-orange">
          View library →
        </span>
      )}
    </article>
  );

  if (isKnowledgeBase) {
    return (
      <Link href="/knowledge-base" className="block h-full">
        {inner}
      </Link>
    );
  }

  return inner;
}

function PipelineCarousel({
  stepCounts,
}: {
  stepCounts: Record<number, number>;
}) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  const advance = useCallback(() => {
    setIndex((i) => (i + 1) % RFP_PROCESS_STEPS.length);
  }, []);

  useEffect(() => {
    if (paused) return;
    const timer = setInterval(advance, 4000);
    return () => clearInterval(timer);
  }, [paused, advance]);

  const step = RFP_PROCESS_STEPS[index];
  const count = stepCounts[step.step] ?? 0;

  return (
    <div
      className="md:hidden"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onTouchStart={() => setPaused(true)}
      onTouchEnd={() => setPaused(false)}
    >
      <div className="relative min-h-[200px] overflow-hidden">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={step.step}
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -24 }}
            transition={smoothTransition}
          >
            <StepCard step={step} count={count} />
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="mt-5 flex items-center justify-center gap-2">
        {RFP_PROCESS_STEPS.map((s, i) => (
          <button
            key={s.step}
            type="button"
            onClick={() => setIndex(i)}
            aria-label={`Stage ${s.step}: ${s.title}`}
            className={`h-2 rounded-full transition-all duration-300 ${
              i === index
                ? "w-6 bg-zo-teal"
                : "w-2 bg-zo-warm-gray hover:bg-zo-teal/40"
            }`}
          />
        ))}
      </div>

      <p className="mt-3 text-center text-xs text-zo-text-muted">
        Stage {step.step} of {RFP_PROCESS_STEPS.length} · auto-advances
      </p>
    </div>
  );
}

function PipelineGrid({ stepCounts }: { stepCounts: Record<number, number> }) {
  return (
    <div className="hidden gap-4 md:grid md:grid-cols-2 lg:grid-cols-3">
      {RFP_PROCESS_STEPS.map((step, i) => (
        <motion.div
          key={step.step}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...smoothTransition, delay: i * 0.04 }}
          className="h-full"
        >
          <StepCard
            step={step}
            count={stepCounts[step.step] ?? 0}
            className="h-full"
          />
        </motion.div>
      ))}
    </div>
  );
}

export function ProcessPipeline({ rfps = [] }: ProcessPipelineProps) {
  const stepCounts = getStepCounts(rfps);

  return (
    <section className="space-y-6">
      <div>
        <h2 className="font-heading text-xl font-bold text-foreground">
          RFP Process
        </h2>
        <p className="mt-1 text-sm text-zo-text-muted">
          Nine stages from knowledge base to export
        </p>
      </div>

      <PipelineGrid stepCounts={stepCounts} />
      <PipelineCarousel stepCounts={stepCounts} />
    </section>
  );
}
