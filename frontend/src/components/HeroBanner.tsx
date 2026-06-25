import Link from "next/link";
import { AddManualRfpButton } from "./AddManualRfpButton";
import { SyncJustWinButton } from "./SyncJustWinButton";

export function HeroBanner() {
  return (
    <section className="zo-panel-white relative overflow-hidden rounded-2xl p-7 md:p-10 lg:p-12">
      <div className="pointer-events-none absolute -left-16 -top-20 h-56 w-56 rounded-full bg-[#ef5018]/15 md:-left-20" />

      <div className="relative z-10 flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-between">
        <div className="max-w-2xl">
          <span className="zo-tag border-black/20 text-black/80">
            RFP Workspace
          </span>
          <h1 className="font-heading mt-5 text-3xl font-semibold leading-[0.88] text-black md:text-5xl lg:text-6xl">
            Active RFPs
          </h1>
          <p className="mt-5 text-base leading-8 text-black/80 md:text-lg">
            Add RFPs manually, review go/no-go, and track each opportunity from
            intake through submission.
          </p>
        </div>

        <div className="relative z-10 flex shrink-0 flex-col gap-3 sm:flex-row lg:flex-col">
          <SyncJustWinButton
            variant="hero"
            className="w-full sm:w-auto"
          />
          <AddManualRfpButton variant="hero" className="w-full sm:w-auto" />
          <Link
            href="/knowledge-base"
            className="zo-btn secondary !w-full !border-black/25 !text-black sm:!w-auto"
          >
            Knowledge Base →
          </Link>
        </div>
      </div>
    </section>
  );
}
