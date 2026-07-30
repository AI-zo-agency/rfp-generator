"use client";

import { FadeIn } from "@/components/ui/FadeIn";

interface FinancialHeaderProps {
  title: string;
  subtitle: string;
}

export function FinancialHeader({ title, subtitle }: FinancialHeaderProps) {
  return (
    <FadeIn>
      <header className="flex flex-col gap-6 sm:gap-8 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="font-heading mt-3 text-3xl leading-tight text-foreground sm:text-4xl md:text-[2.75rem]">
            {title}
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-relaxed text-zo-text-secondary sm:mt-4 md:text-lg">
            {subtitle}
          </p>
        </div>
      </header>
    </FadeIn>
  );
}
