import { describe, expect, it } from "vitest";
import { describeSyncResult } from "./justwin-sync-message";

const counts = (over: Partial<Parameters<typeof describeSyncResult>[0]> = {}) => ({
  rfpsFound: 5,
  rfpsCreated: 3,
  rfpsSkipped: 0,
  pdfsDownloaded: 3,
  ...over,
});

describe("describeSyncResult", () => {
  const ask = { tab: "hot", scope: "for 2026-10-30" };

  it("reports nothing found", () => {
    expect(describeSyncResult(counts({ rfpsFound: 0 }), ask)).toBe(
      "No hot leads found for 2026-10-30."
    );
  });

  it("reports everything already in ZO", () => {
    expect(
      describeSyncResult(counts({ rfpsCreated: 0, rfpsSkipped: 5, pdfsDownloaded: 0 }), ask)
    ).toBe("No new RFPs for 2026-10-30 — 5 already in ZO were skipped.");
  });

  it("mentions backfilled PDFs when nothing new was added", () => {
    expect(
      describeSyncResult(counts({ rfpsCreated: 0, rfpsSkipped: 5, pdfsDownloaded: 1 }), ask)
    ).toBe(
      "No new RFPs for 2026-10-30 — 5 already in ZO were skipped (1 missing PDF backfilled)."
    );
  });

  it("reports new RFPs alongside skipped ones", () => {
    expect(describeSyncResult(counts({ rfpsSkipped: 2 }), ask)).toBe(
      "Added 3 new RFPs for 2026-10-30 (2 already synced skipped, 3 PDFs downloaded)."
    );
  });

  it("reports a clean sync", () => {
    expect(describeSyncResult(counts({ rfpsCreated: 1, pdfsDownloaded: 1 }), ask)).toBe(
      "Synced 1 hot RFP for 2026-10-30 (1 PDF downloaded)."
    );
  });

  it("drops the tab and date wording for a sync picked back up later", () => {
    expect(describeSyncResult(counts())).toBe("Synced 3 RFPs (3 PDFs downloaded).");
    expect(describeSyncResult(counts({ rfpsFound: 0 }))).toBe("No leads found.");
    expect(
      describeSyncResult(counts({ rfpsCreated: 0, rfpsSkipped: 5, pdfsDownloaded: 0 }))
    ).toBe("No new RFPs — 5 already in ZO were skipped.");
  });
});
