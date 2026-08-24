"use client";

import { useEffect, useState } from "react";
import {
  getProposalJobStatus,
  listActiveProposalJobs,
  type ActiveProposalJob,
} from "@/lib/proposal-api";

const POLL_MS = 4000;

/**
 * Shown only while THIS RFP's own job is genuinely queued (dispatched, but
 * no free worker slot yet) — not while it's actively running. Explains why
 * nothing appears to be happening instead of leaving "Generating…" looking
 * frozen with no reason given.
 */
export function QueuedJobBanner({ rfpId }: { rfpId: string }) {
  const [queued, setQueued] = useState(false);
  const [others, setOthers] = useState<ActiveProposalJob[]>([]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      const job = await getProposalJobStatus(rfpId);
      if (cancelled) return;
      const isQueued = job?.status === "queued";
      setQueued(isQueued);
      if (isQueued) {
        const active = await listActiveProposalJobs();
        if (!cancelled) {
          setOthers(active.filter((j) => j.rfpId !== rfpId && j.status === "running"));
        }
      }
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [rfpId]);

  if (!queued) return null;

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 md:px-4">
      <span className="font-semibold">Waiting for a worker slot.</span>{" "}
      {others.length > 0 ? (
        <>
          Currently running:{" "}
          {others.map((j) => j.title).join(", ")}. This will start
          automatically as soon as one finishes.
        </>
      ) : (
        <>It will start automatically in a moment.</>
      )}
    </div>
  );
}
