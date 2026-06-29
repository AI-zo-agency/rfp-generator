"use client";

import Link from "next/link";
import { FadeInItem, FadeInStagger } from "./ui/FadeIn";
import { AddManualRfpButton } from "./AddManualRfpButton";
import { SyncJustWinButton } from "./SyncJustWinButton";

export function HeroBanner() {
  return (
    <section className="zo-panel-white relative overflow-hidden rounded-2xl p-6 sm:p-8 md:p-10 lg:p-12">
      <div className="pointer-events-none absolute -left-16 -top-20 h-56 w-56 rounded-full bg-[#ef5018]/15 md:-left-20" />
      <div className="pointer-events-none absolute -bottom-24 -right-16 h-48 w-48 rounded-full bg-[#274742]/10" />

      <div className="relative z-10 flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-between">
        <div className="max-w-2xl">
          <span className="zo-tag border-black/20 text-black/80">
            RFP Workspace
          </span>
          <h1 className="font-heading mt-5 text-3xl font-semibold leading-[0.95] text-black sm:text-4xl md:text-5xl lg:text-6xl">
            Active RFPs
          </h1>
          <p className="mt-4 text-base leading-7 text-black/75 sm:mt-5 sm:leading-8 md:text-lg">
            Add RFPs manually, review go/no-go, and track each opportunity from
            intake through submission.
          </p>
        </div>

        <FadeInStagger className="flex w-full shrink-0 flex-col gap-3 sm:w-auto sm:flex-row lg:flex-col">
          <FadeInItem className="w-full sm:w-auto">
            <SyncJustWinButton variant="hero" className="w-full sm:w-auto" />
          </FadeInItem>
          <FadeInItem className="w-full sm:w-auto">
            <AddManualRfpButton variant="hero" className="w-full sm:w-auto" />
          </FadeInItem>
          <FadeInItem className="w-full sm:w-auto">
            <Link
              href="/knowledge-base"
              className="zo-btn secondary !w-full !border-black/25 !text-black sm:!w-auto"
            >
              Knowledge Base →
            </Link>
          </FadeInItem>
        </FadeInStagger>
      </div>
    </section>
  );
}
