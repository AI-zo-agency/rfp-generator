/** Cite-linked packet exhibits beside the primary RFP PDF (scoped to one RFP). */

export type RfpPacketFile = {
  id: string;
  rfpId: string;
  citeTarget: string;
  citeQuote?: string;
  filename: string;
  storagePath: string;
  contentSha256?: string;
  uploadedAt: string;
  uploadedBy?: string | null;
};

export type PacketAnalyzeMeta = {
  citeTarget: string;
  action: "upload" | "delete";
  /** True when Fit was already running and the file was NOT saved this click. */
  uploadBlocked?: boolean;
};

export type ApplyPacketExhibitJob = {
  rfpId?: string;
  jobType?: string;
  status?: string;
  error?: string | null;
  startedAt?: string;
  finishedAt?: string | null;
};

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export async function listRfpPacketFiles(
  rfpId: string
): Promise<RfpPacketFile[]> {
  const res = await fetch(
    `/api/rfps/${encodeURIComponent(rfpId)}/packet-files`,
    { cache: "no-store" }
  );
  const data = (await res.json()) as {
    files?: RfpPacketFile[];
    error?: string;
    detail?: string;
  };
  if (!res.ok) {
    throw new Error(data.detail ?? data.error ?? "Failed to list packet files");
  }
  return data.files ?? [];
}

export async function uploadRfpPacketFile(
  rfpId: string,
  args: { citeTarget: string; citeQuote?: string; file: File }
): Promise<{ analyzeStarted: boolean }> {
  const body = new FormData();
  body.append("pdf", args.file);
  body.append("citeTarget", args.citeTarget);
  if (args.citeQuote) body.append("citeQuote", args.citeQuote);

  const res = await fetch(
    `/api/rfps/${encodeURIComponent(rfpId)}/packet-files`,
    { method: "POST", body }
  );
  const data = (await res.json()) as {
    analyzeStarted?: boolean;
    error?: string;
    detail?: string;
  };
  if (!res.ok) {
    throw new Error(data.detail ?? data.error ?? "Upload failed");
  }
  return { analyzeStarted: Boolean(data.analyzeStarted) };
}

export async function deleteRfpPacketFile(
  rfpId: string,
  fileId: string
): Promise<{ analyzeStarted: boolean }> {
  const res = await fetch(
    `/api/rfps/${encodeURIComponent(rfpId)}/packet-files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" }
  );
  const data = (await res.json()) as {
    analyzeStarted?: boolean;
    error?: string;
    detail?: string;
  };
  if (!res.ok) {
    throw new Error(data.detail ?? data.error ?? "Delete failed");
  }
  return { analyzeStarted: Boolean(data.analyzeStarted) };
}

export function packetFileForCite(
  files: RfpPacketFile[],
  citeTarget: string
): RfpPacketFile | undefined {
  return files.find((f) => f.citeTarget === citeTarget);
}

/** Start surgical section refresh for an exhibit on this RFP only. */
export async function startApplyPacketExhibit(
  rfpId: string,
  citeTarget: string
): Promise<{ started: boolean; alreadyRunning: boolean }> {
  const res = await fetch(
    `/api/rfps/${encodeURIComponent(rfpId)}/proposal/apply-packet-exhibit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ citeTarget }),
    }
  );
  const data = (await res.json()) as {
    started?: boolean;
    alreadyRunning?: boolean;
    error?: string;
    detail?: string;
  };
  if (!res.ok && res.status !== 202) {
    throw new Error(
      data.detail ?? data.error ?? "Failed to refresh proposal sections"
    );
  }
  return {
    started: Boolean(data.started),
    alreadyRunning: Boolean(data.alreadyRunning),
  };
}

export async function getApplyPacketExhibitStatus(
  rfpId: string
): Promise<ApplyPacketExhibitJob | null> {
  const res = await fetch(
    `/api/rfps/${encodeURIComponent(rfpId)}/proposal/apply-packet-exhibit/status`,
    { cache: "no-store" }
  );
  const data = (await res.json()) as {
    job?: ApplyPacketExhibitJob | null;
    error?: string;
  };
  if (!res.ok) {
    throw new Error(data.error ?? "Failed to check section refresh status");
  }
  return data.job ?? null;
}

/** Poll until apply job finishes (or no job / idle). */
export async function waitForApplyPacketExhibit(
  rfpId: string,
  options?: { maxWaitMs?: number; pollMs?: number }
): Promise<ApplyPacketExhibitJob | null> {
  const maxWaitMs = options?.maxWaitMs ?? 10 * 60_000;
  const pollMs = options?.pollMs ?? 2_500;
  const started = Date.now();
  let last: ApplyPacketExhibitJob | null = null;
  while (Date.now() - started < maxWaitMs) {
    last = await getApplyPacketExhibitStatus(rfpId);
    if (!last) return null;
    if (last.status === "completed" || last.status === "failed" || last.status === "cancelled") {
      return last;
    }
    await sleep(pollMs);
  }
  return last;
}

/**
 * After Fit finishes for an uploaded cite: if this RFP already has a draft,
 * rewrite only the sections that depend on that exhibit (never other RFPs).
 */
export async function maybeApplyPacketExhibitAfterFit(
  rfpId: string,
  citeTarget: string
): Promise<"skipped_no_draft" | "started" | "already_running" | "failed"> {
  const target = citeTarget.trim();
  if (!target) return "skipped_no_draft";
  try {
    const propRes = await fetch(
      `/api/rfps/${encodeURIComponent(rfpId)}/proposal`,
      { cache: "no-store" }
    );
    if (!propRes.ok) return "skipped_no_draft";
    const prop = (await propRes.json()) as {
      draft?: { sections?: Array<{ content?: string }> } | null;
    };
    const hasContent = Boolean(
      prop.draft?.sections?.some((s) => Boolean(s.content?.trim()))
    );
    if (!hasContent) return "skipped_no_draft";
    const { started, alreadyRunning } = await startApplyPacketExhibit(
      rfpId,
      target
    );
    if (alreadyRunning) return "already_running";
    if (started) {
      await waitForApplyPacketExhibit(rfpId);
      return "started";
    }
    return "failed";
  } catch {
    return "failed";
  }
}
